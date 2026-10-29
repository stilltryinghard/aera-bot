"""Cabinet-first customer journey; existing verified payment handlers stay in use."""
import logging
from datetime import UTC, datetime
from html import escape
from io import BytesIO

import qrcode

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InputMediaPhoto, InlineKeyboardButton as B, InlineKeyboardMarkup as M
from sqlalchemy import select

from app.bot.presentation import edit_screen, send_screen
from app.bot.portal import get_user
from app.config import get_settings
from app.core.security import TokenVault
from app.db.models import AffiliateWithdrawal, Plan, User
from app.db.session import sessions
from app.services import affiliates
from app.services.cabinet import entries, personal_link, expired
from app.services.tariffs_v2 import TERMS, get_plan, price

router = Router(name="customer_v2")
log = logging.getLogger(__name__)
INSTRUCTIONS = "https://t.me/aerasetting/12"


class Withdrawal(StatesGroup):
    details = State()


def keyboard(*rows):
    return M(inline_keyboard=[[B(text=label, callback_data=data) for label, data in row] for row in rows])


def home_menu(*, show_trial=False):
    markup = M(inline_keyboard=[
        [B(text="🛒 Выбрать подписку", callback_data="plans")],
        [B(text="🎁 Бесплатно 5 дней", callback_data="trial")],
        [B(text="🤝 Партнёрская программа", callback_data="referral")],
        [B(text="💡 Инструкции", url=INSTRUCTIONS)],
        [B(text="📌 О сервисе", callback_data="v2:about")],
    ])
    if not show_trial:
        markup.inline_keyboard = [row for row in markup.inline_keyboard
                                  if not any(button.callback_data == "trial" for button in row)]
    return markup


async def menu_for_user(db, user, *, records=None):
    """Use the same trial visibility on every customer navigation path."""
    from app.db.models import TrialUsage
    from app.services.trial_pool import fingerprint
    settings = get_settings()
    if records is None:
        records = await entries(db, user)
    usage = await db.get(TrialUsage, fingerprint(user.telegram_id, settings.app_secret))
    hide_trial = (user.is_blocked or not user.is_active
                  or any(kind != "trial" or row.started_at or row.activation_deadline_ms
                         for kind, row in records)
                  or bool(usage and usage.activated_at))
    if not hide_trial and not any(kind == "trial" for kind, _ in records):
        from app.services.trial_attempts import MAX_ISSUES, issued_count
        hide_trial = await issued_count(db, user, settings.app_secret) >= MAX_ISSUES
    return home_menu(show_trial=not hide_trial)


def about_screen():
    body = ("<b>📌 О сервисе AERA</b>\n\n"
            "<b>🚀 Скорость и стабильность</b>\n"
            "<blockquote>Быстрое и устойчивое соединение для видео, общения и привычных приложений. Трафик без ограничений.</blockquote>\n"
            "<b>🔐 Безопасность</b>\n"
            "<blockquote>Защищённое подключение дома, в дороге и в общественных Wi-Fi сетях.</blockquote>\n"
            "<b>🔑 Ваш ключ</b>\n"
            "<blockquote>Ключ — это доступ к вашей подписке. Храните его у себя и не передавайте посторонним.</blockquote>")
    markup = M(inline_keyboard=[
        [B(text="💬 Поддержка", url="https://t.me/aerasuppbot")],
        [B(text="📢 Канал", url="https://t.me/aeraavpn")],
        [B(text="⬅️ Назад", callback_data="profile")],
    ])
    return body, markup


def back():
    return [("👤 Личный кабинет", "profile")]


async def cabinet_device_limits(db, records):
    """Display each subscription's allowance in the same order as its keys."""
    from app.core.trial_policy import device_limit_for_assignment
    limits = []
    for kind, row in records:
        if kind == "family":
            limit = row.device_limit
        elif kind == "trial":
            limit = await device_limit_for_assignment(db, row)
        else:
            plan = await db.get(Plan, row.plan_id)
            limit = plan.device_limit if plan else None
            if kind == "manual" and row.link_encrypted:
                from urllib.parse import urlsplit
                from app.subscription.multinode import panel_state
                vault = TokenVault(get_settings().app_secret)
                state = await panel_state(db, vault)
                identity = urlsplit(vault.reveal(row.link_encrypted)).username
                actual = (state or {}).get("entries", {}).get(identity, {}).get("limit_ip")
                if isinstance(actual, int) and actual >= 0:
                    limit = actual
        limits.append("безлимит" if limit == 0 else str(limit) if limit is not None else "—")
    return ", ".join(limits) if limits else "0"


def renewable_entries(records):
    return [(kind, row) for kind, row in records
            if kind in {"paid", "manual"} and row.link_encrypted and row.expires_at
            and (kind == "manual" or (row.issued_at and row.status in {"ACTIVE", "EXPIRED"}))]


async def renewal_screen(db, user, selection=None):
    records = renewable_entries(await entries(db, user))
    if selection:
        from app.services.cabinet import selected
        chosen = selected(records, selection)
    elif len(records) == 1:
        chosen = records[0]
    else:
        rows = []
        for kind, row in records:
            plan = await db.get(Plan, row.plan_id)
            from app.bot.texts.portal import period, devices
            label = (period(plan, "ru") + " · " + devices(plan, "ru")) if plan else "Подписка"
            rows.append([("🔄 " + label, "v2:renew:" + kind + ":" + row.id)])
        return ("Выберите подписку для продления 👇" if rows else "Нет подписки для продления.",
                keyboard(*rows, back()))
    from app.services.renewals import create_renewal
    from app.bot.portal import order_screen
    kind, row = chosen
    order = await create_renewal(db, user, kind, row.id)
    return await order_screen(db, order, user, user.language_code)


async def cabinet(db, user, page=0):
    settings = get_settings()
    vault = TokenVault(settings.app_secret)
    stats = await affiliates.statistics(db, user.id)
    records = await entries(db, user)
    device_limits = await cabinet_device_limits(db, records)
    name = " ".join(x for x in [user.first_name, user.last_name] if x) or user.username or "AERA"
    body = ("<b>👤 Профиль</b>\n"
            f"<blockquote><code>├ Имя:       {escape(name)}\n"
            f"├ ID:        {user.telegram_id}\n"
            f"├ Баланс:    {stats['available'] / 100:g} ₽\n"
            f"├ Подписок:  {len(records)}\n"
            f"└ Устройств: {device_limits}</code></blockquote>\n"
            '<a href="https://t.me/aeraavpn">🕊 Наш канал 👈</a>')
    markup = await menu_for_user(db, user, records=records)
    if any(button.callback_data == "trial" for row in markup.inline_keyboard for button in row):
        markup.inline_keyboard[0:2] = reversed(markup.inline_keyboard[0:2])
    body += "\n<b>🔗 Ваш ключ 👇</b>"
    if not records:
        body += "\n<blockquote><code>Ключ появится после выбора подписки.</code></blockquote>"
        return body, markup
    from app.subscription.multinode import owned_panel_url
    for index, (kind, row) in enumerate(records, 1):
        url = (await owned_panel_url(db, user, row, vault, settings)
               if kind in {"paid", "manual", "family"} else personal_link((kind, row), user))
        if url:
            body += ("\n" if index == 1 else "\n\n") + "<blockquote><code>" + escape(url) + "</code></blockquote>"
        else:
            body += "\n<blockquote><code>" + ("Подписка завершена." if expired(row) else "Проверяем доступ. Обновите кабинет чуть позже.") + "</code></blockquote>"
    return body, markup


async def trial_screen(db, user):
    """Opening the trial section allocates once through the existing atomic pool."""
    from app.services.trial_pool import claim_link
    from app.core.trial_policy import duration_for_assignment, device_limit_for_assignment
    from app.services.notifications import enqueue
    settings = get_settings()
    records = await entries(db, user)
    if any(kind != "trial" for kind, _ in records):
        return await cabinet(db, user)
    trial, result = await claim_link(db, user, settings.app_secret)
    markup = M(inline_keyboard=[
        [B(text="💡 Инструкции", url=INSTRUCTIONS)],
        [B(text="👤 Личный кабинет", callback_data="profile")],
    ])
    if result == "limit":
        return await cabinet(db, user)
    if result == "pending":
        return "⏳ Обновляем пробный ключ. Откройте этот раздел через несколько секунд.", markup
    if result == "empty":
        for aid in settings.admin_ids:
            from app.services.commerce import CommerceService
            admin = await CommerceService(db, TokenVault(settings.app_secret)).user(aid)
            await enqueue(db, f"trial-empty:{user.id}:{aid}:{datetime.now(UTC):%Y%m%d}",
                          admin.id, "PORTAL",
                          f"🎁 Не хватило пробной ссылки для @{user.username or '-'} "
                          f"(ID {user.telegram_id}). Пополни запас в панели.")
        return "Пробные ключи временно закончились. Попробуйте позже.", markup
    if result == "used" or not trial or trial.status in {"EXPIRED", "RETIRED"} or expired(trial):
        return "Пробная подписка уже использована. Выберите подписку в личном кабинете.", markup
    days = await duration_for_assignment(db, trial) // 86400000
    limit = await device_limit_for_assignment(db, trial)
    device_text = "1 устройство" if limit == 1 else f"{limit} устройства"
    body = (f"<b>⚡ {days} дней AERA бесплатно</b>\n"
            f"<blockquote><code>📱 {device_text}\n📅 {days} дней\n🚀 Безлимитный трафик</code></blockquote>\n"
            "📎 Вставьте ключ в INCY/Happ\n")
    url = personal_link(("trial", trial), user)
    body += "<blockquote><code>" + (escape(url) if url else "Проверяем доступ. Обновите раздел чуть позже.") + "</code></blockquote>"
    if not trial.started_at:
        body += "\nПодключитесь в течение часа."
    return body, markup


def term_screen():
    return ("<b>📅 Выберите срок подписки</b>",
            keyboard(*[[(f"📅 {days} дней · от {price(days, 1):g} ₽", f"v2:devices:{days}:1")] for days in TERMS], back()))


def devices_screen(days, count):
    total = price(days, count)
    rows = []
    for first in range(1, 11, 2):
        rows.append([(f"{'✅ ' if n == count else ''}{n} устр. · {price(days, n)} ₽", f"v2:devices:{days}:{n}")
                     for n in (first, first + 1)])
    rows += [[(f"Оплатить {total} ₽", f"v2:checkout:{days}:{count}")], [("⬅️ Выбрать другой срок", "plans")]]
    return (f"<b>⚙️ Настройка подписки</b>\n\n"
            f"<blockquote>📅 Срок: {days} дней\n📱 Устройства: {count}\n💰 К оплате: {total} ₽</blockquote>\n\n"
            "Выберите количество устройств 👇", keyboard(*rows))


def invitation_url(user):
    return f"https://t.me/{get_settings().bot_username}?start=ref_{user.referral_code}"



def invitation_text(user):
    return ("AERA VPN сервис №1 👑\n"
            "5 Дней бесплатно! Всегда стабильно и 100% приватно!\n\n"
            + invitation_url(user))


def invitation_qr(url):
    code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=4)
    code.add_data(url)
    code.make(fit=True)
    stream = BytesIO()
    code.make_image(fill_color="black", back_color="white").save(stream, format="PNG")
    return stream.getvalue()


async def partner(db, user):
    stats = await affiliates.statistics(db, user.id)
    url = invitation_url(user)
    body = ("<b>🤝 Партнёрская программа</b>\n"
            "<blockquote><code>Пригласи друга и получи 35% с его первой покупки</code></blockquote>\n"
            "<b>📎 Ваша ссылка</b>\n<blockquote><code>" + escape(url) + "</code></blockquote>\n"
            "<b>📊 Статистика</b>\n<blockquote><code>"
            f"Приглашено: {stats['invited']}\nСовершено покупок: {stats['buyers']}\n"
            f"Всего заработано: {stats['earned'] / 100:g} ₽\nДоступно к выводу: {stats['available'] / 100:g} ₽</code></blockquote>\n"
            "<b>📈 Ставка 35%</b>\n"
            "<blockquote><code>Покупка друга 1 000 ₽\nВам 350 ₽</code></blockquote>\n"
            "<b>Вывод от 1 000 ₽ на криптокошелёк</b>")
    markup = M(inline_keyboard=[
        [B(text="💸 Вывести деньги", callback_data="v2:withdraw")],
        [B(text="📨 Пригласить друзей", callback_data="v2:invite")],
        [B(text="📷 Показать QR-код", callback_data="v2:referral_qr")],
        [B(text="⬅️ Назад", callback_data="profile")],
    ])
    return body, markup


@router.message(Command("start", "help"))
async def start(message, state):
    await state.clear()
    async with sessions.begin() as db:
        user = await get_user(db, message.from_user)
        if (message.text or "").split(maxsplit=1)[0].split("@", 1)[0].lower() == "/start":
            from app.services.start_counter import record_start
            await record_start(db, message.from_user.id, get_settings().app_secret)
        if user.is_blocked or not user.is_active:
            await message.answer("Доступ ограничен. Поддержка: @aerasuppbot")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("ref_"):
            from app.services.referrals import register_referral
            await register_referral(db, user, parts[1][4:])
        if len(parts) == 2 and parts[1] == "connect":
            body = "<b>💡 Инструкции подключения AERA</b>\nОткройте инструкции для INCY, Happ и ТВ в нашем канале."
            markup = M(inline_keyboard=[
                [B(text="💡 Инструкции", url=INSTRUCTIONS)],
                [B(text="👤 Личный кабинет", callback_data="profile")],
            ])
            art = "connect"
        else:
            body, markup = await cabinet(db, user)
            art = "profile"
    await send_screen(message, body, reply_markup=markup, art=art)


@router.callback_query(F.data.in_({"menu", "profile", "plans", "referral", "devices", "trial", "trial:get"}) | F.data.startswith("v2:") | F.data.startswith("profile:"))
async def navigate(call, state):
    await call.answer()
    await state.clear()
    settings = get_settings()
    try:
        async with sessions.begin() as db:
            user = await get_user(db, call.from_user)
            if user.is_blocked or not user.is_active:
                raise ValueError("Account unavailable")
            data = call.data
            art = "profile"
            if data in {"menu", "profile"} or data.startswith("profile:"):
                if data.startswith("profile:"):
                    from app.services.cabinet import selected
                    selected(await entries(db, user), data[len("profile:"):])
                body, markup = await cabinet(db, user)
            elif data == "v2:renew" or data.startswith("v2:renew:"):
                selection = data[len("v2:renew:"):] if data.startswith("v2:renew:") else None
                body, markup = await renewal_screen(db, user, selection)
                art = "payments"
            elif data.startswith("v2:cabinet:"):
                body, markup = await cabinet(db, user, int(data.rsplit(":", 1)[1]))
            elif data in {"trial", "trial:get"}:
                body, markup = await trial_screen(db, user)
                art = "trial"
            elif data in {"plans", "devices"}:
                body, markup = term_screen()
                art = "plans"
            elif data == "v2:about":
                body, markup = about_screen()
                art = "connect"
            elif data == "referral":
                body, markup = await partner(db, user)
                art = "referral"
            elif data == "v2:invite":
                await call.message.answer(
                    "Приглашение — следующим сообщением. Чтобы отправить его нескольким друзьям, "
                    "зажмите сообщение, выберите «Переслать», отметьте нужные чаты и подтвердите отправку.",
                    parse_mode=None,
                )
                await call.message.answer(invitation_text(user), parse_mode=None)
                return
            elif data == "v2:referral_qr":
                image = BufferedInputFile(invitation_qr(invitation_url(user)), filename="aera-invitation-qr.png")
                markup = keyboard([("⬅️ Назад", "referral")])
                caption = "📷 Ваш QR-код партнёрской ссылки."
                if call.message.photo:
                    await call.message.edit_media(InputMediaPhoto(media=image, caption=caption), reply_markup=markup)
                else:
                    await call.message.answer_photo(image, caption=caption, reply_markup=markup)
                return
            elif data == "v2:withdraw":
                stats = await affiliates.statistics(db, user.id)
                if stats["available"] < affiliates.MIN_WITHDRAWAL:
                    body = f"Вывод доступен от 1 000 ₽. Сейчас доступно: {stats['available'] / 100:g} ₽."
                else:
                    body = (f"<b>💸 Вывод на криптокошелёк · {stats['available'] / 100:g} ₽</b>\n"
                            "Отправьте одним сообщением три строки:\n"
                            "<blockquote><code>Монета\nСеть\nАдрес кошелька</code></blockquote>\n"
                            "Заявку обработает оператор. Не отправляйте seed-фразу или приватный ключ.")
                    await state.set_state(Withdrawal.details)
                markup = keyboard([("⬅️ Партнёрская программа", "referral")], back())
                art = "referral"
            else:
                _, action, raw_days, raw_count = data.split(":")
                days, count = int(raw_days), int(raw_count)
                price(days, count)
                if action == "devices":
                    body, markup = devices_screen(days, count)
                    art = "plans"
                elif action == "checkout":
                    from app.services.requests import select_tariff
                    from app.services.commerce import CommerceService
                    from app.services.portal import ensure_order
                    from app.bot.portal import order_screen
                    plan = await get_plan(db, days, count)
                    ticket, plan = await select_tariff(db, CommerceService(db, TokenVault(settings.app_secret)),
                                                     user, plan.id, settings.admin_ids, notify_owner=False)
                    order = await ensure_order(db, ticket, plan)
                    body, markup = await order_screen(db, order, user, user.language_code)
                    art = "payments"
                else:
                    raise ValueError("Unknown action")
        await edit_screen(call.message, body, reply_markup=markup, art=art)
    except (ValueError, TypeError):
        await edit_screen(call.message, "Не удалось открыть этот раздел. Вернитесь в личный кабинет.", reply_markup=home_menu(), art="profile")
    except Exception as error:
        log.exception("customer_v2_failed type=%s", type(error).__name__)
        await call.message.answer("Не удалось выполнить действие. Попробуйте позже или напишите @aerasuppbot.")


@router.message(Withdrawal.details, F.text, ~F.text.startswith("/"))
async def withdrawal_details(message, state):
    async with sessions.begin() as db:
        user = await get_user(db, message.from_user)
        try:
            request = await affiliates.request_withdrawal(db, user, message.text, get_settings())
            body = f"✅ Заявка на вывод {request.amount_minor / 100:g} ₽ на криптокошелёк принята. Перевод выполнит оператор после проверки."
        except ValueError:
            await message.answer("Проверьте баланс и отправьте три строки: монета, сеть, адрес кошелька.",
                                 reply_markup=keyboard([("🤝 Партнёрская программа", "referral")], back()))
            return
    await state.clear()
    await message.answer(body, reply_markup=keyboard([("🤝 Партнёрская программа", "referral")], back()))


@router.message(Command("withdrawals", "withdraw_paid", "withdraw_reject"))
async def withdrawal_admin(message):
    settings = get_settings()
    if message.from_user.id not in settings.admin_ids or message.chat.type != "private":
        return
    parts = (message.text or "").split(maxsplit=2)
    command = parts[0].split("@")[0]
    async with sessions.begin() as db:
        if command == "/withdrawals":
            rows = list(await db.scalars(select(AffiliateWithdrawal).where(
                AffiliateWithdrawal.status.in_(["PENDING", "REVIEW"])).order_by(AffiliateWithdrawal.created_at).limit(10)))
            body = "<b>Заявки на вывод на криптокошелёк</b>"
            vault = TokenVault(settings.app_secret)
            for row in rows:
                user = await db.get(User, row.user_id)
                body += (f"\n\n<code>{row.id}</code>\nID: {user.telegram_id} · {row.amount_minor / 100:g} ₽ · {row.status}\n"
                         + escape(affiliates.withdrawal_details_text(vault.reveal(row.details_encrypted))))
            body += "\n\nПосле фактического перевода: /withdraw_paid ID номер_квитанции\nОтказ: /withdraw_reject ID"
        else:
            try:
                if len(parts) < 2:
                    raise ValueError("Missing ID")
                row = await affiliates.decide_withdrawal(db, parts[1], message.from_user.id,
                        "PAID" if command == "/withdraw_paid" else "REJECTED", parts[2] if len(parts) == 3 else "", settings)
                body = "Статус заявки: " + row.status
            except ValueError:
                body = "Не удалось изменить заявку. Проверьте ID, статус и номер квитанции фактического перевода."
    await message.answer(body, parse_mode="HTML")



async def starts_count(message):
    if message.from_user.id not in get_settings().admin_ids or message.chat.type != "private":
        return
    from app.services.start_counter import start_count
    async with sessions() as db:
        count = await start_count(db)
    await message.answer(str(count))


def statistics_keyboard():
    return M(inline_keyboard=[[B(text="🔄 Обновить", callback_data="owner:stats:refresh")]])


@router.message(Command("stats"))
async def section_statistics(message):
    if message.from_user.id not in get_settings().admin_ids or message.chat.type != "private":
        return
    from app.services.bot_metrics import report
    async with sessions() as db:
        body = await report(db)
    await message.answer(body, parse_mode="HTML", reply_markup=statistics_keyboard())


@router.callback_query(F.data == "owner:stats:refresh")
async def refresh_statistics(callback):
    if (callback.from_user.id not in get_settings().admin_ids
            or not callback.message or callback.message.chat.type != "private"):
        await callback.answer("Недоступно", show_alert=True)
        return
    from app.services.bot_metrics import report
    async with sessions() as db:
        body = await report(db)
    try:
        await callback.message.edit_text(body, parse_mode="HTML", reply_markup=statistics_keyboard())
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
    await callback.answer("Данные обновлены")
