import logging

from aiogram import F, Router
from aiogram.types import Message, PreCheckoutQuery
from sqlalchemy import select, update

from app.bot.keyboards import keyboard
from app.bot.texts import ru
from app.config import get_settings
from app.db.models import ProvisioningJob, SaleOrder, User
from app.db.session import sessions
from app.services.stars import apply_receipt, validate_invoice

router = Router()


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
            payment, manual = await apply_receipt(
                db,
                settings,
                receipt.invoice_payload,
                message.from_user.id,
                receipt.total_amount,
                receipt.currency,
                receipt.telegram_payment_charge_id,
            )
            if manual:
                user = await db.get(User, payment.user_id)
                lang = user.language_code
                from app.db.models import PaidLink

                issued = await db.scalar(
                    select(PaidLink.id).where(
                        PaidLink.order_id == manual, PaidLink.issued_at.is_not(None)
                    )
                )
            job_id = await db.scalar(
                select(ProvisioningJob.id).where(ProvisioningJob.payment_id == payment.id)
            )
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
        from app.bot.runtime import provisioner

        ready = await provisioner.run(job_id, notify=False)
        await message.answer(
            (ru.READY if settings.xui_mock_mode else ru.READY_REAL) if ready else ru.PENDING,
            reply_markup=keyboard(
                ("🚀 Подключить", "connect"), ("📊 Моя подписка", "subscription")
            ),
        )
    except Exception as error:
        logging.getLogger("aera").error("stars_receipt_failed type=%s", type(error).__name__)
        # In webhook mode the error makes Telegram redeliver. Under polling the update is
        # already confirmed; the worker's Stars reconciliation recovers the payment.
        raise
