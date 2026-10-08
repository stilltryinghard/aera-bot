import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, update

from app.config import get_settings
from app.db.models import (
    Broadcast,
    BroadcastDelivery,
    Notification,
    Subscription,
    SupportTicket,
    User,
)
from app.services.commerce import utc

MAX_ATTEMPTS = 10


async def enqueue(
    db, key: str, user_id: str, kind: str, text: str, subscription_id: str | None = None
) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    await db.execute(
        insert(Notification)
        .values(
            dedupe_key=key,
            user_id=user_id,
            notification_type=kind,
            text=text,
            subscription_id=subscription_id,
        )
        .on_conflict_do_nothing(index_elements=[Notification.dedupe_key])
    )


async def alert_admins(db, settings, key: str, text: str) -> None:
    """Queue an operator alert once per admin; `key` makes repeated calls idempotent."""
    for admin_id in sorted(settings.admin_ids):
        admin = await db.scalar(select(User).where(User.telegram_id == admin_id))
        if admin:
            await enqueue(db, f"{key}:{admin_id}", admin.id, "ALERT", text)


async def schedule_expiry(db) -> None:
    current = datetime.now(UTC)
    subscriptions = await db.scalars(
        select(Subscription).where(
            Subscription.status.in_(["ACTIVE", "EXPIRING", "EXPIRED", "TRIAL"])
        )
    )
    for sub in subscriptions:
        remaining = utc(sub.expires_at) - current
        days = remaining.total_seconds() / 86400
        if 1 < days <= 3:
            kind, text = "3D", "Подписка AERA закончится в течение 3 дней. Продлите её заранее."
        elif 0 < days <= 1:
            kind, text = "1D", "Подписка AERA закончится в течение суток. Продлите её заранее."
        elif -1 < days <= 0:
            kind, text = "EXPIRED", "Подписка AERA закончилась. Продлите её, чтобы подключиться."
        elif -3 < days <= -1:
            kind, text = "AFTER", "Подключение AERA ждёт вас. Продлите подписку для доступа."
        else:
            continue
        await enqueue(
            db,
            f"expiry:{sub.id}:{utc(sub.expires_at).isoformat()}:{kind}",
            sub.user_id,
            kind,
            text,
            sub.id,
        )


async def send_notifications(sessions, bot: Bot, *, keys=None, allowed_types=None) -> None:
    current = datetime.now(UTC)
    async with sessions.begin() as db:
        # Reclaim deliveries interrupted before their result could be recorded.
        await db.execute(
            update(Notification)
            .where(
                Notification.status == "SENDING",
                Notification.updated_at < current - timedelta(minutes=2),
            )
            .values(status="PENDING")
        )
        query = select(Notification.id).where(
            Notification.status == "PENDING", Notification.attempts < MAX_ATTEMPTS
        )
        if keys is not None:
            query = query.where(Notification.dedupe_key.in_(keys))
        if allowed_types is not None:
            query = query.where(Notification.notification_type.in_(allowed_types))
        record_ids = list(await db.scalars(query.order_by(Notification.created_at).limit(20)))
    for record_id in record_ids:
        async with sessions.begin() as db:
            claim = await db.execute(
                update(Notification)
                .where(
                    Notification.id == record_id,
                    Notification.status == "PENDING",
                    Notification.attempts < MAX_ATTEMPTS,
                )
                .values(status="SENDING", updated_at=datetime.now(UTC))
            )
            if claim.rowcount != 1:
                continue
            record = await db.get(Notification, record_id)
            user = await db.get(User, record.user_id)
            if user.is_blocked:
                record.status = "BLOCKED"
                continue
            recipient, message_text = user.telegram_id, record.text
            markup = None
            if record.notification_type == "ACCESS_READY":
                from app.bot.portal import home_menu
                from app.bot.texts.portal import text
                from app.core.security import TokenVault
                from app.db.models import PaidLink, Plan

                access = await db.get(PaidLink, record.text)
                if not access or access.user_id != user.id or not access.issued_at:
                    record.status = "BLOCKED"
                    continue
                plan = await db.get(Plan, access.plan_id)
                message_text = text(
                    "paid_link",
                    user.language_code,
                    plan=plan.name,
                    url=TokenVault(get_settings().app_secret).reveal(access.link_encrypted),
                )
                markup = home_menu(user.language_code)
            if record.notification_type == "TARIFF_REQUEST":
                from app.services.requests import paid_requests

                ticket = await db.get(SupportTicket, record.dedupe_key.split(":")[1])
                if not ticket or not await db.scalar(
                    paid_requests().where(SupportTicket.id == ticket.id)
                ):
                    record.status = "CANCELLED"
                    continue
                applicant = await db.get(User, ticket.user_id)
                message_text = (
                    f"💳 Оплаченная покупка № {ticket.id[:8]}\n"
                    f"Клиент: {applicant.first_name or applicant.telegram_id}\n"
                    "💎 Подписки доступны в «Наших пользователях»."
                )
                contact = (
                    "https://t.me/" + quote(applicant.username, safe="")
                    if applicant.username
                    else f"tg://user?id={applicant.telegram_id}"
                )
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="👤 Пользователь",
                                callback_data=f"ad:user:{applicant.id}",
                            )
                        ],
                        [InlineKeyboardButton(text="Написать пользователю", url=contact)],
                    ]
                )
        # The claim is committed before network I/O, including on SQLite. An immediate
        # handler delivery and the worker cannot both send an unclaimed notification.
        result, retry_delay = "SENT", 0
        try:
            await bot.send_message(recipient, message_text, reply_markup=markup, parse_mode=None)
        except TelegramForbiddenError:
            result = "BLOCKED"
        except TelegramRetryAfter as error:
            result, retry_delay = "PENDING", min(error.retry_after, 30)
        except Exception:
            result = "PENDING"
        async with sessions.begin() as db:
            record = await db.get(Notification, record_id)
            if record is None:
                continue
            record.status = result
            if result == "BLOCKED":
                user = await db.get(User, record.user_id)
                user.is_blocked = True
            elif result == "PENDING":
                record.attempts += 1
                if record.attempts >= MAX_ATTEMPTS:
                    record.status = "FAILED"
                    # Alerts about alerts would never stop if an admin cannot be reached.
                    if record.notification_type != "ALERT":
                        user = await db.get(User, record.user_id)
                        await alert_admins(
                            db,
                            get_settings(),
                            f"undelivered:{record.id}",
                            f"⚠️ Сообщение не доставлено после {MAX_ATTEMPTS} попыток.\n"
                            f"Тип: {record.notification_type}\n"
                            f"Получатель: Telegram ID {user.telegram_id}\n"
                            "Свяжись с ним вручную.",
                        )
            else:
                record.sent_at = datetime.now(UTC)
        if retry_delay:
            await asyncio.sleep(retry_delay)
            break
        await asyncio.sleep(0.06)


async def send_broadcasts(sessions, bot: Bot) -> None:
    # Claim a batch and commit before network I/O, like send_notifications: Telegram calls
    # never run inside a transaction that holds row locks.
    current = datetime.now(UTC)
    async with sessions.begin() as db:
        # Reclaim deliveries interrupted before their result could be recorded.
        await db.execute(
            update(BroadcastDelivery)
            .where(
                BroadcastDelivery.status == "SENDING",
                BroadcastDelivery.updated_at < current - timedelta(minutes=2),
            )
            .values(status="PENDING")
        )
        deliveries = list(
            await db.scalars(
                select(BroadcastDelivery)
                .where(BroadcastDelivery.status == "PENDING")
                .limit(20)
                .with_for_update(skip_locked=True)
            )
        )
        claimed = [delivery.id for delivery in deliveries]
        for delivery in deliveries:
            delivery.status, delivery.updated_at = "SENDING", current
    for index, delivery_id in enumerate(claimed):
        async with sessions() as db:
            delivery = await db.get(BroadcastDelivery, delivery_id)
            broadcast = await db.get(Broadcast, delivery.broadcast_id)
            user = await db.get(User, delivery.user_id)
        markup = None
        if broadcast.button_text and broadcast.button_url:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=broadcast.button_text, url=broadcast.button_url)]
                ]
            )
        result, block_user, retry_delay = "SENT", False, 0
        if user.is_blocked:
            result = "BLOCKED"
        else:
            try:
                if broadcast.image_file_id:
                    await bot.send_photo(
                        user.telegram_id,
                        broadcast.image_file_id,
                        caption=broadcast.text,
                        reply_markup=markup,
                    )
                else:
                    await bot.send_message(user.telegram_id, broadcast.text, reply_markup=markup)
            except TelegramForbiddenError:
                result, block_user = "BLOCKED", True
            except TelegramRetryAfter as error:
                result, retry_delay = "PENDING", min(error.retry_after, 30)
            except Exception:
                result = "FAILED"
        async with sessions.begin() as db:
            if retry_delay:
                # Hand this and every not-yet-sent claim back for a later run.
                await db.execute(
                    update(BroadcastDelivery)
                    .where(
                        BroadcastDelivery.id.in_(claimed[index:]),
                        BroadcastDelivery.status == "SENDING",
                    )
                    .values(status="PENDING")
                )
            else:
                delivery = await db.scalar(
                    select(BroadcastDelivery)
                    .where(BroadcastDelivery.id == delivery_id)
                    .with_for_update()
                )
                if delivery.status == "SENDING":
                    broadcast = await db.scalar(
                        select(Broadcast)
                        .where(Broadcast.id == delivery.broadcast_id)
                        .with_for_update()
                    )
                    delivery.status = result
                    if result == "SENT":
                        broadcast.sent += 1
                    elif result == "FAILED":
                        broadcast.failed += 1
                    else:
                        broadcast.blocked += 1
                    if block_user:
                        await db.execute(
                            update(User).where(User.id == user.id).values(is_blocked=True)
                        )
                    if broadcast.sent + broadcast.failed + broadcast.blocked >= broadcast.total:
                        broadcast.status = "DONE"
        if retry_delay:
            await asyncio.sleep(retry_delay)
            break
        if result != "BLOCKED" or block_user:
            await asyncio.sleep(0.06)
