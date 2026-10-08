import asyncio
import io
import logging
from datetime import UTC, datetime
from functools import lru_cache

import qrcode
from aiogram import Dispatcher, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select

from app.bot.client import create_bot
from app.bot.keyboards import button, keyboard, menu, welcome_menu
from app.bot.presentation import (
    catalog_caption,
    devices_label,
    edit_screen,
    family_caption,
    period_label,
    plan_caption,
    price_label,
    send_screen,
    subscription_caption,
)
from app.bot.texts import ru
from app.config import get_settings
from app.core.security import TokenVault
from app.db.models import Plan, ProvisioningJob, SupportMessage, SupportTicket
from app.db.session import sessions
from app.integrations.xui.factory import create_adapter
from app.services.catalog import FAMILIES, family_of
from app.services.commerce import CommerceService
from app.services.provisioning import ProvisioningService
from app.services.settings import setting

router = Router()
settings = get_settings()
vault = TokenVault(settings.app_secret)
adapter = create_adapter(sessions, settings)
provisioner = ProvisioningService(sessions, adapter, vault)


class SupportState(StatesGroup):
    message = State()


class PromoState(StatesGroup):
    code = State()


@router.message(Command("start", "help"))
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with sessions.begin() as db:
        user = await CommerceService(db, vault).user(
            message.from_user.id, username=message.from_user.username
        )
        from app.services.referrals import register_referral

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("ref_"):
            await register_referral(db, user, parts[1][4:])
    await send_screen(
        message,
        ru.WELCOME,
        reply_markup=welcome_menu(),
    )


@router.message(Command("privacy", "terms"))
async def legal(message: Message) -> None:
    command = (message.text or "").split()[0].split("@")[0]
    key = "privacy_text" if command == "/privacy" else "terms_text"
    async with sessions() as db:
        text = await setting(db, key, ru.PRIVACY if key == "privacy_text" else ru.TERMS)
    await message.answer(text)


@router.message(Command("myid"))
async def myid(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: {message.from_user.id}")


@router.message(Command("support", "paysupport"))
async def support(message: Message, state: FSMContext) -> None:
    await state.set_state(SupportState.message)
    await message.answer(ru.SUPPORT)


@router.message(SupportState.message)
async def support_message(message: Message, state: FSMContext) -> None:
    if not message.text or len(message.text) > 4000:
        await message.answer(ru.ERROR)
        return
    async with sessions.begin() as db:
        service = CommerceService(db, vault)
        user = await service.user(message.from_user.id)
        data = await state.get_data()
        ticket = SupportTicket(
            user_id=user.id, category=data.get("category", "Другое"), subject="Обращение из бота"
        )
        db.add(ticket)
        await db.flush()
        db.add(
            SupportMessage(
                ticket_id=ticket.id, sender_type="USER", sender_id=user.id, text=message.text
            )
        )
        from app.services.notifications import enqueue

        for admin_id in settings.admin_ids:
            admin_user = await service.user(admin_id)
            await enqueue(
                db,
                f"ticket:{ticket.id}:{admin_id}",
                admin_user.id,
                "SUPPORT_ADMIN",
                ru.NEW_TICKET.format(id=ticket.id[:8]),
            )
    await state.clear()
    await message.answer(ru.TICKET.format(id=ticket.id[:8]), reply_markup=menu())


@router.message(PromoState.code)
async def promo_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    async with sessions.begin() as db:
        service = CommerceService(db, vault)
        user = await service.user(message.from_user.id)
        payment = await service.purchase(
            user.id, data["plan_id"], settings.payment_provider, promo_code=message.text or ""
        )
    await state.clear()
    if settings.payment_provider == "telegram_stars":
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        from app.integrations.payments.telegram_stars import TelegramStarsProvider

        url = await TelegramStarsProvider(message.bot).create_payment(
            payment.id, payment.amount_minor, payment.currency
        )
        await message.answer(
            ru.PAYMENT_PROMPT,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить", url=url)]]
            ),
        )
    else:
        await message.answer(
            ru.MOCK_PAY.format(name="AERA"),
            reply_markup=keyboard(("Подтвердить тестовую оплату", f"pay:{payment.id}")),
        )


@router.callback_query()
async def navigate(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    data = call.data or "menu"
    if data != "support":
        await state.clear()
    try:
        if data == "download:windows":
            from app.bot.installers import send_windows_installer

            await send_windows_installer(call.message)
            return
        text, markup, art = ru.MENU, menu(), "welcome"
        async with sessions.begin() as db:
            service = CommerceService(db, vault)
            user = await service.user(call.from_user.id, username=call.from_user.username)
            if data.startswith("accept:"):
                user.terms_accepted_at = datetime.now(UTC)
                user.terms_version = await setting(db, "terms_version", "1")
                data = "buy:" + data.split(":", 1)[1]
            if data.startswith("buy:") and settings.payment_provider == "telegram_stars":
                terms_version = await setting(db, "terms_version", "1")
                terms_text = await setting(db, "terms_text", "")
                if not terms_text:
                    await edit_screen(call.message, ru.TERMS_PENDING, reply_markup=menu())
                    return
                if not user.terms_accepted_at or user.terms_version != terms_version:
                    await edit_screen(
                        call.message,
                        terms_text,
                        reply_markup=keyboard(
                            ("Прочитал и согласен", "accept:" + data.split(":", 1)[1]),
                            ("Назад", "plans"),
                        ),
                    )
                    return
            sub, client = await service.subscription(user.id)
            if data == "onboarding":
                text, markup = ru.ONBOARDING, keyboard(("Продолжить", "how"))
            elif data == "how":
                text, markup = ru.HOW, keyboard(("Посмотреть тарифы", "plans"))
            elif data == "plans":
                plans = await service.plans()
                text, art = catalog_caption(plans), "plans"
                markup = keyboard(
                    *[
                        (f"{title} · {devices}", f"family:{family}")
                        for family, (title, devices, *_rest) in FAMILIES.items()
                        if any(family_of(p) == family for p in plans)
                    ],
                    *[(p.name, f"plan:{p.id}") for p in plans if not family_of(p)],
                    ("Главное меню", "menu"),
                )
                if await setting(db, "trial_enabled", "false") == "true" and not user.trial_used:
                    from aiogram.types import InlineKeyboardButton

                    markup.inline_keyboard.insert(
                        0, [InlineKeyboardButton(text="🎁 Попробовать AERA", callback_data="trial")]
                    )
            elif data.startswith("family:"):
                family = data.split(":", 1)[1]
                plans = [p for p in await service.plans() if family_of(p) == family]
                if family not in FAMILIES or not plans:
                    raise ValueError("Tariff unavailable")
                plans.sort(key=lambda p: p.duration_months or 0)
                text, art = family_caption(family, plans), "plans"
                from aiogram.types import InlineKeyboardMarkup

                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            button(
                                f"{period_label(p)} · {price_label(p)}",
                                f"plan:{p.id}",
                                "primary" if p.duration_months == 6 else None,
                            )
                        ]
                        for p in plans
                    ]
                    + [[button("Все тарифы", "plans")]]
                )
            elif data.startswith("plan:"):
                plan = await db.get(Plan, data.split(":", 1)[1])
                if not plan or not plan.is_active:
                    raise ValueError("Tariff unavailable")
                text, art = plan_caption(plan), "plans"
                markup = keyboard(
                    ("💳 Купить", f"checkout:{plan.id}"),
                    ("🎁 Промокод", f"promo:{plan.id}"),
                    ("Тарифы", "plans"),
                )
            elif data.startswith("crypto:"):
                if not settings.crypto_mock_enabled or settings.app_env == "production":
                    raise ValueError("Crypto simulation disabled")
                from app.services.crypto import create_invoice

                invoice = await create_invoice(db, vault, user.id, data.split(":", 1)[1])
                text = ru.CRYPTO_INVOICE.format(
                    amount=invoice.expected_amount,
                    asset=invoice.asset,
                    network=invoice.network,
                    expiry=invoice.expires_at.strftime("%H:%M UTC"),
                )
                markup = keyboard(
                    ("Проверить оплату", f"crypto-check:{invoice.id}"), ("Назад", "plans")
                )
            elif data.startswith("crypto-check:"):
                from app.db.models import CryptoInvoice, Payment
                from app.integrations.payments.crypto import MockCryptoProvider
                from app.services.crypto import reconcile_invoice

                invoice = await db.get(CryptoInvoice, data.split(":", 1)[1])
                payment = await db.get(Payment, invoice.payment_id) if invoice else None
                if not payment or payment.user_id != user.id:
                    raise ValueError("Invoice ownership mismatch")
                provider_id = invoice.provider_invoice_id
                await db.commit()
                status = await reconcile_invoice(
                    sessions,
                    vault,
                    MockCryptoProvider(sessions, settings.crypto_mock_secret),
                    provider_id,
                )
                text = ru.CRYPTO_STATUSES.get(status, ru.ERROR)
            elif data.startswith("promo:"):
                await state.set_state(PromoState.code)
                await state.update_data(plan_id=data.split(":", 1)[1])
                text = ru.PROMO_PROMPT
            elif data.startswith("buy:"):
                payment = await service.purchase(
                    user.id, data.split(":", 1)[1], provider=settings.payment_provider
                )
                plan = await db.get(Plan, payment.plan_id)
                text, markup = (
                    ru.MOCK_PAY.format(name=plan.name),
                    keyboard(
                        ("Подтвердить тестовую оплату", f"pay:{payment.id}"), ("Назад", "plans")
                    ),
                )
                if settings.payment_provider == "telegram_stars":
                    await db.commit()
                    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

                    from app.integrations.payments.telegram_stars import TelegramStarsProvider

                    url = await TelegramStarsProvider(call.bot).create_payment(
                        payment.id, payment.amount_minor, payment.currency
                    )
                    text = ru.PAYMENT_PROMPT
                    markup = InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить", url=url)]]
                    )
            elif data.startswith("pay:"):
                if settings.payment_provider != "mock" or settings.app_env == "production":
                    raise ValueError("Mock confirmation disabled")
                from app.db.models import Payment

                payment_id = data.split(":", 1)[1]
                payment = await db.get(Payment, payment_id)
                if (
                    not payment
                    or payment.user_id != user.id
                    or payment.provider not in {"mock", "stars_mock"}
                ):
                    raise ValueError("Payment ownership mismatch")
                await service.confirm(payment_id)
                await db.flush()
                job_id = await db.scalar(
                    select(ProvisioningJob.id).where(ProvisioningJob.payment_id == payment_id)
                )
                text, markup = ru.PENDING, keyboard(("📊 Моя подписка", "subscription"))
            elif data == "trial":
                from app.services.trial import activate_trial

                job_id = await activate_trial(db, user.id)
                text = ru.PENDING
            elif data == "subscription":
                if not sub:
                    text, markup = ru.NO_SUB, keyboard(("💎 Тарифы", "plans"))
                else:
                    plan = await db.get(Plan, sub.plan_id)
                    text = subscription_caption(
                        plan, sub, client, ru.STATUSES.get(sub.status, sub.status)
                    )
                    markup = keyboard(
                        ("🚀 Подключить", "connect"),
                        ("💎 Продлить", "plans"),
                        ("🔗 Ссылка", "link"),
                        ("📷 QR", "qr"),
                        ("Обновить ссылку", "rotate"),
                        ("Отключить ссылку", "revoke-confirm"),
                        ("⌂ Главное меню", "menu"),
                    )
            elif data == "devices":
                text = (
                    (
                        "<b>Твои устройства</b>\n\n"
                        + devices_label(sub.device_limit, sub.unlimited_devices)
                        + "\n\niPhone · Android · Windows · macOS\n"
                        "Подключение - только через Hiddify.\n"
                        "Открой инструкцию для своего устройства ниже."
                    )
                    if sub
                    else ru.NO_SUB
                )
                markup = keyboard(("Подключить устройство", "connect"), ("Главное меню", "menu"))
            elif data == "connect":
                text, markup = (
                    ru.DEVICES,
                    keyboard(
                        ("iPhone / iPad", "device:ios"),
                        ("Android", "device:android"),
                        ("Windows", "device:windows"),
                        ("macOS", "device:macos"),
                        ("Главное меню", "menu"),
                    ),
                )
            elif data.startswith("device:"):
                from aiogram.types import InlineKeyboardButton

                device = data.split(":", 1)[1]
                names = {
                    "ios": "iPhone / iPad",
                    "android": "Android",
                    "windows": "Windows",
                    "macos": "macOS",
                }
                text = ru.INSTRUCTION.format(device=names[device])
                markup = keyboard(
                    ("🔗 Получить ссылку", "link"),
                    ("📷 QR", "qr"),
                    ("✅ Я подключился", "connected"),
                    ("Назад", "connect"),
                )
                if device == "windows":
                    markup.inline_keyboard.insert(
                        0, [button("Скачать установщик 📦", "download:windows")]
                    )
                elif device == "macos":
                    markup.inline_keyboard.insert(
                        0,
                        [
                            InlineKeyboardButton(
                                text="📲 Установить Hiddify",
                                url=ru.HIDDIFY_DOWNLOAD_URLS[device],
                            )
                        ],
                    )
            elif data in {"link", "qr", "rotate"}:
                if not client or not client.enabled:
                    text = ru.NO_SUB
                else:
                    if data == "rotate":
                        await service.rotate_token(user.id)
                    if not client.token_encrypted:
                        text = ru.REVOKED
                    else:
                        url = (
                            settings.public_base_url
                            + "/sub/"
                            + vault.reveal(client.token_encrypted)
                        )
                        text = ru.LINK.format(url=url)
                        if settings.xui_mock_mode:
                            text += "\n\n" + ru.ACCESS_NOTICE
                        if data == "qr":
                            stream = io.BytesIO()
                            qrcode.make(url).save(stream, format="PNG")
                            await call.message.answer_photo(
                                BufferedInputFile(stream.getvalue(), filename="aera.png"),
                                caption=ru.CLIENT_NOTE
                                + ("\n\n" + ru.ACCESS_NOTICE if settings.xui_mock_mode else ""),
                            )
                            return
            elif data == "revoke-confirm":
                text, markup = (
                    "Отключить текущую ссылку?",
                    keyboard(("Да, отключить", "revoke"), ("Назад", "subscription")),
                )
            elif data == "revoke":
                await service.rotate_token(user.id, revoke=True)
                text = ru.REVOKED
            elif data == "connected":
                text = ru.CONNECTED
            elif data == "support":
                text, markup = (
                    ru.SUPPORT_CATEGORY,
                    keyboard(
                        *[(name, f"support:{index}") for index, name in enumerate(ru.CATEGORIES)],
                        ("Назад", "menu"),
                    ),
                )
            elif data.startswith("support:"):
                await state.set_state(SupportState.message)
                await state.update_data(category=ru.CATEGORIES[int(data.split(":")[1])])
                text = ru.SUPPORT
            elif data == "referral":
                text = ru.REFERRAL.format(
                    url=f"https://t.me/{settings.bot_username}?start=ref_{user.referral_code}"
                )
        if data.startswith("pay:") or data == "trial":
            if await provisioner.run(job_id, notify=False):
                text, markup = (
                    ru.READY if settings.xui_mock_mode else ru.READY_REAL,
                    keyboard(
                        ("🚀 Подключить устройство", "connect"), ("📊 Моя подписка", "subscription")
                    ),
                )
        await edit_screen(call.message, text, reply_markup=markup, art=art)
    except Exception as error:
        logging.getLogger("aera").error("bot_operation_failed type=%s", type(error).__name__)
        await call.message.answer(ru.ERROR, reply_markup=menu())


@lru_cache
def create_dispatcher() -> Dispatcher:
    from app.bot.admin import router as admin_router
    from app.bot.checkout import router as checkout_router
    from app.bot.middleware import GuardMiddleware
    from app.bot.payments import router as payment_router
    from app.bot.portal import router as portal_router
    from app.bot.portal_admin import router as portal_admin_router
    from app.bot.web_login import router as web_login_router

    dispatcher = Dispatcher(storage=RedisStorage.from_url(settings.redis_url))
    guard = GuardMiddleware()
    dispatcher.message.outer_middleware(guard)
    dispatcher.callback_query.outer_middleware(guard)
    dispatcher.include_router(web_login_router)
    dispatcher.include_router(payment_router)
    dispatcher.include_router(portal_router)
    dispatcher.include_router(checkout_router)
    dispatcher.include_router(portal_admin_router)
    dispatcher.include_router(admin_router)
    dispatcher.include_router(router)
    return dispatcher


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("Set BOT_TOKEN in .env to start Telegram polling")
    bot = create_bot(settings)
    try:
        await create_dispatcher().start_polling(bot)
    finally:
        await bot.session.close()
        if hasattr(adapter, "close"):
            await adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
