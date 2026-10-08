from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards import keyboard
from app.bot.presentation import edit_screen, period_label, price_label
from app.bot.texts import ru
from app.config import get_settings
from app.core.security import TokenVault
from app.db.models import CryptoInvoice, Payment, Plan
from app.db.session import sessions
from app.integrations.payments.telegram_stars import TelegramStarsProvider
from app.services.checkout import CheckoutService
from app.services.commerce import CommerceService
from app.services.settings import setting

router = Router()


def payment_methods(plan_id: str) -> InlineKeyboardMarkup:
    return keyboard(
        ("💳 Карта / СБП", f"method:card:{plan_id}"),
        ("Криптовалюта", f"method:crypto:{plan_id}"),
        ("⭐ Telegram Stars", f"method:stars:{plan_id}"),
        ("Назад", f"plan:{plan_id}"),
    )


@router.callback_query(
    F.data.startswith("checkout:")
    | F.data.startswith("method:")
    | F.data.startswith("agreeorder:")
    | F.data.startswith("checkorder:")
)
async def checkout(call: CallbackQuery) -> None:
    await call.answer()
    data = call.data.split(":")
    settings = get_settings()
    vault = TokenVault(settings.app_secret)
    if data[0] == "checkout":
        from html import escape

        async with sessions() as db:
            plan = await db.get(Plan, data[1])
            if not plan or not plan.is_active:
                raise ValueError("Tariff unavailable")
            caption = (
                ru.PAYMENT_METHODS + f"\n\n{escape(plan.name)}\n"
                f"{period_label(plan)} · {price_label(plan)}"
            )
        if settings.payment_provider == "mock":
            caption += "\n\nТестовая оплата: деньги не списываются."
        await edit_screen(call.message, caption, reply_markup=payment_methods(data[1]))
        return
    async with sessions.begin() as db:
        user = await CommerceService(db, vault).user(call.from_user.id)
        if data[0] == "checkorder":
            payment = await db.get(Payment, data[1])
            if not payment or payment.user_id != user.id:
                raise ValueError("Payment ownership mismatch")
        elif settings.payment_provider != "mock":
            version = await setting(db, "terms_version", "1")
            text = await setting(db, "terms_text", "")
            if not text:
                await edit_screen(call.message, ru.TERMS_PENDING)
                return
            if data[0] == "agreeorder":
                user.terms_accepted_at, user.terms_version = datetime.now(UTC), version
            if not user.terms_accepted_at or user.terms_version != version:
                await edit_screen(
                    call.message,
                    text,
                    reply_markup=keyboard(
                        ("Прочитал и согласен", f"agreeorder:{data[1]}:{data[2]}"),
                        ("Назад", f"checkout:{data[2]}"),
                    ),
                )
                return
    service = CheckoutService(sessions, settings, vault)
    if data[0] == "checkorder":
        status = await service.reconcile(payment.id)
        await edit_screen(
            call.message,
            ru.EXTERNAL_PAYMENT_STATUS.get(status, ru.ERROR),
            reply_markup=keyboard(("📊 Моя подписка", "subscription")),
        )
        return
    method, plan_id = data[1], data[2]
    payment, url = await service.create(user.id, plan_id, method)
    if payment.provider == "telegram_stars":
        url = await TelegramStarsProvider(call.bot).create_payment(
            payment.id, payment.amount_minor, payment.currency
        )
    if url:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Оплатить у провайдера", url=url)],
                [
                    InlineKeyboardButton(
                        text="Проверить оплату", callback_data=f"checkorder:{payment.id}"
                    )
                ],
                [InlineKeyboardButton(text="Назад", callback_data=f"checkout:{plan_id}")],
            ]
        )
        if payment.provider == "telegram_stars":
            markup.inline_keyboard.pop(1)
        await edit_screen(call.message, ru.EXTERNAL_CHECKOUT, reply_markup=markup)
    elif payment.provider == "crypto_mock":
        async with sessions() as db:
            invoice = await db.get(CryptoInvoice, payment.details["invoice_id"])
        await edit_screen(
            call.message,
            ru.CRYPTO_INVOICE.format(
                amount=invoice.expected_amount,
                asset=invoice.asset,
                network=invoice.network,
                expiry=invoice.expires_at.strftime("%H:%M UTC"),
            ),
            reply_markup=keyboard(
                ("Проверить оплату", f"crypto-check:{invoice.id}"),
                ("Назад", f"checkout:{plan_id}"),
            ),
        )
    else:
        await edit_screen(
            call.message,
            ru.MOCK_PAY.format(name="AERA"),
            reply_markup=keyboard(
                ("Подтвердить тестовую оплату", f"pay:{payment.id}"),
                ("Назад", f"checkout:{plan_id}"),
            ),
        )
