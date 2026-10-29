"""Cancel only a definitively unpaid order; preserve external payment recovery."""

from datetime import UTC, datetime, timedelta
from sqlalchemy import update
from app.db.models import Payment, ReferralCoupon, SupportTicket, User


def verified_cancellation(payment, now=None):
    if not payment or payment.status != "CANCELLED":
        return False
    if payment.provider == "telegram_stars":
        return not payment.details.get("precheckout_accepted")
    if payment.provider != "platega" or payment.details.get("provider_status") not in {
        "CANCELED",
        "CANCELLED",
    }:
        return False
    try:
        checked = datetime.fromisoformat(payment.details["provider_checked_at"])
        return timedelta(0) <= (now or datetime.now(UTC)) - checked <= timedelta(minutes=2)
    except (KeyError, TypeError, ValueError):
        return False


async def close_cancelled_order(db, order):
    if order.paid_at:
        raise ValueError("A paid order cannot be cancelled")
    payment = await db.get(Payment, order.payment_id) if order.payment_id else None
    if payment and payment.status != "CANCELLED":
        raise ValueError("Payment cancellation is not confirmed")
    ticket = await db.get(SupportTicket, order.ticket_id)
    if ticket.status not in {"OPEN", "IN_PROGRESS", "CLOSED"}:
        raise ValueError("Order is already fulfilled")
    ticket.status, ticket.closed_at = "CLOSED", datetime.now(UTC)
    if order.coupon_id:
        coupon = await db.get(ReferralCoupon, order.coupon_id)
        if coupon and coupon.status == "RESERVED" and coupon.reserved_ticket_id == ticket.id:
            coupon.status, coupon.reserved_ticket_id = "AVAILABLE", None


async def cancel_unpaid_order(db, order, user):
    await db.execute(update(User).where(User.id == user.id).values(is_active=User.is_active))
    await db.refresh(order)
    ticket = await db.get(SupportTicket, order.ticket_id, populate_existing=True)
    if not ticket or ticket.user_id != user.id:
        raise ValueError("Order ownership mismatch")
    payment = (
        await db.get(Payment, order.payment_id, populate_existing=True)
        if order.payment_id
        else None
    )
    if order.paid_at:
        return False
    if payment:
        if payment.status in {"PAID", "CHARGEBACKED", "REFUNDED"}:
            return False
        if payment.provider == "telegram_stars":
            if payment.details.get("precheckout_accepted"):
                return False
        elif not verified_cancellation(payment):
            # A hosted invoice may still be paid even after its Telegram message is closed.
            return False
        if payment.status == "PENDING":
            payment.status = "CANCELLED"
    await close_cancelled_order(db, order)
    return True
