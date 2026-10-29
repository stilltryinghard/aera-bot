"""Paid manual access and referral discounts; never provisions or modifies VPN."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db.models import (
    ManualAccess,
    Plan,
    Referral,
    ReferralCoupon,
    SaleOrder,
    SupportMessage,
    SupportTicket,
    User,
)
from app.services.commerce import add_months, utc
from app.services.notifications import enqueue


def discount_percent(amount_minor):
    return 50 if amount_minor <= 50000 else 25 if amount_minor <= 100000 else 0


async def ensure_order(db, ticket, plan):
    if not plan.is_active or plan.price_minor > 1000000:
        raise ValueError("Этот тариф недоступен для новой покупки. Лимит заказа — 10 000 ₽.")
    order = await db.scalar(select(SaleOrder).where(SaleOrder.ticket_id == ticket.id))
    if order:
        return order
    await db.execute(update(User).where(User.id == ticket.user_id).values(is_active=User.is_active))
    order = await db.scalar(select(SaleOrder).where(SaleOrder.ticket_id == ticket.id))
    if order:
        return order
    percent = 0 if plan.slug.startswith("devices-") else discount_percent(plan.price_minor)
    coupon = (
        await db.scalar(
            select(ReferralCoupon)
            .where(ReferralCoupon.user_id == ticket.user_id, ReferralCoupon.status == "AVAILABLE")
            .order_by(ReferralCoupon.created_at)
            .limit(1)
            .with_for_update()
        )
        if percent
        else None
    )
    order = SaleOrder(
        ticket_id=ticket.id,
        plan_id=plan.id,
        amount_rub_minor=plan.price_minor * (100 - percent) // 100 if coupon else plan.price_minor,
        discount_percent=percent if coupon else 0,
        coupon_id=coupon.id if coupon else None,
    )
    db.add(order)
    if coupon:
        coupon.status, coupon.reserved_ticket_id = "RESERVED", ticket.id
    await db.flush()
    return order


async def confirm_order(db, order, settings):
    ticket = await db.get(SupportTicket, order.ticket_id)
    await db.execute(update(User).where(User.id == ticket.user_id).values(is_active=User.is_active))
    await db.refresh(order)
    if order.paid_at:
        return
    order.paid_at = datetime.now(UTC)
    if order.coupon_id:
        coupon = await db.get(ReferralCoupon, order.coupon_id)
        coupon.status = "USED"
    from app.services.affiliates import accrue_first
    await accrue_first(db, order, settings)
    user = await db.get(User, ticket.user_id)
    from app.services.paid_pool import fulfill

    automatic = await fulfill(db, order, settings)
    from app.services.renewals import intent

    renewal = await intent(db, order)
    if renewal:
        await enqueue(
            db,
            "renewal-payment:" + order.id,
            user.id,
            "PORTAL",
            "Оплата продления подтверждена. Обновляем срок вашей подписки; ссылка останется прежней."
            if user.language_code == "ru"
            else "Renewal payment confirmed. Updating your expiry; your link will stay the same.",
        )
        for admin_id in settings.admin_ids:
            admin = await db.scalar(select(User).where(User.telegram_id == admin_id))
            if admin:
                await enqueue(
                    db,
                    f"sale-paid:{order.id}:{admin_id}",
                    admin.id,
                    "PORTAL",
                    f"Оплачено продление подписки. Клиент ID {user.telegram_id}. Срок обновится после проверки панели.",
                )
        return
    for admin_id in settings.admin_ids:
        admin = await db.scalar(select(User).where(User.telegram_id == admin_id))
        if admin:
            await enqueue(
                db,
                f"sale-paid:{order.id}:{admin_id}",
                admin.id,
                "PORTAL",
                f"💳 Оплата подтверждена\nПокупка № {ticket.id[:8]}\n"
                f"Клиент: {user.first_name or user.telegram_id}\n"
                f"@{user.username or '-'}\nСумма: {order.amount_rub_minor / 100:g} ₽\n"
                "✅ Личная ссылка поставлена в автоматическую отправку.\n"
                "👥 Клиент доступен в «Наших пользователях»."
                if automatic
                else f"💳 Оплата подтверждена\nПокупка № {ticket.id[:8]}\n"
                f"Клиент: {user.first_name or user.telegram_id}\n"
                f"@{user.username or '-'}\nСумма: {order.amount_rub_minor / 100:g} ₽\n"
                "⏳ Ссылка ещё не выдана. Проверь запас платных ссылок в /admin; "
                "бот повторит выдачу автоматически.",
            )


async def connected_access(db, ticket):
    order = await db.scalar(select(SaleOrder).where(SaleOrder.ticket_id == ticket.id))
    if order is None:
        plan_id = await db.scalar(
            select(SupportMessage.sender_id)
            .where(
                SupportMessage.ticket_id == ticket.id,
                SupportMessage.sender_type == "TARIFF_SELECTION",
            )
            .limit(1)
        )
        plan = await db.get(Plan, plan_id) if plan_id else None
        if plan is None:
            return
        order = await ensure_order(db, ticket, plan)
    plan = await db.get(Plan, order.plan_id)
    access = await db.scalar(select(ManualAccess).where(ManualAccess.user_id == ticket.user_id))
    start = utc(ticket.closed_at or datetime.now(UTC))
    base = max(utc(access.expires_at), start) if access else start
    expiry = (
        add_months(base, plan.duration_months)
        if plan.duration_months
        else base + timedelta(days=plan.duration_days)
    )
    if access:
        access.expires_at, access.plan_id = expiry, plan.id
    else:
        db.add(
            ManualAccess(
                user_id=ticket.user_id, plan_id=plan.id, started_at=start, expires_at=expiry
            )
        )


async def backfill_access(db):
    users = await db.scalars(
        select(User).where(~select(ManualAccess.id).where(ManualAccess.user_id == User.id).exists())
    )
    for user in users:
        ticket = await db.scalar(
            select(SupportTicket)
            .where(
                SupportTicket.user_id == user.id,
                SupportTicket.status == "CONNECTED",
                SupportTicket.category == "TARIFF_REQUEST",
            )
            .order_by(SupportTicket.closed_at.desc())
            .limit(1)
        )
        if ticket:
            from app.services.paid_pool import latest

            if await latest(db, user.id) is None:
                await connected_access(db, ticket)


async def confirm_stars(db, payment, charge_id, settings):
    await db.execute(
        update(User).where(User.id == payment.user_id).values(is_active=User.is_active)
    )
    await db.refresh(payment)
    order = await db.scalar(select(SaleOrder).where(SaleOrder.payment_id == payment.id))
    if order is None:
        raise ValueError("Missing manual order")
    if payment.status == "PAID" and payment.provider_payment_id != charge_id:
        raise ValueError("Charge mismatch")
    payment.provider_payment_id = charge_id
    if payment.status != "PAID":
        payment.status, payment.paid_at = "PAID", datetime.now(UTC)
    await confirm_order(db, order, settings)
