import time
from dataclasses import replace

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, func

from app.bot.keyboards import keyboard
from app.bot.presentation import devices_label, period_label, price_label, traffic_label
from app.bot.texts import ru
from app.config import get_settings
from app.db.models import (
    Payment,
    Plan,
    SaleOrder,
    Server,
    Subscription,
    SupportTicket,
    User,
    PaidLink,
    TrialLink,
)
from app.db.session import sessions
from app.services.admin import AdminService
from app.services.manual_lifecycle import (
    DeletionBusy,
    delete_support,
    mark_connected,
    refuse_request,
)
from app.services.notifications import send_notifications
from app.services.paid_pool import current, current_customer_condition
from app.services.requests import REQUEST_CATEGORY, paid_requests

router = Router()
OPEN_REQUEST_STATUSES = ("OPEN", "IN_PROGRESS")


def admin_menu():
    if get_settings().manual_sales:
        return keyboard(
            ("👥 Наши пользователи", "ad:users"),
            ("🎁 Остаток пробных ссылок", "ad:portal:trials"),
            ("🔗 Запас платных ссылок", "ad:portal:stock"),
            ("🔔 Уведомление", "ad:update:preview"),
            ("Главное меню", "menu"),
        )
    return keyboard(
        ("👥 Пользователи", "ad:users"),
        ("💎 Подписки", "ad:subscriptions"),
        ("💰 Платежи", "ad:payments"),
        ("📦 Тарифы", "ad:plans"),
        ("🖥 Серверы", "ad:servers"),
        ("🎫 Поддержка", "ad:support"),
        ("📊 Аналитика", "ad:analytics"),
        ("⌂ Главное меню", "menu"),
    )


class AdminState(StatesGroup):
    reply = State()


@router.message(Command("admin"))
async def admin_start(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in get_settings().admin_ids:
        await message.answer(ru.ACCESS_DENIED)
        return
    await state.clear()
    async with sessions() as db:
        title = await admin_overview(db)
    await message.answer(title, reply_markup=admin_menu())


async def admin_overview(db):
    if not get_settings().manual_sales:
        return ru.ADMIN_TITLE
    total_users = await db.scalar(select(func.count(User.id)))
    customers = await db.scalar(select(func.count(User.id)).where(current_customer_condition()))
    trial_free = await db.scalar(select(func.count(TrialLink.id)).where(TrialLink.status == "FREE"))
    paid_free = await db.scalar(select(func.count(PaidLink.id)).where(PaidLink.status == "FREE"))
    reserved = await db.scalar(select(func.count(PaidLink.id)).where(PaidLink.status == "RESERVED"))
    return (
        f"Управление AERA\n\n👥 Пользователей бота: {total_users}\n💎 С действующим платным доступом: {customers}"
        f"\n\n🎁 Остаток пробных ссылок: {trial_free}\n🔗 Остаток платных ссылок: {paid_free}\n⏳ В резерве: {reserved}"
    )


@router.message(AdminState.reply, ~F.text.startswith("/"))
async def ticket_reply(message: Message, state: FSMContext) -> None:
    if message.from_user.id not in get_settings().admin_ids:
        await state.clear()
        return
    data = await state.get_data()
    async with sessions.begin() as db:
        ticket = await db.get(SupportTicket, data["ticket_id"])
        if ticket is None:
            await state.clear()
            await message.answer("Обращение уже удалено.", reply_markup=admin_menu())
            return
        if ticket.category == REQUEST_CATEGORY and get_settings().manual_sales:
            await state.clear()
            await message.answer(
                "💎 Покупки обрабатываются автоматически. "
                "Клиенты доступны в «Наших пользователях».",
                reply_markup=admin_menu(),
            )
            return
        if (
            ticket
            and ticket.category == REQUEST_CATEGORY
            and ticket.status not in OPEN_REQUEST_STATUSES
        ):
            await state.clear()
            await message.answer(
                "Эта заявка уже закрыта и находится в архиве.", reply_markup=admin_menu()
            )
            return
        reply_id = await AdminService(db, get_settings(), message.from_user.id).reply_ticket(
            data["ticket_id"], message.text or ""
        )
    await send_notifications(sessions, message.bot, keys=[f"support:{reply_id}"])
    await state.clear()
    await message.answer(
        "Ответ сохранён. Если доставка задержится, бот повторит отправку.",
        reply_markup=keyboard(
            ("Открыть обращение", f"ad:ticket:{data['ticket_id']}"),
            ("Пульт управления", "ad:menu"),
        ),
    )


@router.callback_query(F.data.startswith("ad:"))
async def admin_action(call: CallbackQuery, state: FSMContext) -> None:
    if call.from_user.id not in get_settings().admin_ids:
        await call.answer(ru.ACCESS_DENIED, show_alert=True)
        return
    await call.answer()
    await state.clear()
    data = call.data.split(":")
    text, markup = ru.ADMIN_TITLE, admin_menu()
    forgotten_telegram_id = None
    async with sessions.begin() as db:
        service = AdminService(db, get_settings(), call.from_user.id)
        section = data[1]
        if get_settings().manual_sales and section in {
            "requests",
            "archive",
            "close",
            "connected",
            "refuse",
        }:
            await call.message.edit_text(await admin_overview(db), reply_markup=admin_menu())
            return
        if get_settings().manual_sales and section in {"ticket", "erase"}:
            old_ticket = await db.get(SupportTicket, data[2])
            if old_ticket and old_ticket.category == REQUEST_CATEGORY:
                await call.message.edit_text(await admin_overview(db), reply_markup=admin_menu())
                return
        if section == "menu":
            text = await admin_overview(db)
        if get_settings().manual_sales and section in {
            "confirm",
            "apply",
            "subscriptions",
            "payments",
            "servers",
            "analytics",
        }:
            await call.message.edit_text(
                "💎 Оплата и выдача ссылок работают автоматически. "
                "Клиенты доступны в «Наших пользователях».",
                reply_markup=admin_menu(),
            )
            return
        if section == "analytics":
            stats = await service.analytics()
            text = "\n".join(f"{key}: {value}" for key, value in stats.items())
        elif section in {
            "users",
            "subscriptions",
            "payments",
            "plans",
            "servers",
            "support",
        }:
            models = {
                "users": User,
                "subscriptions": Subscription,
                "payments": Payment,
                "plans": Plan,
                "servers": Server,
                "support": SupportTicket,
            }
            query = select(models[section])
            if section == "support":
                query = query.where(SupportTicket.category != REQUEST_CATEGORY)
            if section == "users" and get_settings().manual_sales:
                query = query.where(current_customer_condition())
                text = (
                    "👥 Наши пользователи\n\n💎 Действующие платные подписки "
                    "и оплаченные ссылки, ожидающие первого подключения."
                )
            page = max(0, int(data[2])) if section == "users" and len(data) > 2 else 0
            records = list(
                await db.scalars(
                    query.order_by(models[section].created_at.desc(), models[section].id.desc())
                    .offset(page * 25)
                    .limit(26)
                )
            )
            if section == "users":
                total = await db.scalar(
                    select(func.count()).select_from(query.order_by(None).subquery())
                )
                text = f"👥 Наши пользователи — {total}\n\nДействующий платный доступ:\n" + (
                    "\n".join(f"• ID: {r.telegram_id}" for r in records[:25])
                    or "Пока нет пользователей."
                )
                markup = keyboard(
                    *([("Предыдущая страница", f"ad:users:{page - 1}")] if page else []),
                    *(
                        [("Следующая страница", f"ad:users:{page + 1}")]
                        if len(records) > 25
                        else []
                    ),
                    ("🔄 Обновить", f"ad:users:{page}"),
                    ("Назад", "ad:menu"),
                )
                if not records:
                    text = "👥 Наши пользователи — 0\n\nДействующих платных подписок пока нет."
            elif section == "support":
                markup = keyboard(
                    *[(f"{r.subject[:32]} · {r.status}", f"ad:ticket:{r.id}") for r in records],
                    ("Назад", "ad:menu"),
                )
            elif section == "plans":
                records.sort(
                    key=lambda plan: (
                        plan.unlimited_devices,
                        plan.device_limit,
                        plan.duration_months or plan.duration_days,
                        plan.name,
                    )
                )
                text = (
                    "ТАРИФЫ:\n\n"
                    + "\n\n".join(
                        f"{plan.name}\n{period_label(plan)} - {price_label(plan)}\n"
                        f"{devices_label(plan.device_limit, plan.unlimited_devices)}\n"
                        f"{traffic_label(plan.traffic_limit_bytes)}"
                        + ("\nОтключён для выбора" if not plan.is_active else "")
                        for plan in records
                    )
                    if records
                    else ru.ADMIN_EMPTY
                )
            else:
                text = "\n\n".join(
                    str(r.id)
                    + "\n"
                    + str(getattr(r, "name", ""))
                    + " "
                    + str(getattr(r, "status", ""))
                    for r in records
                )
            text = text or ru.ADMIN_EMPTY
        elif section == "user":
            user = await db.get(User, data[2])
            if not user:
                await call.message.edit_text("Пользователь уже удалён.", reply_markup=admin_menu())
                return
            if get_settings().manual_sales and not await db.scalar(
                select(User.id).where(User.id == user.id, current_customer_condition())
            ):
                await call.message.edit_text(
                    "👥 У клиента нет действующей платной подписки.\n"
                    "История оплаченных покупок сохранена в его боте.",
                    reply_markup=keyboard(
                        ("👥 Наши пользователи", "ad:users"), ("Пульт управления", "ad:menu")
                    ),
                )
                return
            sub = await db.scalar(select(Subscription).where(Subscription.user_id == user.id))
            text = f"Telegram: {user.telegram_id}\n@{user.username or '-'}\n"
            if get_settings().manual_sales:
                from app.db.models import FamilyAccess, ManualAccess

                access = await db.scalar(
                    select(ManualAccess).where(ManualAccess.user_id == user.id)
                )
                family = await db.scalar(
                    select(FamilyAccess).where(
                        FamilyAccess.user_id == user.id, FamilyAccess.is_active.is_(True)
                    )
                )
                if family:
                    text += f"\n✅ Семья · бессрочно\n📱 Устройств: {family.device_limit or 'безлимит'}\n"
                paid = await current(db, user.id)
                if access and paid is None:
                    plan = await db.get(Plan, access.plan_id)
                    text += f"\n📦 {plan.name}\n✅ Подписка действует\n"
                    text += f"\n📅 До: {access.expires_at:%d.%m.%Y %H:%M} UTC\n"
                if paid:
                    plan = await db.get(Plan, paid.plan_id)
                    text += (
                        f"\n📦 {plan.name} · {period_label(plan)}\n"
                        f"{devices_label(plan.device_limit, plan.unlimited_devices)}\n"
                        "🚀 Трафик безлимитный\n"
                    )
                    text += (
                        "✅ Подписка действует\n"
                        if paid.status == "ACTIVE"
                        else "⏳ Ожидает первого подключения\n"
                    )
                    text += f"🔗 Личный ключ: {paid.label}\n"
                    if paid.expires_at:
                        from app.services.cabinet import expiry_text

                        text += expiry_text(paid.expires_at, "ru") + "\n"
                    else:
                        text += "⏳ Срок начнётся после первого VPN-подключения.\n"
            if sub:
                text += f"{sub.status}\nДо {sub.expires_at:%d.%m.%Y}"
            markup = keyboard(
                ("+30 дней", f"ad:confirm:extend:{user.id}"),
                ("Отключить", f"ad:confirm:disable:{user.id}"),
                ("Включить", f"ad:confirm:enable:{user.id}"),
                ("Сбросить ссылку", f"ad:confirm:rotate:{user.id}"),
                ("Назад", "ad:users"),
            )
            if get_settings().manual_sales:
                from aiogram.types import InlineKeyboardButton

                contact = (
                    f"https://t.me/{user.username}"
                    if user.username
                    else f"tg://user?id={user.telegram_id}"
                )
                markup = keyboard(("🔄 Обновить", f"ad:user:{user.id}"), ("Назад", "ad:users"))
                markup.inline_keyboard.insert(
                    0, [InlineKeyboardButton(text="Написать пользователю", url=contact)]
                )
        elif section == "confirm":
            text, markup = (
                ru.ADMIN_CONFIRM,
                keyboard(("Подтвердить", f"ad:apply:{data[2]}:{data[3]}"), ("Назад", "ad:users")),
            )
        elif section == "apply":
            await service.change_access(data[3], data[2], True)
            text = ru.ADMIN_SAVED
        elif section == "ticket":
            from app.db.models import SupportMessage

            ticket = await db.get(SupportTicket, data[2])
            if ticket is None:
                await call.message.edit_text("Заявка не найдена.", reply_markup=admin_menu())
                return
            if ticket.category == REQUEST_CATEGORY and not await db.scalar(
                paid_requests().where(SupportTicket.id == ticket.id)
            ):
                await call.message.edit_text(
                    "⏳ Заявка появится в пульте после подтверждения оплаты.",
                    reply_markup=admin_menu(),
                )
                return
            messages = await db.scalars(
                select(SupportMessage)
                .where(SupportMessage.ticket_id == ticket.id)
                .order_by(SupportMessage.created_at)
            )
            text = "\n\n".join(m.text for m in messages)[-3800:]
            if ticket.category == REQUEST_CATEGORY:
                text = text.replace(
                    "Нужны личное сопровождение, согласование оплаты и подключение.", ""
                ).rstrip()
                text = "✅ ОПЛАЧЕНО\n\n" + text
            archived = (
                ticket.category == REQUEST_CATEGORY and ticket.status not in OPEN_REQUEST_STATUSES
            )
            if not archived:
                await state.set_state(AdminState.reply)
                await state.update_data(ticket_id=ticket.id)
                text += "\n\n" + ru.ADMIN_REPLY
            if archived:
                text = "Архивная заявка - закрыта\n\n" + text
                markup = keyboard(
                    ("Архив заявок", "ad:archive"), ("Активные заявки", "ad:requests")
                )
                if ticket.status != "CONNECTED":
                    markup.inline_keyboard[:0] = keyboard(
                        ("Клиент подключён", f"ad:connected:{ticket.id}"),
                        ("Клиент отказался", f"ad:refuse:{ticket.id}"),
                    ).inline_keyboard
            elif ticket.category == REQUEST_CATEGORY:
                markup = keyboard(
                    ("Клиент подключён", f"ad:connected:{ticket.id}"),
                    ("Клиент отказался", f"ad:refuse:{ticket.id}"),
                    ("Активные заявки", "ad:requests"),
                )
            else:
                markup = keyboard(
                    ("Удалить обращение", f"ad:delete-support:{ticket.id}"),
                    ("Поддержка", "ad:support"),
                    ("Пульт управления", "ad:menu"),
                )
        elif section == "close":
            # Old Telegram cards route to the two explicit outcomes too.
            text, markup = (
                "Чем завершилась заявка?",
                keyboard(
                    ("Клиент подключён", f"ad:connected:{data[2]}"),
                    ("Клиент отказался", f"ad:refuse:{data[2]}"),
                    ("Активные заявки", "ad:requests"),
                ),
            )
        elif section == "connected":
            order = await db.scalar(select(SaleOrder).where(SaleOrder.ticket_id == data[2]))
            if order and not order.paid_at:
                await call.message.edit_text(
                    "⭐ Оплата пока не подтверждена. После оплаты Stars бот сам выдаст ссылку "
                    "и добавит клиента в пользователей.",
                    reply_markup=keyboard(("Заявка", f"ad:ticket:{data[2]}")),
                )
                return
            saved = await mark_connected(db, data[2])
            text = (
                "Клиент подключён ✓\nОн добавлен в «Наши пользователи». Заявка в архиве."
                if saved
                else "Заявка уже удалена."
            )
            markup = keyboard(("Наши пользователи", "ad:users"), ("Активные заявки", "ad:requests"))
        elif section in {"refuse", "delete-support"}:
            ticket = await db.get(SupportTicket, data[2])
            if ticket is None:
                text = "Обращение уже удалено."
            else:
                text = (
                    "Клиент отказался. Удалить заявку и данные нового клиента из бота навсегда?\n\n"
                    "Если это администратор или существующий клиент, удалится только эта заявка."
                    if section == "refuse"
                    else "Удалить это обращение и всю переписку по нему из данных бота навсегда?"
                )
                markup = keyboard(
                    ("Удалить навсегда", f"ad:erase:{data[2]}"),
                    ("Отмена", f"ad:ticket:{data[2]}"),
                )
        elif section == "erase":
            ticket = await db.get(SupportTicket, data[2])
            try:
                if ticket and ticket.category == REQUEST_CATEGORY:
                    outcome, forgotten_telegram_id = await refuse_request(
                        db, ticket.id, get_settings().admin_ids
                    )
                    text = {
                        "forgotten": "Заявки и данные отказавшегося клиента удалены из бота.",
                        "request_only": (
                            "Заявка удалена. Учётная запись существующего клиента "
                            "или администратора сохранена."
                        ),
                        "connected": (
                            "Клиент уже отмечен подключённым. "
                            "Его данные сохраняются в «Наших пользователях»."
                        ),
                        "missing": "Заявка уже удалена.",
                    }[outcome]
                else:
                    await delete_support(db, data[2])
                    text = "Обращение и переписка удалены из данных бота."
            except DeletionBusy as error:
                text = str(error)
                markup = keyboard(
                    ("Повторить удаление", f"ad:erase:{data[2]}"),
                    ("Открыть обращение", f"ad:ticket:{data[2]}"),
                )
    if forgotten_telegram_id is not None:
        context = FSMContext(
            storage=state.storage,
            key=replace(state.key, chat_id=forgotten_telegram_id, user_id=forgotten_telegram_id),
        )
        await context.clear()
        cache = getattr(state.storage, "redis", None)
        if cache is not None:
            minute = int(time.time()) // 60
            await cache.delete(
                *[f"bot-rate:{forgotten_telegram_id}:{n}" for n in range(minute - 2, minute + 1)]
            )
    await call.message.edit_text(text, reply_markup=markup, parse_mode=None)
