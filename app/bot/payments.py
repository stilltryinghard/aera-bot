import logging

from aiogram import F, Router
from aiogram.types import Message, PreCheckoutQuery
from sqlalchemy import select, update

from app.bot.texts import ru
from app.config import get_settings
from app.db.models import Payment, SaleOrder, User
from app.db.session import sessions

router = Router()


async def validate_invoice(
    db, payment_id: str, telegram_id: int, amount: int, currency: str
) -> Payment:
    payment = await db.get(Payment, payment_id)
    user = await db.get(User, payment.user_id) if payment else None
    if (
        not payment
        or not user
        or user.telegram_id != telegram_id
        or payment.provider != "telegram_stars"
        or currency != payment.currency
        or amount != payment.amount_minor
        or payment.status not in {"PENDING", "PAID"}
    ):
        raise ValueError("Invoice mismatch")
    if not await db.scalar(select(SaleOrder.id).where(SaleOrder.payment_id == payment.id)):
        raise ValueError("Manual order missing")
    return payment


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    try:
        async with sessions.begin() as db:
            await db.execute(
                update(User)
                .where(User.telegram_id == query.from_user.id)
                .values(is_active=User.is_active)
            )
            payment = await validate_invoice(
                db, query.invoice_payload, query.from_user.id, query.total_amount, query.currency
            )
            user = await db.get(User, payment.user_id)
            if user.is_blocked or not user.is_active or payment.status != "PENDING":
                raise ValueError("Payment unavailable")
            order = await db.scalar(select(SaleOrder).where(SaleOrder.payment_id == payment.id))
            if order:
                from app.db.models import SupportTicket

                ticket = await db.get(SupportTicket, order.ticket_id)
                if order.paid_at or not ticket or ticket.status not in {"OPEN", "IN_PROGRESS"}:
                    raise ValueError("Order already fulfilled")
                from app.services.payment_timeout import require_open
                await require_open(db, order)
                from app.services.paid_pool import available, enabled, reserve

                if await enabled(db):
                    if not await available(db, order) or await reserve(db, order) is None:
                        raise ValueError("Paid inventory unavailable")
                payment.details = {**payment.details, "precheckout_accepted": True}
    except Exception:
        await query.answer(ok=False, error_message=ru.ERROR)
    else:
        await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    settings = get_settings()
    receipt = message.successful_payment
    try:
        async with sessions.begin() as db:
            payment = await validate_invoice(
                db,
                receipt.invoice_payload,
                message.from_user.id,
                receipt.total_amount,
                receipt.currency,
            )
            if payment.status == "PAID" and (
                payment.provider_payment_id != receipt.telegram_payment_charge_id
            ):
                raise ValueError("Charge identity mismatch")
            manual = await db.scalar(select(SaleOrder.id).where(SaleOrder.payment_id == payment.id))
            if manual:
                from app.services.portal import confirm_stars

                await confirm_stars(db, payment, receipt.telegram_payment_charge_id, settings)
                user = await db.get(User, payment.user_id)
                lang = user.language_code
                from app.db.models import PaidLink

                issued = await db.scalar(
                    select(PaidLink.id).where(
                        PaidLink.order_id == manual, PaidLink.issued_at.is_not(None)
                    )
                )
            await db.flush()
        if manual:
            from app.bot.portal import home_menu
            from app.bot.texts.portal import text

            await message.answer(
                text("paid_ready" if issued else "paid", lang), reply_markup=home_menu(lang)
            )
            if issued:
                from app.services.notifications import send_notifications

                await send_notifications(
                    sessions,
                    message.bot,
                    keys={"paid-link:" + manual},
                    allowed_types={"ACCESS_READY"},
                )
            return
    except Exception as error:
        logging.getLogger("aera").error("stars_receipt_failed type=%s", type(error).__name__)
        # Fail the webhook so Telegram can redeliver a receipt rather than losing paid events.
        raise
