"""Owner controls for the manual portal, trial stock and paid access."""

from datetime import UTC, datetime
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select

from app.bot.keyboards import keyboard
from app.bot.texts.portal import text as tr
from app.config import get_settings
from app.core.security import TokenVault
from app.db.models import ManualAccess, PaidLink, Plan, SaleOrder, SupportTicket, TrialLink, User
from app.db.session import sessions
from app.services.notifications import enqueue
from app.services.settings import set_setting, setting

router = Router(name="portal_admin")

TRIAL_STATUS = {
    "FREE": "Свободна ✅",
    "WAITING": "Ждёт подключения ⏳",
    "ACTIVE": "Активна 🚀",
    "EXPIRED": "Срок завершён ⌛",
    "UNVERIFIED": "Проверяем 🔎",
    "UNAVAILABLE": "Нужна проверка ⚙️",
    "RETIRED": "Выдана ранее 🗄️",
    "RECYCLING": "Меняем ключ 🔑",
    "ACTIVATING": "Подтверждаем срок ⚙️",
    "NEEDS_RESET": "Обновляем свободный ключ 🔑",
    "BLOCKED": "Выдана, отключена в панели ⚙️",
}


class OwnerInput(StatesGroup):
    link = State()
    stars = State()
    expiry = State()
    stock = State()


@router.message(OwnerInput.stock, F.text, ~F.text.startswith("/"))
async def save_stock(message, state: FSMContext):
    if not authorized(message.from_user):
        return
    data = await state.get_data()
    links = [line.strip() for line in message.text.splitlines() if line.strip()]
    if not links or len(links) > 30:
        await message.answer("🔗 Пришли от 1 до 30 VLESS-ссылок, каждую с новой строки.")
        return
    try:
        async with sessions.begin() as db:
            from app.services.paid_pool import import_paid_links

            plan = await db.get(Plan, data["plan_id"])
            await import_paid_links(db, {plan.slug: links}, TokenVault(get_settings().app_secret))
    except ValueError:
        await message.answer(
            "⚙️ Проверь формат. Ключ не должен принадлежать пробному или другому тарифу."
        )
        return
    await state.clear()
    await message.answer(
        "✅ Ссылки добавлены. Бот проверит срок и лимиты чтением панели, "
        "после этого корректные свободные ключи станут доступны для продажи.",
        reply_markup=keyboard(("🔗 Запас платных ссылок", "ad:portal:stock")),
    )


def authorized(sender):
    return sender.id in get_settings().admin_ids


@router.message(OwnerInput.link, F.text, ~F.text.startswith("/"))
async def save_link(message, state: FSMContext):
    if not authorized(message.from_user):
        return
    raw = (message.text or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"vless", "https"} or not parsed.hostname or len(raw) > 2000:
        await message.answer(
            "🔗 Пришли личную VLESS-ссылку или HTTPS-ссылку подписки. /admin отменяет ввод."
        )
        return
    data = await state.get_data()
    vault = TokenVault(get_settings().app_secret)
    async with sessions.begin() as db:
        access = await db.scalar(
            select(ManualAccess).where(ManualAccess.user_id == data["user_id"])
        )
        if not access:
            await state.clear()
            await message.answer("Сначала отметь клиента подключённым в заявке.")
            return
        access.link_encrypted = vault.cipher.encrypt(raw.encode()).decode()
        user = await db.get(User, data["user_id"])
        await enqueue(
            db,
            f"access-link:{access.id}:{datetime.now(UTC).timestamp()}",
            user.id,
            "PORTAL",
            tr("my_link", user.language_code) + "\n" + raw,
        )
    await state.clear()
    await message.answer(
        "✅ Ссылка сохранена в профиле клиента и поставлена в отправку.",
        reply_markup=keyboard(("👤 Клиент", f"ad:user:{data['user_id']}")),
    )


@router.message(OwnerInput.stars, F.text, ~F.text.startswith("/"))
async def save_stars(message, state: FSMContext):
    if not authorized(message.from_user):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 1000000:
        await message.answer("⭐ Пришли целое число Stars от 1 до 1000000.")
        return
    data = await state.get_data()
    async with sessions.begin() as db:
        plan = await db.get(Plan, data["plan_id"])
        plan.stars_price = int(raw)
    await state.clear()
    await message.answer(
        "✅ Цена обновлена для новых счетов. Выставленные счета сохраняют сумму.",
        reply_markup=keyboard(("⭐ Цены Stars", "ad:portal:pricing")),
    )


@router.message(OwnerInput.expiry, F.text, ~F.text.startswith("/"))
async def save_expiry(message, state: FSMContext):
    if not authorized(message.from_user):
        return
    try:
        date = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M").replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        await message.answer("📅 Формат: 05.11.2026 18:00 (UTC)")
        return
    data = await state.get_data()
    async with sessions.begin() as db:
        access = await db.scalar(
            select(ManualAccess).where(ManualAccess.user_id == data["user_id"])
        )
        if not access:
            await message.answer("Нет платной подписки.")
            return
        access.expires_at = date
    await state.clear()
    await message.answer(
        "✅ Срок в кабинете обновлён. При необходимости согласуй такой же срок в панели вручную.",
        reply_markup=keyboard(("👤 Клиент", f"ad:user:{data['user_id']}")),
    )


@router.callback_query(F.data.startswith("ad:portal:"))
async def action(call, state: FSMContext):
    if not authorized(call.from_user):
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.answer()
    await state.clear()
    parts = call.data.split(":")
    section = parts[2]
    identity = parts[3] if len(parts) > 3 else None
    rows = []
    body = "💎 AERA"
    vault = TokenVault(get_settings().app_secret)
    async with sessions.begin() as db:
        if section == "stock":
            plans = list(
                await db.scalars(
                    select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)
                )
            )
            body = "🔗 ПЛАТНЫЕ ССЫЛКИ\n\nДобавляй отдельные ключи для каждого тарифа и срока. "
            body += (
                "Панель должна иметь безлимитный трафик, нужный IP-лимит "
                "и старт срока после первого использования.\n\n"
            )
            for plan in plans:
                records = list(
                    await db.scalars(select(PaidLink).where(PaidLink.plan_id == plan.id))
                )
                free = sum(r.status == "FREE" for r in records)
                reserved = sum(r.status == "RESERVED" for r in records)
                issued = sum(bool(r.issued_at) for r in records)
                body += (
                    f"📦 {plan.name} · {plan.duration_months} мес.: "
                    f"свободно {free} / всего {len(records)} · резерв {reserved} · выдано {issued}\n"
                )
                rows.append(
                    (
                        f"📦 {plan.name} · {plan.duration_months} мес.",
                        f"ad:portal:stock-plan:{plan.id}",
                    )
                )
        elif section == "stock-plan":
            plan = await db.get(Plan, identity)
            records = list(
                await db.scalars(
                    select(PaidLink)
                    .where(PaidLink.plan_id == identity)
                    .order_by(PaidLink.created_at)
                )
            )
            body = (
                f"🔗 {plan.name} · {plan.duration_months} мес.\n"
                f"Длительность в панели: {plan.duration_days} дней.\n\n"
            )
            body += (
                "\n".join(f"🔑 {r.label} · {r.status}" for r in records[:40]) or "Пока нет ссылок."
            )
            rows = [
                ("➕ Добавить ссылки", f"ad:portal:add-stock:{identity}"),
                ("🔗 Все тарифы", "ad:portal:stock"),
            ]
        elif section == "add-stock":
            plan = await db.get(Plan, identity)
            await state.set_state(OwnerInput.stock)
            await state.update_data(plan_id=identity)
            body = (
                f"🔗 {plan.name} · {plan.duration_months} мес.\n\n"
                "Пришли VLESS-ссылки, каждую с новой строки. /admin отменяет ввод."
            )
            rows = [("🔗 Запас", "ad:portal:stock")]
        elif section == "trials":
            from app.services.trial_metrics import trial_summary

            records = list(await db.scalars(select(TrialLink).order_by(TrialLink.label)))
            counts = {
                key: sum(r.status == key for r in records)
                for key in (
                    "FREE",
                    "WAITING",
                    "ACTIVE",
                    "EXPIRED",
                    "UNVERIFIED",
                    "UNAVAILABLE",
                    "RETIRED",
                    "RECYCLING",
                    "ACTIVATING",
                    "NEEDS_RESET",
                    "BLOCKED",
                )
            }
            body = (
                await trial_summary(db)
                + "\n\n📊 Состояние запаса\n"
                + "\n".join(
                    f"{label}: {counts[key]}"
                    for key, label in [
                        ("FREE", "✅ Свободно"),
                        ("WAITING", "⏳ Выдано, ждут подключения"),
                        ("ACTIVE", "🚀 Активно"),
                        ("EXPIRED", "⌛ Завершено"),
                        ("UNVERIFIED", "🔎 Ещё не проверено"),
                        ("UNAVAILABLE", "⚙️ Недоступно"),
                        ("RETIRED", "🗄️ Выдано ранее, не переиспользуется"),
                        ("RECYCLING", "🔑 Замена ключа, ждёт подтверждения"),
                        ("ACTIVATING", "⚙️ Подтверждение срока после использования"),
                        ("NEEDS_RESET", "🔑 Обновление свободных ключей"),
                        ("BLOCKED", "⚙️ Выдано, отключено в панели"),
                    ]
                )
                + "\n\n⏱ Час без подключения или окончание семи дней: "
                "бот заменит только пробный ключ и вернёт запись в запас. "
                "Проверка состояния раз в 30 секунд."
            )
            rows = [
                (
                    f"🎁 {r.label} · {TRIAL_STATUS.get(r.status, 'Проверяем 🔎')}",
                    f"ad:portal:trial:{r.id}",
                )
                for r in records[:40]
            ]
        elif section == "trial":
            record = await db.get(TrialLink, identity)
            user = await db.get(User, record.user_id) if record.user_id else None
            body = (
                f"🎁 {record.label}\nСтатус: {TRIAL_STATUS.get(record.status, 'Проверяем 🔎')}\n"
                f"Клиент: @{user.username or '-'} (ID {user.telegram_id})"
                if user
                else f"🎁 {record.label}\nСтатус: {TRIAL_STATUS.get(record.status, 'Проверяем 🔎')}"
            )
            if record.status == "UNAVAILABLE":
                body += (
                    "\n\n⚙️ Панель не подтвердила готовность этого ключа. "
                    "Этот статус не означает, что клиент подключился."
                )
            if record.expires_at:
                body += f"\n📅 До: {record.expires_at:%d.%m.%Y %H:%M} UTC"
            body += f"\n\n🔗 {vault.reveal(record.link_encrypted)}"
            rows = [("🎁 Запас ссылок", "ad:portal:trials")]
        elif section == "paid":
            ticket = await db.get(SupportTicket, identity)
            order = await db.scalar(select(SaleOrder).where(SaleOrder.ticket_id == identity))
            if not ticket or not order:
                body = "Заявка без нового счёта. Для оплаты выбери тариф в обновлённом меню."
            else:
                body = (
                    "✅ Оплата подтверждена платёжной системой."
                    if order.paid_at
                    else "⭐ Сейчас работает оплата через Stars. Бот проверит платёж "
                    "и выдаст ссылку автоматически. Ручное подтверждение отключено."
                )
                rows = [("👥 Наши пользователи", "ad:users")]
        elif section in {"link", "expiry"}:
            access = await db.scalar(select(ManualAccess).where(ManualAccess.user_id == identity))
            if access:
                await state.set_state(OwnerInput.link if section == "link" else OwnerInput.expiry)
                await state.update_data(user_id=identity)
                body = (
                    "🔗 Пришли личную ссылку клиента. Она сохранится зашифрованной "
                    "и будет отправлена ему."
                    if section == "link"
                    else "📅 Пришли дату окончания в UTC: ДД.ММ.ГГГГ ЧЧ:ММ. "
                    "Это меняет только кабинет бота."
                )
            else:
                body = "Сначала отметь клиента подключённым."
            rows = [("👤 Клиент", f"ad:user:{identity}")]
        elif section == "pricing":
            enabled = await setting(db, "manual_stars_enabled", "false") == "true"
            plans = list(
                await db.scalars(
                    select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order)
                )
            )
            body = (
                "⭐ ЦЕНЫ TELEGRAM STARS\n\nЦена покупки Stars зависит от магазина и страны. "
                "Начальные цены рассчитаны по вознаграждению Telegram $0.013 за Star "
                "и ориентиру 83.4839 ₽/USD, округлены вверх.\n\n"
                "Нажми тариф, чтобы изменить цену.\n"
                + f"Счета: {'включены' if enabled else 'выключены'}"
            )
            rows = [
                (
                    f"⭐ {p.name} · {p.duration_months} мес. · {p.stars_price or '-'}",
                    f"ad:portal:price:{p.id}",
                )
                for p in plans
            ]
            rows.append(
                ("⏸ Выключить Stars" if enabled else "✅ Включить Stars", "ad:portal:toggle-stars")
            )
        elif section == "price":
            await state.set_state(OwnerInput.stars)
            await state.update_data(plan_id=identity)
            body = "⭐ Пришли новую цену целым числом Stars."
            rows = [("⭐ Цены Stars", "ad:portal:pricing")]
        elif section == "toggle-stars":
            current = await setting(db, "manual_stars_enabled", "false")
            await set_setting(db, "manual_stars_enabled", "false" if current == "true" else "true")
            body = "✅ Настройка счетов Stars изменена."
            rows = [("⭐ Цены Stars", "ad:portal:pricing")]
    await call.message.edit_text(
        body, parse_mode=None, reply_markup=keyboard(*rows, ("Пульт управления 🎛️", "ad:menu"))
    )


@router.callback_query(F.data.startswith("ad:update:"))
async def subscription_update_action(call, state: FSMContext):
    if not authorized(call.from_user) or not call.message or call.message.chat.type != "private":
        await call.answer("Нет доступа", show_alert=True)
        return
    await call.answer()
    await state.clear()
    from app.services import subscription_updates as updates
    from aiogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as M
    parts=call.data.split(":")
    action=parts[2]
    token=parts[3] if len(parts)==4 else None
    try:
        async with sessions.begin() as db:
            if action in {"preview","new"}:
                item=await updates.draft(db,call.from_user.id,new=action=="new")
                token=item['token']
                if item['status']=='DRAFT':
                    count=len(await updates.recipients(db))
                    body=await updates.message(db)+f"\n\nПолучателей сейчас: <b>{count}</b>\nОтправка начнётся только после подтверждения."
                    markup=M(inline_keyboard=[
                        [B(text="📨 Отправить подписчикам",callback_data=f"ad:update:send:{token}")],
                        [B(text="Отмена",callback_data=f"ad:update:cancel:{token}")]])
                else:
                    body=await updates.progress(db,token)
                    markup=keyboard(("📊 Проверить отправку",f"ad:update:status:{token}"),("🔔 Новое уведомление","ad:update:new"),("Пульт управления 🎛️","ad:menu"))
            elif action=="cancel":
                await updates.cancel(db,token,call.from_user.id,get_settings())
                body="Уведомление отменено. Рассылка не запускалась."
                markup=keyboard(("Пульт управления 🎛️","ad:menu"))
            elif action in {"send","status"}:
                if action=="send":await updates.publish(db,token,call.from_user.id,get_settings())
                else:await updates.owned_draft(db,token,call.from_user.id,get_settings())
                body=await updates.progress(db,token)
                markup=keyboard(("📊 Проверить отправку",f"ad:update:status:{token}"),("🔔 Новое уведомление","ad:update:new"),("Пульт управления 🎛️","ad:menu"))
            else:raise ValueError("Unknown action")
        sent = await call.message.answer(body,parse_mode="HTML",reply_markup=markup)
        if action in {"preview","new"} and item['status']=='DRAFT' and type(getattr(sent,"message_id",None)) is int:
            from app.services.news_links import remember
            async with sessions.begin() as db:
                await remember(db,f"preview:{call.from_user.id}:{sent.message_id}",call.message.chat.id,sent.message_id,body,markup)
    except ValueError:
        await call.message.answer("Открой актуальное уведомление в /admin. Старое подтверждение недоступно.")
