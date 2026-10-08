"""Telegram Stars receipts: one apply path for live receipts and reconciliation.

Telegram delivers `successful_payment` once. Under polling the update is confirmed before the
handler runs, so a crash or a database error at that moment loses it, and Telegram drops
undelivered updates after 24 hours. `reconcile` recovers such payments from the bot's Star
transaction history.
"""

import logging
from datetime import UTC, datetime, timedelta

from aiogram.types import TransactionPartnerUser
from sqlalchemy import select

from app.core.security import TokenVault
from app.db.models import AppSetting, PaidLink, Payment, SaleOrder, User
from app.services.commerce import CommerceService, utc
from app.services.notifications import alert_admins, enqueue

PAGE = 100
MAX_PAGES = 50
LOOKBACK = timedelta(days=30)
CURSOR_KEY = "stars_transactions_offset"


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
    if payment.details.get("manual_order") and not await db.scalar(
        select(SaleOrder.id).where(SaleOrder.payment_id == payment.id)
    ):
        raise ValueError("Manual order missing")
    return payment


async def apply_receipt(db, settings, payment_id, telegram_id, amount, currency, charge_id):
    """Confirm a Stars payment; safe to repeat for the same charge.

    Returns (payment, manual order id or None).
    """
    payment = await validate_invoice(db, payment_id, telegram_id, amount, currency)
    if payment.status == "PAID" and payment.provider_payment_id != charge_id:
        raise ValueError("Charge identity mismatch")
    manual = await db.scalar(select(SaleOrder.id).where(SaleOrder.payment_id == payment.id))
    if manual:
        from app.services.portal import confirm_stars

        await confirm_stars(db, payment, charge_id, settings)
    else:
        payment.provider_payment_id = charge_id
        await CommerceService(db, TokenVault(settings.app_secret)).confirm(payment.id)
    await db.flush()
    return payment, manual


async def _fetch(sessions, bot, since):
    """Star transactions newer than `since`, whichever direction Telegram orders them."""
    first = (await bot.get_star_transactions(offset=0, limit=PAGE)).transactions
    if len(first) < PAGE:
        return first
    if first[0].date > first[-1].date:  # newest first: read until older than `since`
        collected, offset = list(first), PAGE
        for _ in range(MAX_PAGES - 1):
            if utc(collected[-1].date) < since:
                break
            page = (await bot.get_star_transactions(offset=offset, limit=PAGE)).transactions
            collected += page
            offset += PAGE
            if len(page) < PAGE:
                break
        return collected
    # Oldest first: resume from the last position, re-reading one page for overlap.
    async with sessions() as db:
        stored = await db.get(AppSetting, CURSOR_KEY)
    start = max(int(stored.value) - PAGE if stored else 0, 0)
    collected, offset = [], start
    for _ in range(MAX_PAGES):
        page = (await bot.get_star_transactions(offset=offset, limit=PAGE)).transactions
        collected += page
        offset += len(page)
        if len(page) < PAGE:
            break
    async with sessions.begin() as db:
        from app.services.settings import set_setting

        await set_setting(db, CURSOR_KEY, str(offset))
    return collected


async def reconcile(sessions, bot, settings) -> int:
    """Apply Stars payments Telegram has but we never recorded. Returns how many were applied."""
    cutoff = datetime.now(UTC) - LOOKBACK
    async with sessions() as db:
        candidates = {
            payment.id: payment
            for payment in await db.scalars(
                select(Payment).where(
                    Payment.provider == "telegram_stars",
                    Payment.status.in_(["PENDING", "CANCELLED"]),
                    Payment.created_at >= cutoff,
                )
            )
        }
    if not candidates:
        return 0
    since = min(utc(p.created_at) for p in candidates.values()) - timedelta(days=1)
    transactions = await _fetch(sessions, bot, since)
    refunded = {t.id for t in transactions if isinstance(t.receiver, TransactionPartnerUser)}
    applied = 0
    for transaction in transactions:
        source = transaction.source
        if (
            not isinstance(source, TransactionPartnerUser)
            or source.transaction_type != "invoice_payment"
            or source.invoice_payload not in candidates
            or transaction.id in refunded
        ):
            continue
        payment = candidates.pop(source.invoice_payload)
        if payment.status == "CANCELLED":
            async with sessions.begin() as db:
                await alert_admins(
                    db,
                    settings,
                    f"stars-cancelled-paid:{transaction.id}",
                    "⚠️ Получена оплата Stars за отменённый заказ. Нужен возврат.\n"
                    f"Платёж: {payment.id}\nTelegram ID: {source.user.id}\n"
                    f"Сумма: {transaction.amount} ⭐\nID списания: {transaction.id}",
                )
            continue
        try:
            async with sessions.begin() as db:
                payment, manual = await apply_receipt(
                    db,
                    settings,
                    payment.id,
                    source.user.id,
                    transaction.amount,
                    "XTR",
                    transaction.id,
                )
                user = await db.get(User, payment.user_id)
                if manual:
                    from app.bot.texts.portal import text

                    issued = await db.scalar(
                        select(PaidLink.id).where(
                            PaidLink.order_id == manual, PaidLink.issued_at.is_not(None)
                        )
                    )
                    await enqueue(
                        db,
                        "stars-recovered:" + payment.id,
                        user.id,
                        "PORTAL",
                        text("paid_ready" if issued else "paid", user.language_code),
                    )
            applied += 1
            logging.getLogger("aera").warning("stars_payment_recovered")
        except Exception as error:
            logging.getLogger("aera").error("stars_recovery_failed type=%s", type(error).__name__)
            async with sessions.begin() as db:
                await alert_admins(
                    db,
                    settings,
                    f"stars-unapplied:{transaction.id}",
                    "⚠️ Оплата Stars найдена в Telegram, но не проведена автоматически.\n"
                    f"Платёж: {payment.id}\nTelegram ID: {source.user.id}\n"
                    f"Сумма: {transaction.amount} ⭐\nID списания: {transaction.id}",
                )
    return applied
