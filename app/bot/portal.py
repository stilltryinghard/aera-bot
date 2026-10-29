"""Current AERA checkout and compatibility routes for old Telegram buttons."""

import logging
from datetime import UTC, datetime
from html import escape
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from app.bot.keyboards import keyboard
from app.bot.presentation import edit_screen, send_screen
from app.bot.texts.portal import devices, period
from app.bot.texts.portal import text as tr
from app.config import get_settings
from app.core.security import TokenVault
from app.db.models import (
    Payment,
    Plan,
    SaleOrder,
    SupportTicket,
)
from app.db.session import sessions
from app.services.commerce import CommerceService, utc
from app.services.portal import ensure_order
from app.services.orders import cancel_unpaid_order
from app.services.payment_timeout import PaymentWindowExpired, expire_open_orders
from app.services.requests import paid_requests, select_tariff

router = Router(name="manual_portal")
router.message.filter(lambda event: get_settings().manual_sales)
router.callback_query.filter(lambda event: get_settings().manual_sales)


class HelpFlow(StatesGroup):
    message = State()


def home_menu(lang):
    from app.bot.customer_v2 import home_menu as customer_menu
    return customer_menu()


def cancelled_menu(lang):
    return keyboard(
        ("🛒 Choose subscription" if lang == "en" else "🛒 Выбрать подписку", "plans"),
        ("👤 My account" if lang == "en" else "👤 Личный кабинет", "profile"),
    )


def actions(lang, *items):
    markup = keyboard(*[(tr(k, lang), d) for k, d in items], (tr("home", lang), "menu"))
    for row in markup.inline_keyboard:
        for index, button in enumerate(row):
            if button.callback_data == "support":
                row[index] = InlineKeyboardButton(text=button.text, url="https://t.me/aerasuppbot")
    return markup


async def get_user(db, sender):
    return await CommerceService(db, TokenVault(get_settings().app_secret)).user(
        sender.id, username=sender.username, first_name=sender.first_name
    )


@router.message(Command("privacy", "terms", "support", "paysupport"))
async def commands(message: Message, state: FSMContext):
    await state.clear()
    cmd = (message.text or "").split()[0].split("@")[0]
    if cmd in {"/support", "/paysupport"}:
        body, markup = support_screen()
    else:
        privacy = cmd == "/privacy"
        body = "<b>🔐 Конфиденциальность</b>" if privacy else "<b>📄 Условия подписки</b>"
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть документ", url="https://aera-reserve.duckdns.org/" + ("privacy" if privacy else "terms"))],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile")],
        ])
    await message.answer(body, parse_mode="HTML", reply_markup=markup)


@router.message(HelpFlow.message, F.text, ~F.text.startswith("/"))
async def support_message(message: Message, state: FSMContext):
    # Old saved FSM state must lead to the current support bot, not a second inbox.
    await state.clear()
    body, markup = support_screen()
    await message.answer(body, parse_mode="HTML", reply_markup=markup)


async def cabinet_link(db, user, selection=None):
    from app.services.cabinet import entries, selected, personal_link

    entry = selected(await entries(db, user), selection)
    if entry and entry[0] in {"paid", "manual", "family"}:
        from app.subscription.multinode import owned_panel_url

        settings = get_settings()
        return await owned_panel_url(db, user, entry[1], TokenVault(settings.app_secret), settings)
    return personal_link(entry, user)


async def cabinet_screen(db, user, lang, settings, vault, selection=None):
    from app.bot.customer_v2 import cabinet
    if selection:
        from app.services.cabinet import entries, selected
        selected(await entries(db, user), selection)
    return await cabinet(db, user)


async def current_action(db, user, data):
    if data == "language" or data.startswith(("language:", "privacy")) or data in {"link", "qr"}:
        return "profile"
    if data == "hiddify:plans" or data.startswith("family:"):
        return "plans"
    if data in {"connect", "how", "download:windows"} or data.startswith("guide:"):
        return "instructions"
    if data.startswith("terms:"):
        return "stars:" + data.split(":", 1)[1]
    parts = data.split(":")
    if parts[0] == "checkout" and len(parts) == 2:
        return "plan:" + parts[1]
    if parts[0] in {"method", "agreeorder"} and len(parts) == 3:
        return "plan:" + parts[2]
    if parts[0] in {"sbp", "bank", "bankpay", "cryptosoon"} and len(parts) == 2:
        return "order:" + parts[1]
    if parts[0] == "checkorder" and len(parts) == 2:
        payment = await db.get(Payment, parts[1])
        if not payment or payment.user_id != user.id:
            raise ValueError("Payment ownership mismatch")
        order = await db.scalar(select(SaleOrder).where(SaleOrder.payment_id == payment.id))
        if not order:
            raise ValueError("Order unavailable")
        return "order:" + order.id
    return data


def support_screen():
    return ("<b>💬 Поддержка AERA</b>\n\n<blockquote>Напишите нам — поможем с подключением и подпиской 💛</blockquote>",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Написать в поддержку", url="https://t.me/aerasuppbot")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile")],
            ]))


def instructions_screen():
    from app.bot.customer_v2 import INSTRUCTIONS
    return ("<b>💡 Инструкции AERA</b>", InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💡 Открыть инструкции", url=INSTRUCTIONS)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile")],
    ]))


def payment_step(method, amount, url, order_id):
    titles = {"sbp": "🏦 Оплата по СБП", "crypto": "🪙 Оплата криптовалютой", "stars": "⭐ Оплата Telegram Stars"}
    title = titles[method]
    total = f"{amount} ⭐" if method == "stars" else f"{amount / 100:g} ₽"
    body = (f"<b>{title}</b>\n\n<blockquote>💰 К оплате: {total}\n"
            "Срок оплаты — 15 минут\nДоступ обновится после оплаты 💛</blockquote>")
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить", url=url)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="order:" + order_id)],
    ])
    return body, markup


async def order_screen(db, order, user, lang):
    from app.services.renewals import intent

    await expire_open_orders(db, user_id=user.id)
    renewal = await intent(db, order)
    ticket = await db.get(SupportTicket, order.ticket_id)
    if not order.paid_at and ticket.status == "CLOSED":
        return tr("cancelled", lang), cancelled_menu(lang)
    plan = await db.get(Plan, order.plan_id)
    if order.paid_at:
        from app.db.models import PaidLink

        issued = await db.scalar(
            select(PaidLink.id).where(
                PaidLink.order_id == order.id, PaidLink.issued_at.is_not(None)
            )
        )
        if renewal and renewal.status == "DONE":
            issued = True
        body = tr("paid_ready" if issued else "paid", lang)
        markup = keyboard(("⬅️ Личный кабинет", "profile"))
    else:
        title = "🔄 Продление подписки" if renewal else "💳 Оплата подписки"
        limit = "безлимит" if plan.unlimited_devices else str(plan.device_limit)
        body = (f"<b>{title}</b>\n\n"
                f"<blockquote>📅 Срок: {escape(period(plan, lang))}\n"
                f"📱 Устройств: {limit}\n"
                f"💰 К оплате: {order.amount_rub_minor / 100:g} ₽</blockquote>\n\n"
                "Выберите удобный способ оплаты 👇")
        stars = (max(1, (plan.stars_price * (100 - order.discount_percent) + 99) // 100)
                 if plan.stars_price else None)
        markup = keyboard(
            (f"🏦 СБП · {order.amount_rub_minor / 100:g} ₽", f"platega:sbp:{order.id}"),
            ("🪙 Криптовалюта", f"platega:crypto:{order.id}"),
            (f"⭐ Telegram Stars · {stars}" if stars else "⭐ Telegram Stars", f"terms:{order.id}"),
            ("⬅️ Личный кабинет", "profile"),
        )
    return body, markup


@router.callback_query(lambda call: not (call.data or "").startswith("ad:"))
async def navigate(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    settings = get_settings()
    vault = TokenVault(settings.app_secret)
    data = call.data or "menu"
    lang = "ru"
    art = "welcome"
    invoice = None
    try:
        async with sessions.begin() as db:
            user = await get_user(db, call.from_user)
            lang = user.language_code
            if user.is_blocked or not user.is_active:
                raise ValueError("Account unavailable")
            data = await current_action(db, user, data)
            from app.bot.customer_v2 import menu_for_user
            markup = await menu_for_user(db, user)
            body = tr("welcome", lang)
            if data.startswith("changeplan:"):
                order = await owned_order(db, data.split(":")[1], user)
                from app.services.renewals import intent

                if not await intent(db, order):
                    raise ValueError("Renewal unavailable")
                if not await cancel_unpaid_order(db, order, user):
                    raise ValueError(
                        "Оплата уже обрабатывается. Дождитесь её завершения перед выбором другого тарифа."
                    )
                data = "plans"
            if data == "menu":
                body, markup = await cabinet_screen(db, user, lang, settings, vault)
                art = "profile"
            elif data == "plans":
                from app.bot.customer_v2 import term_screen
                body, markup = term_screen()
                art = "plans"
            elif data.startswith("renew:"):
                from app.services.renewals import create_renewal

                _, kind, identity = data.split(":")
                order = await create_renewal(db, user, kind, identity)
                data = "order:" + order.id
            elif data.startswith("plan:"):
                ticket, plan = await select_tariff(
                    db,
                    CommerceService(db, vault),
                    user,
                    data.split(":")[1],
                    settings.admin_ids,
                    notify_owner=False,
                )
                order = await ensure_order(db, ticket, plan)
                data = "order:" + order.id
            if data.startswith("order:"):
                order = await owned_order(db, data.split(":")[1], user)
                body, markup = await order_screen(db, order, user, lang)
                art = "payments"
            elif data.startswith("cancelorder:"):
                order = await owned_order(db, data.split(":")[1], user)
                cancelled = await cancel_unpaid_order(db, order, user)
                body = (
                    ("Purchase cancelled." if lang == "en" else "Покупка отменена.")
                    if cancelled
                    else (
                        "Payment is already being processed. Check your account."
                        if lang == "en"
                        else "Оплата уже обрабатывается. Проверьте личный кабинет."
                    )
                )
                markup = cancelled_menu(lang) if cancelled else actions(lang, ("plans", "plans"), ("profile", "profile"))
            elif data.startswith("stars:"):
                order = await owned_order(db, data.split(":")[1], user)
                user.terms_accepted_at = datetime.now(UTC)
                user.terms_version = "portal-2"
                from app.services.manual_checkout import stars_payment

                payment = await stars_payment(db, data.split(":")[1], user)
                if payment is None:
                    from app.services.paid_pool import enabled

                    body = tr("paid_stock_empty" if await enabled(db) else "soon", lang)
                else:
                    invoice = (payment.id, payment.amount_minor)
                art = "payment_stars"
            elif data.startswith("platega:"):
                from app.services.platega import create_payment

                _, method, order_id = data.split(":")
                order = await owned_order(db, order_id, user)
                amount = order.amount_rub_minor
                uid = user.id
                await db.commit()
                result = await create_payment(
                    sessions, settings, uid, method, "bot", order_id=order_id
                )
                body, markup = payment_step(method, amount, result["url"], order_id)
                art = "payment_" + method
            elif data == "instructions":
                body, markup = instructions_screen()
                art = "connect"
            elif data in {"profile", "subscription"} or data.startswith(
                ("subscription:", "profile:")
            ):
                art = "profile"
                if data == "profile" or data.startswith("profile:"):
                    selection = data[len("profile:") :] if data.startswith("profile:") else None
                    body, markup = await cabinet_screen(db, user, lang, settings, vault, selection)
                else:
                    art = "history"
                    page = max(0, int(data.split(":")[1])) if ":" in data else 0
                    tickets = list(
                        await db.scalars(
                            paid_requests()
                            .where(SupportTicket.user_id == user.id)
                            .order_by(SaleOrder.paid_at.desc(), SupportTicket.id.desc())
                            .offset(page * 5)
                            .limit(6)
                        )
                    )
                    body = f"<b>{tr('requests', lang)}</b>\n\n"
                    from app.services.cabinet import payment_method

                    for ticket in tickets[:5]:
                        order = await db.scalar(
                            select(SaleOrder).where(SaleOrder.ticket_id == ticket.id)
                        )
                        plan = await db.get(Plan, order.plan_id) if order else None
                        payment = (
                            await db.get(Payment, order.payment_id) if order.payment_id else None
                        )
                        amount = (
                            f"{payment.amount_minor} ⭐"
                            if payment and payment.currency == "XTR"
                            else f"{order.amount_rub_minor / 100:g} ₽"
                        )
                        body += (
                            tr(
                                "purchase_record",
                                lang,
                                number=ticket.id[:8],
                                plan=("AERA · " + devices(plan, lang)) if plan else "AERA",
                                period=period(plan, lang) if plan else "",
                                amount=amount,
                                method=payment_method(payment, lang),
                                date=utc(order.paid_at).strftime("%d.%m.%Y %H:%M UTC"),
                            )
                            + "\n\n"
                        )
                    if not tickets:
                        body += tr("empty_requests", lang)
                    markup = keyboard(
                        *([(tr("previous", lang), f"subscription:{page - 1}")] if page else []),
                        *(
                            [(tr("next", lang), f"subscription:{page + 1}")]
                            if len(tickets) > 5
                            else []
                        ),
                        (tr("home", lang), "menu"),
                    )
            elif data == "referral":
                from app.bot.customer_v2 import partner
                body, markup = await partner(db, user)
                art = "referral"
            elif data == "trial":
                from app.bot.customer_v2 import trial_screen
                body, markup = await trial_screen(db, user)
                art = "trial"
            elif data == "support":
                body, markup = support_screen()
                art = "support"
        if invoice:
            from app.integrations.payments.telegram_stars import TelegramStarsProvider

            url = await TelegramStarsProvider(call.bot).create_payment(
                invoice[0], invoice[1], "XTR", lang=lang
            )
            body, markup = payment_step("stars", invoice[1], url, order.id)
        await edit_screen(call.message, body, reply_markup=markup, art=art)
    except PaymentWindowExpired:
        await edit_screen(call.message, "⌛ Срок оплаты истёк. Выберите подписку и способ оплаты заново.",
                          reply_markup=cancelled_menu(lang), art="payments")
    except Exception as error:
        logging.getLogger("aera").error("portal_action_failed type=%s", type(error).__name__)
        await edit_screen(
            call.message, tr("error", lang), reply_markup=actions(lang, ("support", "support"))
        )


async def owned_order(db, identity, user):
    order = await db.get(SaleOrder, identity)
    ticket = await db.get(SupportTicket, order.ticket_id) if order else None
    if not ticket or ticket.user_id != user.id or ticket.category != "TARIFF_REQUEST":
        raise ValueError("Order ownership mismatch")
    return order


async def send_selected_checkout(message, plan_id, method="stars"):
    """Website and bot share the same validated order screen and payment methods."""
    settings = get_settings()
    lang = "ru"
    try:
        if method not in {"stars", "rub"}:
            raise ValueError("Unsupported checkout method")
        async with sessions.begin() as db:
            user = await get_user(db, message.from_user)
            lang = user.language_code
            ticket, plan = await select_tariff(
                db,
                CommerceService(db, TokenVault(settings.app_secret)),
                user,
                plan_id,
                settings.admin_ids,
                notify_owner=False,
            )
            order = await ensure_order(db, ticket, plan)
            body, markup = await order_screen(db, order, user, lang)
        await send_screen(message, body, reply_markup=markup, art="payments")
    except Exception as error:
        logging.getLogger("aera").error("website_checkout_failed type=%s", type(error).__name__)
        await message.answer(tr("error", lang), reply_markup=actions(lang, ("support", "support")))
