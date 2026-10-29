"""Manual customer outcomes and permanent deletion of bot conversations.

CONNECTED tariff tickets are the customer register; no VPN API is involved.
"""

from datetime import UTC, datetime

from sqlalchemy import delete, exists, or_, select, text, update

from app.db.models import (
    AuditLog,
    BroadcastDelivery,
    FamilyAccess,
    ManualAccess,
    Notification,
    PaidLink,
    Payment,
    Referral,
    ReferralCoupon,
    SaleOrder,
    Subscription,
    SupportMessage,
    SupportTicket,
    TrialLink,
    User,
)
from app.services.requests import REQUEST_CATEGORY


class DeletionBusy(ValueError):
    pass


def customer_condition():
    return exists().where(
        SupportTicket.user_id == User.id,
        SupportTicket.category == REQUEST_CATEGORY,
        SupportTicket.status == "CONNECTED",
    )


async def lock_ticket(db, ticket_id):
    ticket = await db.get(SupportTicket, ticket_id)
    if ticket is None:
        return None
    # A write also serializes this customer's callbacks on SQLite, where FOR UPDATE
    # is ignored. Re-read the ticket after acquiring the customer lock.
    result = await db.execute(
        update(User).where(User.id == ticket.user_id).values(is_active=User.is_active)
    )
    if not result.rowcount:
        return None
    return await db.scalar(
        select(SupportTicket)
        .where(SupportTicket.id == ticket_id)
        .execution_options(populate_existing=True)
    )


async def mark_connected(db, ticket_id):
    ticket = await lock_ticket(db, ticket_id)
    if not ticket or ticket.category != REQUEST_CATEGORY:
        return False
    if ticket.status == "CONNECTED":
        return True
    ticket.status, ticket.closed_at = "CONNECTED", datetime.now(UTC)
    from app.services.portal import connected_access

    await connected_access(db, ticket)
    return True


async def purge_tickets(db, ticket_ids, *, user_id=None):
    if db.bind.dialect.name == "sqlite":
        await db.execute(text("PRAGMA secure_delete=ON"))
    message_ids = list(
        await db.scalars(select(SupportMessage.id).where(SupportMessage.ticket_id.in_(ticket_ids)))
    )
    clauses = [Notification.dedupe_key.in_([f"support:{mid}" for mid in message_ids])]
    for tid in ticket_ids:
        clauses.extend(
            [
                Notification.dedupe_key.startswith(f"request:{tid}:"),
                Notification.dedupe_key.startswith(f"ticket:{tid}:"),
            ]
        )
    if user_id:
        clauses.append(Notification.user_id == user_id)
    condition = or_(*clauses)
    notices = list(await db.scalars(select(Notification).where(condition).with_for_update()))
    if any(n.status == "SENDING" for n in notices):
        raise DeletionBusy("Сообщение ещё отправляется. Повтори удаление через несколько секунд.")
    if any(
        n.notification_type == "SUPPORT" and n.status == "PENDING" and n.attempts < 10
        for n in notices
    ):
        raise DeletionBusy("Ответ ещё не доставлен. Повтори удаление после доставки ответа.")
    orders = list(await db.scalars(select(SaleOrder).where(SaleOrder.ticket_id.in_(ticket_ids))))
    for order in orders:
        if order.paid_at:
            raise DeletionBusy(
                "Оплата подтверждена. Заявку нельзя удалять как отказ без урегулирования оплаты."
            )
        if order.payment_id:
            payment = await db.get(Payment, order.payment_id)
            from app.services.orders import verified_cancellation

            if payment and (
                payment.status not in {"CANCELLED", "FAILED"}
                or (payment.status == "CANCELLED" and not verified_cancellation(payment))
            ):
                raise DeletionBusy(
                    "По заявке выставлен действующий платёжный счёт. "
                    "Сначала нужно урегулировать его, чтобы не потерять подтверждение оплаты."
                )
    # Validate every order before deleting anything: callers handle DeletionBusy
    # inside their transaction, so earlier deletions would otherwise be committed.
    await db.execute(delete(Notification).where(condition))
    await db.execute(delete(AuditLog).where(AuditLog.entity_id.in_([*ticket_ids, *message_ids])))
    for order in orders:
        if order.coupon_id:
            coupon = await db.get(ReferralCoupon, order.coupon_id)
            if coupon and coupon.status == "RESERVED":
                coupon.status, coupon.reserved_ticket_id = "AVAILABLE", None
    await db.flush()
    await db.execute(
        update(PaidLink)
        .where(PaidLink.order_id.in_([order.id for order in orders]), PaidLink.issued_at.is_(None))
        .values(order_id=None, user_id=None, status="UNVERIFIED")
    )
    await db.execute(delete(SaleOrder).where(SaleOrder.ticket_id.in_(ticket_ids)))
    await db.execute(delete(SupportMessage).where(SupportMessage.ticket_id.in_(ticket_ids)))
    await db.execute(delete(SupportTicket).where(SupportTicket.id.in_(ticket_ids)))


async def delete_support(db, ticket_id):
    ticket = await lock_ticket(db, ticket_id)
    if not ticket or ticket.category == REQUEST_CATEGORY:
        return False
    await purge_tickets(db, [ticket.id])
    return True


async def refuse_request(db, ticket_id, admin_ids):
    """Forget a new applicant, retaining accounts with existing access or owner roles.

    Returns (outcome, Telegram ID to clear from volatile bot storage).
    """
    ticket = await lock_ticket(db, ticket_id)
    if not ticket or ticket.category != REQUEST_CATEGORY:
        return "missing", None
    if ticket.status == "CONNECTED":
        return "connected", None
    user = await db.get(User, ticket.user_id)
    # Never-authorized Stars invoices can be invalidated safely. Subsequent
    # pre-checkout fails, while authorized payments remain protected below.
    order = await db.scalar(select(SaleOrder).where(SaleOrder.ticket_id == ticket.id))
    if order and not order.paid_at and order.payment_id:
        payment = await db.get(Payment, order.payment_id)
        reserved = await db.scalar(select(PaidLink.id).where(PaidLink.order_id == order.id))
        if (
            payment
            and payment.status == "PENDING"
            and payment.provider == "telegram_stars"
            and payment.details.get("manual_order") == order.id
            and not payment.details.get("precheckout_accepted")
            and not reserved
        ):
            order.payment_id = None
            await db.flush()
            await db.delete(payment)
            await db.flush()
    registered = bool(
        await db.scalar(select(User.id).where(User.id == user.id, customer_condition()))
    )
    has_access = bool(
        await db.scalar(select(Subscription.id).where(Subscription.user_id == user.id))
        or await db.scalar(select(Payment.id).where(Payment.user_id == user.id))
        or await db.scalar(select(FamilyAccess.id).where(FamilyAccess.user_id == user.id))
        or await db.scalar(select(ManualAccess.id).where(ManualAccess.user_id == user.id))
    )
    if user.telegram_id in admin_ids or registered or has_access:
        await purge_tickets(db, [ticket.id])
        return "request_only", None
    ticket_ids = list(
        await db.scalars(select(SupportTicket.id).where(SupportTicket.user_id == user.id))
    )
    telegram_id, user_id = user.telegram_id, user.id
    await purge_tickets(db, ticket_ids, user_id=user_id)
    await db.execute(delete(BroadcastDelivery).where(BroadcastDelivery.user_id == user_id))
    # An issued key is retired, even if the prospect is forgotten. Never reassign it.
    await db.execute(
        update(TrialLink).where(TrialLink.user_id == user_id).values(user_id=None, status="RETIRED")
    )
    referral_ids = select(Referral.id).where(
        or_(Referral.referrer_user_id == user_id, Referral.referred_user_id == user_id)
    )
    await db.execute(
        update(SaleOrder)
        .where(
            SaleOrder.coupon_id.in_(
                select(ReferralCoupon.id).where(ReferralCoupon.referral_id.in_(referral_ids))
            )
        )
        .values(coupon_id=None)
    )
    await db.execute(delete(ReferralCoupon).where(ReferralCoupon.referral_id.in_(referral_ids)))
    await db.execute(
        delete(Referral).where(
            or_(Referral.referrer_user_id == user_id, Referral.referred_user_id == user_id)
        )
    )
    await db.execute(update(User).where(User.referred_by_id == user_id).values(referred_by_id=None))
    await db.execute(delete(AuditLog).where(AuditLog.entity_id == user_id))
    await db.execute(delete(User).where(User.id == user_id))
    return "forgotten", telegram_id
