"""Durable tariff selections for personal follow-up; no payment or VPN mutations."""

from sqlalchemy import select

from app.bot.presentation import devices_label, period_label, price_label, traffic_label
from app.db.models import Plan, SaleOrder, SupportMessage, SupportTicket, User
from app.services.commerce import CommerceService
from app.services.notifications import enqueue

REQUEST_CATEGORY = "TARIFF_REQUEST"


def paid_requests():
    """Filter before pagination; a selected plan or ticket status is not payment."""
    return (
        select(SupportTicket)
        .join(SaleOrder, SaleOrder.ticket_id == SupportTicket.id)
        .where(SupportTicket.category == REQUEST_CATEGORY, SaleOrder.paid_at.is_not(None))
    )


async def select_tariff(
    db,
    service: CommerceService,
    user: User,
    plan_id: str,
    admin_ids: set[int],
    *,
    notify_owner=True,
):
    if not admin_ids:
        raise ValueError("Request recipient is not configured")
    plan = await db.get(Plan, plan_id)
    if not plan or not plan.is_active or plan.price_minor > 1000000 or user.is_blocked or not user.is_active:
        raise ValueError("Tariff selection unavailable")
    from app.services.payment_timeout import expire_open_orders
    await expire_open_orders(db, user_id=user.id)
    subject = f"{plan.name} · {period_label(plan)} · {price_label(plan)}"[:256]
    # The preceding user upsert holds this user's write lock until commit (also on SQLite).
    # Reuse the open selection so repeated clicks do not notify the owner repeatedly.
    ticket = await db.scalar(
        select(SupportTicket)
        .join(SupportMessage, SupportMessage.ticket_id == SupportTicket.id)
        .where(
            SupportTicket.user_id == user.id,
            SupportTicket.category == REQUEST_CATEGORY,
            SupportTicket.subject == subject,
            SupportTicket.status.in_(["OPEN", "IN_PROGRESS"]),
            SupportMessage.sender_type == "TARIFF_SELECTION",
            SupportMessage.sender_id == plan.id,
        )
        .order_by(SupportTicket.created_at.desc())
        .limit(1)
    )
    if ticket is None:
        ticket = SupportTicket(
            user_id=user.id,
            category=REQUEST_CATEGORY,
            subject=subject,
        )
        db.add(ticket)
        await db.flush()
        contact = f"@{user.username}" if user.username else "Username не указан"
        text = (
            f"ЗАЯВКА НА ТАРИФ\nНомер: {ticket.id[:8]}\n\n"
            f"Пользователь: {user.first_name or 'Без имени'}\n{contact}\n"
            f"Telegram ID: {user.telegram_id}\n\n"
            f"Тариф: {plan.name}\nСрок: {period_label(plan)}\nЦена: {price_label(plan)}\n"
            f"{devices_label(plan.device_limit, plan.unlimited_devices)}\n"
            f"{traffic_label(plan.traffic_limit_bytes)}\nКлиент: INCY / Happ\n\n"
            "Выбор подписки для прямой оплаты и автоматической выдачи."
        )
        db.add(
            SupportMessage(
                ticket_id=ticket.id,
                sender_type="TARIFF_SELECTION",
                sender_id=plan.id,
                text=text,
            )
        )
        for admin_id in sorted(admin_ids):
            admin = await service.user(admin_id)
            if notify_owner:
                await enqueue(
                    db, f"request:{ticket.id}:{admin_id}", admin.id, REQUEST_CATEGORY, text
                )
    return ticket, plan
