"""Expire checkout UI, retaining external receipts and possibly-paid reserves."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db.models import Payment, ReferralCoupon, SaleOrder, SupportTicket, User

WINDOW = timedelta(minutes=15)


class PaymentWindowExpired(ValueError):
    pass


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def elapsed(order, payment=None, now=None):
    if order.paid_at or (payment and (payment.paid_at or payment.status in {
        "PAID", "CHARGEBACKED", "REFUNDED"
    })):
        return False
    # updated_at is changed by polling; only the immutable creation time counts.
    started = payment.created_at if payment else order.created_at
    return utc(now or datetime.now(UTC)) >= utc(started) + WINDOW


async def require_open(db, order, now=None):
    ticket = await db.get(SupportTicket, order.ticket_id, populate_existing=True)
    payment = await db.get(Payment, order.payment_id, populate_existing=True) if order.payment_id else None
    if not order.paid_at and (ticket.status == "CLOSED" or elapsed(order, payment, now)):
        raise PaymentWindowExpired("Срок оплаты истёк. Выберите подписку и способ оплаты заново.")


async def expire_open_orders(db, settings=None, *, user_id=None, now=None):
    now = utc(now or datetime.now(UTC))
    query = (select(SaleOrder.id, SupportTicket.user_id)
             .join(SupportTicket, SupportTicket.id == SaleOrder.ticket_id)
             .where(SupportTicket.category == "TARIFF_REQUEST",
                    SupportTicket.status.in_(["OPEN", "IN_PROGRESS"]),
                    SaleOrder.paid_at.is_(None), SaleOrder.created_at <= now - WINDOW))
    if user_id:
        query = query.where(SupportTicket.user_id == user_id)
    candidates = list(await db.execute(query.order_by(SaleOrder.created_at).limit(200)))
    changed = 0
    for identity, owner in candidates:
        # Same per-user serialization as receipt and checkout paths.
        await db.execute(update(User).where(User.id == owner).values(is_active=User.is_active))
        order = await db.get(SaleOrder, identity, populate_existing=True)
        ticket = await db.get(SupportTicket, order.ticket_id, populate_existing=True)
        payment = await db.get(Payment, order.payment_id, populate_existing=True) if order.payment_id else None
        if ticket.status not in {"OPEN", "IN_PROGRESS"} or not elapsed(order, payment, now):
            continue
        ticket.status, ticket.closed_at = "CLOSED", now
        recoverable = bool(payment and (
            payment.provider == "platega" or payment.details.get("precheckout_accepted")
        ))
        if payment:
            details = dict(payment.details)
            details.setdefault("checkout_expired_at", now.isoformat())
            if payment.provider == "telegram_stars" and payment.status == "PENDING" and not recoverable:
                payment.status = "CANCELLED"
            payment.details = details
        # Never reuse a coupon or stock reserved against an externally payable invoice.
        # Existing observer releases stock only after verified provider cancellation.
        if order.coupon_id and not recoverable:
            coupon = await db.get(ReferralCoupon, order.coupon_id)
            if coupon and coupon.status == "RESERVED" and coupon.reserved_ticket_id == ticket.id:
                coupon.status, coupon.reserved_ticket_id = "AVAILABLE", None
        changed += 1
    await db.flush()
    return changed
