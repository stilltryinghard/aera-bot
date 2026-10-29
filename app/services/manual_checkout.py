"""Real Stars invoices for manual fulfillment; no mock payment or VPN provisioning."""

from sqlalchemy import update

from app.db.models import Payment, Plan, SaleOrder, SupportTicket, User
from app.services.settings import setting


async def stars_payment(db, order_id, user):
    await db.execute(update(User).where(User.id == user.id).values(is_active=User.is_active))
    order = await db.get(SaleOrder, order_id, populate_existing=True)
    ticket = await db.get(SupportTicket, order.ticket_id) if order else None
    if (
        not ticket
        or ticket.user_id != user.id
        or order.paid_at
    ):
        raise ValueError("Order unavailable")
    from app.services.payment_timeout import require_open
    await require_open(db, order)
    if ticket.status not in {"OPEN", "IN_PROGRESS"}:
        raise ValueError("Order unavailable")
    if await setting(db, "manual_stars_enabled", "false") != "true":
        return None
    from app.services.paid_pool import available

    if not await available(db, order):
        return None
    plan = await db.get(Plan, order.plan_id)
    if not plan.is_active or not plan.stars_price or not user.terms_accepted_at:
        raise ValueError("Stars invoice unavailable")
    if order.payment_id:
        payment = await db.get(Payment, order.payment_id)
        if payment.status == "PENDING" and payment.provider == "telegram_stars":
            return payment
        raise ValueError("Order already has a payment")
    payment = Payment(
        user_id=user.id,
        plan_id=plan.id,
        provider="telegram_stars",
        amount_minor=max(1, (plan.stars_price * (100 - order.discount_percent) + 99) // 100),
        currency="XTR",
        details={"manual_order": order.id},
    )
    db.add(payment)
    await db.flush()
    order.payment_id = payment.id
    return payment
