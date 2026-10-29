import asyncio
from datetime import UTC, datetime, timedelta
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import or_, select, update

from app.db.models import (
    Broadcast,
    BroadcastDelivery,
    Notification,
    User,
)


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


async def send_notifications(sessions, bot: Bot, *, keys=None, allowed_types=None) -> None:
    current = datetime.now(UTC)
    async with sessions.begin() as db:
        # Never auto-resend an update whose Telegram outcome is unknown.
        await db.execute(update(Notification).where(
            Notification.status == "SENDING",
            Notification.notification_type.in_({"SUBSCRIPTION_UPDATE", "TRIAL_REMINDER", "TRIAL_EXPIRY", "PAID_EXPIRY"}),
            Notification.updated_at < current - timedelta(minutes=2),
        ).values(status="UNCERTAIN"))
        # Reclaim deliveries interrupted before their result could be recorded.
        await db.execute(
            update(Notification)
            .where(
                Notification.status == "SENDING",
                Notification.notification_type.not_in({"SUBSCRIPTION_UPDATE", "TRIAL_REMINDER", "TRIAL_EXPIRY", "PAID_EXPIRY"}),
                Notification.updated_at < current - timedelta(minutes=2),
            )
            .values(status="PENDING")
        )
        query = select(Notification.id).where(
            Notification.status == "PENDING",
            Notification.updated_at <= current,
            or_(Notification.attempts < 10, Notification.notification_type == "ACCESS_READY"),
        )
        if keys is not None:
            query = query.where(Notification.dedupe_key.in_(keys))
        if allowed_types is not None:
            query = query.where(Notification.notification_type.in_(allowed_types))
        record_ids = list(
            await db.scalars(
                query.order_by(Notification.updated_at, Notification.created_at).limit(20)
            )
        )
    for record_id in record_ids:
        async with sessions.begin() as db:
            claim = await db.execute(
                update(Notification)
                .where(
                    Notification.id == record_id,
                    Notification.status == "PENDING",
                    Notification.updated_at <= current,
                    or_(
                        Notification.attempts < 10, Notification.notification_type == "ACCESS_READY"
                    ),
                )
                .values(status="SENDING", updated_at=datetime.now(UTC))
            )
            if claim.rowcount != 1:
                continue
            record = await db.get(Notification, record_id)
            user = await db.get(User, record.user_id)
            if not user or user.is_blocked:
                record.status = "BLOCKED"
                continue
            is_subscription_update = record.notification_type == "SUBSCRIPTION_UPDATE"
            is_optional_notice = record.notification_type in {"SUBSCRIPTION_UPDATE", "TRIAL_REMINDER", "TRIAL_EXPIRY", "PAID_EXPIRY"}
            recipient, message_text = user.telegram_id, record.text
            markup = None
            parse_mode = None
            if is_subscription_update:
                from app.services.subscription_updates import has_subscription,published,markup as update_markup,message as update_message
                if not await published(db,record.text) or not await has_subscription(db,user):
                    record.status = "CANCELLED"
                    continue
                message_text,parse_mode,markup = await update_message(db),"HTML",update_markup()
            if record.notification_type == "TRIAL_REMINDER":
                from app.config import get_settings
                from app.services import trial_reminders
                if not await trial_reminders.eligible(db, record.text, get_settings(), user_id=user.id):
                    record.status = "CANCELLED"
                    continue
                message_text, markup = trial_reminders.MESSAGE, trial_reminders.markup()
            if record.notification_type == "TRIAL_EXPIRY":
                from app.services import trial_expiry
                if not await trial_expiry.eligible(db, record.text, user_id=user.id):
                    record.status = "CANCELLED"
                    continue
                import json
                message_text = trial_expiry.message(json.loads(record.text)["hours"])
                markup, parse_mode = trial_expiry.markup(), "HTML"
            if record.notification_type == "PAID_EXPIRY":
                from app.config import get_settings
                from app.services import paid_expiry
                if not await paid_expiry.eligible(db, record.text, get_settings(), user_id=user.id):
                    record.status = "CANCELLED"
                    continue
                import json
                message_text = paid_expiry.message(json.loads(record.text)["hours"])
                selection = json.loads(record.text)
                markup, parse_mode = paid_expiry.markup(selection["kind"], selection["id"]), "HTML"
            if record.notification_type == "ACCESS_READY":
                from app.bot.texts.portal import text
                from app.config import get_settings
                from app.core.security import TokenVault
                from app.db.models import PaidLink, Plan

                access = await db.get(PaidLink, record.text)
                if (
                    not user.is_active
                    or not access
                    or access.user_id != user.id
                    or not access.issued_at
                ):
                    record.status = "BLOCKED"
                    continue
                from app.subscription.multinode import owned_panel_url

                settings = get_settings()
                url = await owned_panel_url(
                    db, user, access, TokenVault(settings.app_secret), settings
                )
                if access.status in {"EXPIRED", "RETIRED"}:
                    record.status = "CANCELLED"
                    continue
                if url is None:
                    # Provisioning is retried separately. Never spend Telegram retries
                    # or disclose the single-node stock key while nodes are preparing.
                    record.status = "PENDING"
                    record.updated_at = datetime.now(UTC) + timedelta(seconds=10)
                    continue
                plan = await db.get(Plan, access.plan_id)
                message_text = text(
                    "paid_link",
                    user.language_code,
                    plan=plan.name,
                    url=escape(url),
                )
                from app.services.trial_upgrade import ALIAS
                from app.db.models import AppSetting
                stable = await db.get(AppSetting, ALIAS + access.id)
                if stable and stable.value == user.id:
                    message_text += ("\n\nСсылка и устройство сохранены. Нажмите «Обновить» в INCY/Happ."
                                     if user.language_code == "ru" else
                                     "\n\nYour link and device are unchanged. Refresh the subscription in INCY/Happ.")
                parse_mode = "HTML"
                instruction, cabinet = {
                    "ru": ("💡 Инструкция", "👤 Личный кабинет"),
                    "en": ("💡 Instructions", "👤 My account"),
                    "zh": ("💡 连接指南", "👤 我的资料"),
                    "fa": ("💡 راهنما", "👤 حساب من"),
                }.get(user.language_code, ("💡 Инструкция", "👤 Личный кабинет"))
                markup = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=instruction, url="https://t.me/aerasetting")],
                    [InlineKeyboardButton(text=cabinet, callback_data="profile:paid:" + access.id)],
                ])
        # The claim is committed before network I/O, including on SQLite. An immediate
        # handler delivery and the worker cannot both send an unclaimed notification.
        result, retry_delay = "SENT", 0
        try:
            sent_message = await bot.send_message(recipient, message_text, reply_markup=markup, parse_mode=parse_mode)
        except TelegramForbiddenError:
            result = "BLOCKED"
        except TelegramRetryAfter as error:
            result, retry_delay = "PENDING", max(1, error.retry_after)
        except TelegramBadRequest:
            result = "FAILED"
        except Exception:
            result = "UNCERTAIN" if is_optional_notice else "PENDING"
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
                delay = retry_delay or min(300, 2 ** min(record.attempts, 9))
                record.updated_at = datetime.now(UTC) + timedelta(seconds=delay)
            elif result == "SENT":
                record.sent_at = datetime.now(UTC)
                if is_subscription_update and type(getattr(sent_message, "message_id", None)) is int:
                    from app.services.news_links import remember
                    await remember(db, "delivery:" + record.id, recipient, sent_message.message_id, message_text, markup)
        if retry_delay:
            until = datetime.now(UTC) + timedelta(seconds=retry_delay)
            async with sessions.begin() as db:
                await db.execute(
                    update(Notification)
                    .where(Notification.status == "PENDING", Notification.updated_at < until)
                    .values(updated_at=until)
                )
            break
        await asyncio.sleep(0.06)


async def send_broadcasts(sessions, bot: Bot) -> None:
    current = datetime.now(UTC)
    async with sessions.begin() as db:
        await db.execute(
            update(BroadcastDelivery)
            .where(
                BroadcastDelivery.status == "SENDING",
                BroadcastDelivery.updated_at < current - timedelta(minutes=2),
            )
            .values(status="PENDING")
        )
        identities = list(
            await db.scalars(
                select(BroadcastDelivery.id)
                .where(
                    BroadcastDelivery.status == "PENDING", BroadcastDelivery.updated_at <= current
                )
                .order_by(BroadcastDelivery.updated_at)
                .limit(20)
            )
        )
    for identity in identities:
        async with sessions.begin() as db:
            claim = await db.execute(
                update(BroadcastDelivery)
                .where(
                    BroadcastDelivery.id == identity,
                    BroadcastDelivery.status == "PENDING",
                    BroadcastDelivery.updated_at <= current,
                )
                .values(status="SENDING", updated_at=datetime.now(UTC))
            )
            if claim.rowcount != 1:
                continue
            delivery = await db.get(BroadcastDelivery, identity)
            broadcast = await db.get(Broadcast, delivery.broadcast_id)
            user = await db.get(User, delivery.user_id)
            recipient = user.telegram_id if user and not user.is_blocked else None
            payload = {
                "text": broadcast.text,
                "image": broadcast.image_file_id,
                "button_text": broadcast.button_text,
                "button_url": broadcast.button_url,
            }
        result, delay = "SENT" if recipient else "BLOCKED", 0
        markup = (
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=payload["button_text"], url=payload["button_url"])]
                ]
            )
            if payload["button_text"] and payload["button_url"]
            else None
        )
        try:
            if recipient:
                if payload["image"]:
                    await bot.send_photo(
                        recipient, payload["image"], caption=payload["text"], reply_markup=markup
                    )
                else:
                    await bot.send_message(recipient, payload["text"], reply_markup=markup)
        except TelegramForbiddenError:
            result = "BLOCKED"
        except TelegramBadRequest:
            result = "FAILED"
        except TelegramRetryAfter as error:
            result, delay = "PENDING", max(1, error.retry_after)
        except Exception:
            result, delay = "PENDING", 30
        async with sessions.begin() as db:
            delivery = await db.get(BroadcastDelivery, identity)
            if not delivery or delivery.status != "SENDING":
                continue
            delivery.status = result
            if result == "PENDING":
                delivery.updated_at = datetime.now(UTC) + timedelta(seconds=delay)
            else:
                column = {"SENT": "sent", "BLOCKED": "blocked", "FAILED": "failed"}[result]
                await db.execute(
                    update(Broadcast)
                    .where(Broadcast.id == delivery.broadcast_id)
                    .values({column: getattr(Broadcast, column) + 1})
                )
                if result == "BLOCKED" and recipient:
                    user = await db.get(User, delivery.user_id)
                    user.is_blocked = True
                await db.flush()
                await db.execute(
                    update(Broadcast)
                    .where(
                        Broadcast.id == delivery.broadcast_id,
                        Broadcast.sent + Broadcast.blocked + Broadcast.failed >= Broadcast.total,
                    )
                    .values(status="DONE")
                )
        if delay:
            break
        await asyncio.sleep(0.06)
