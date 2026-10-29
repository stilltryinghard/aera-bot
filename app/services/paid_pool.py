"""Reserve owner-created keys, deliver only after verified payment, and read expiry."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, or_, select, update

from app.db.models import (
    AppSetting,
    FamilyAccess,
    ManualAccess,
    PaidLink,
    Payment,
    Plan,
    ReferralSoloReward,
    SaleOrder,
    SupportTicket,
    TrialLink,
    User,
)
from app.services.commerce import utc
from app.services.notifications import enqueue
from app.services.trial_pool import parse_link


async def enabled(db):
    value = await db.get(AppSetting, "paid_pool_enabled")
    return value is not None and value.value == "true"


async def available(db, order):
    from app.services.renewals import intent

    if await intent(db, order):
        return True
    if not await enabled(db):
        return True
    from app.services.trial_upgrade import eligible_trial
    ticket = await db.get(SupportTicket, order.ticket_id)
    if ticket and await eligible_trial(db, ticket.user_id):
        return True
    return bool(
        await db.scalar(
            select(PaidLink.id)
            .where(
                PaidLink.plan_id == order.plan_id,
                PaidLink.status.in_(["FREE", "RESERVED"]),
                (PaidLink.order_id.is_(None) | (PaidLink.order_id == order.id)),
                PaidLink.checked_at > datetime.now(UTC) - timedelta(minutes=2),
            )
            .limit(1)
        )
    )


async def import_paid_links(db, groups, vault):
    for slug, links in groups.items():
        plan = await db.scalar(select(Plan).where(Plan.slug == slug, Plan.is_active.is_(True)))
        if not plan:
            raise ValueError("Unknown paid plan")
        for raw in links:
            identity, label = parse_link(raw)
            if await db.scalar(select(TrialLink.id).where(TrialLink.client_uuid == identity)):
                raise ValueError("Trial key cannot be sold")
            previous = await db.scalar(select(PaidLink).where(PaidLink.client_uuid == identity))
            if previous:
                if previous.plan_id != plan.id:
                    raise ValueError("Key already belongs to another plan")
                continue
            db.add(
                PaidLink(
                    client_uuid=identity,
                    label=label,
                    plan_id=plan.id,
                    link_encrypted=vault.cipher.encrypt(raw.strip().encode()).decode(),
                )
            )
            await db.flush()
    if groups and not await db.get(AppSetting, "paid_pool_enabled"):
        db.add(AppSetting(key="paid_pool_enabled", value="true"))


async def reserve(db, order):
    """Called during pre-checkout. Never release a key that may have been paid for."""
    ticket = await db.get(SupportTicket, order.ticket_id)
    await db.execute(update(User).where(User.id == ticket.user_id).values(is_active=User.is_active))
    from app.services.renewals import intent

    renewal = await intent(db, order)
    if renewal:
        return renewal
    from app.services.trial_upgrade import eligible_trial
    trial = await eligible_trial(db, ticket.user_id)
    if trial and not order.paid_at:
        return trial
    previous = await db.scalar(select(PaidLink).where(PaidLink.order_id == order.id))
    if previous:
        return previous
    if not await enabled(db):
        return None
    candidates = await db.scalars(
        select(PaidLink.id)
        .where(
            PaidLink.plan_id == order.plan_id,
            PaidLink.status == "FREE",
            PaidLink.order_id.is_(None),
            PaidLink.checked_at > datetime.now(UTC) - timedelta(minutes=2),
        )
        .order_by(PaidLink.created_at)
        .with_for_update(skip_locked=True)
    )
    for identity in candidates:
        claimed = await db.execute(
            update(PaidLink)
            .where(PaidLink.id == identity, PaidLink.status == "FREE", PaidLink.order_id.is_(None))
            .values(order_id=order.id, user_id=ticket.user_id, status="RESERVED")
        )
        if claimed.rowcount:
            return await db.get(PaidLink, identity, populate_existing=True)
    return None


async def fulfill(db, order, settings):
    if not order.paid_at or not await enabled(db):
        return False
    from app.services.renewals import intent

    renewal = await intent(db, order)
    if renewal:
        if renewal.status == "CHECKOUT":
            renewal.status = "PENDING"
        return renewal.status == "DONE"
    from app.services.trial_upgrade import prepare, restore_previous_device
    if await prepare(db, order, settings):
        return True
    record = await reserve(db, order)
    ticket = await db.get(SupportTicket, order.ticket_id)
    if record is None:
        for identity in settings.admin_ids:
            admin = await db.scalar(select(User).where(User.telegram_id == identity))
            if admin:
                await enqueue(
                    db,
                    f"paid-no-stock:{order.id}:{identity}",
                    admin.id,
                    "PORTAL",
                    f"⚠️ Оплата получена, но свободного ключа нет. Покупка № {ticket.id[:8]}. "
                    "Пополни запас тарифа в /admin. Бот повторит выдачу автоматически.",
                )
        return False
    if not record.issued_at:
        record.issued_at = datetime.now(UTC)
        record.status = "WAITING" if record.started_at is None else record.status
    if await restore_previous_device(db, record, settings):
        return True
    ticket.status, ticket.closed_at = "CONNECTED", record.issued_at
    # The queued payload contains only the record ID, never a plaintext VPN key.
    await enqueue(db, "paid-link:" + order.id, ticket.user_id, "ACCESS_READY", record.id)
    from app.services.trial_conversion import schedule_paid_trials
    await schedule_paid_trials(db, settings, user_id=ticket.user_id)
    return True


async def retry_fulfillment(db, settings):
    """Recover a paid purchase awaiting stock without issuing a second key."""
    if not await enabled(db):
        return
    orders = list(
        await db.scalars(
            select(SaleOrder)
            .join(SupportTicket, SupportTicket.id == SaleOrder.ticket_id)
            .outerjoin(PaidLink, PaidLink.order_id == SaleOrder.id)
            .where(
                SaleOrder.paid_at.is_not(None),
                PaidLink.issued_at.is_(None),
                or_(PaidLink.id.is_not(None), SupportTicket.status.in_(["OPEN", "IN_PROGRESS"])),
            )
            .order_by(SaleOrder.paid_at)
            .limit(25)
        )
    )
    for order in orders:
        await fulfill(db, order, settings)


def current_access_condition():
    return and_(
        PaidLink.issued_at.is_not(None),
        or_(
            and_(PaidLink.status == "WAITING", PaidLink.expires_at.is_(None)),
            and_(PaidLink.status == "ACTIVE", PaidLink.expires_at > datetime.now(UTC)),
        ),
    )


def issued_entitlement_condition():
    """A verified paid purchase or an explicitly granted referral gift."""
    gift = exists().where(
        ReferralSoloReward.order_id == SaleOrder.id,
        ReferralSoloReward.status == "ISSUED",
    )
    return or_(SaleOrder.paid_at.is_not(None), gift)


def current_customer_condition():
    """Current paid, gifted or owner-authorized manual access."""
    paid = exists(
        select(PaidLink.id)
        .join(SaleOrder, SaleOrder.id == PaidLink.order_id)
        .where(
            PaidLink.user_id == User.id, issued_entitlement_condition(), current_access_condition()
        )
    )
    legacy = exists(
        select(ManualAccess.id).where(
            ManualAccess.user_id == User.id,
            ManualAccess.expires_at > datetime.now(UTC),
        )
    )
    family = exists(
        select(FamilyAccess.id).where(
            FamilyAccess.user_id == User.id, FamilyAccess.is_active.is_(True)
        )
    )
    return or_(paid, legacy, family)


async def current(db, user_id):
    return await db.scalar(
        select(PaidLink)
        .join(SaleOrder, SaleOrder.id == PaidLink.order_id)
        .where(
            PaidLink.user_id == user_id, issued_entitlement_condition(), current_access_condition()
        )
        .order_by(PaidLink.issued_at.desc(), PaidLink.id.desc())
        .limit(1)
    )


async def apply_paid_snapshot(db, snapshot, settings):
    config = snapshot.get("settings", {})
    if isinstance(config, str):
        config = json.loads(config)
    clients = {c.get("id"): c for c in config.get("clients", [])}
    stats_by_email = {t.get("email"): t for t in snapshot.get("clientStats", [])}
    now = datetime.now(UTC)
    for record in await db.scalars(select(PaidLink)):
        from app.services.trial_upgrade import pending
        if await pending(db, record.id):
            continue
        client = clients.get(record.client_uuid)
        if client is None:
            record.status = "UNAVAILABLE"
            continue
        stats = stats_by_email.get(client.get("email"), {})
        expiry_ms = int(stats.get("expiryTime") or client.get("expiryTime", 0))
        on = client.get("enable") is True and stats.get("enable", True) is True
        record.checked_at, record.panel_email = now, client.get("email")
        if record.order_id is None:
            plan = await db.get(Plan, record.plan_id)
            expected = plan.duration_days * 86400000
            # Validate the preconfigured term and IP limit; never change the panel.
            limits_ok = int(client.get("totalGB", 0)) == 0 and int(client.get("limitIp", 0)) == (
                0 if plan.unlimited_devices else plan.device_limit
            )
            valid_duration = (
                expiry_ms == -expected and int(stats.get("up", 0)) + int(stats.get("down", 0)) == 0
            )
            record.status = "FREE" if on and limits_ok and valid_duration else "UNAVAILABLE"
            if valid_duration:
                record.duration_ms = -expiry_ms
            continue
        if not record.issued_at:
            order = await db.get(SaleOrder, record.order_id)
            payment = (
                await db.get(Payment, order.payment_id) if order and order.payment_id else None
            )
            ticket = await db.get(SupportTicket, order.ticket_id) if order else None
            plan = await db.get(Plan, record.plan_id)
            unused = (
                {"up", "down", "lastOnline"}.issubset(stats)
                and int(stats.get("up", -1)) + int(stats.get("down", -1)) == 0
                and int(stats.get("lastOnline", 0)) == 0
            )
            limits_ok = int(client.get("totalGB", 0)) == 0 and int(client.get("limitIp", -1)) == (
                0 if plan.unlimited_devices else plan.device_limit
            )
            from app.services.orders import verified_cancellation

            if (
                order
                and not order.paid_at
                and verified_cancellation(payment, now)
                and ticket
                and ticket.status == "CLOSED"
                and unused
                and limits_ok
                and on
                and expiry_ms == -plan.duration_days * 86400000
            ):
                record.order_id = record.user_id = None
                record.status = "FREE"
                record.duration_ms = -expiry_ms
                record.started_at = record.expires_at = None
                continue
        if record.issued_at and not record.started_at:
            # The panel may turn a relative term into a date on a zero-byte
            # statistics report. A purchase starts only after actual VPN use.
            used = int(stats.get("up", 0)) + int(stats.get("down", 0)) > 0 or int(
                stats.get("lastOnline", 0)
            ) > int(utc(record.issued_at).timestamp() * 1000)
            if not used:
                record.expires_at = None
                record.status = "WAITING" if on else "EXPIRED"
                continue
        if expiry_ms > 0:
            record.expires_at = datetime.fromtimestamp(expiry_ms / 1000, UTC)
            if record.started_at is None and record.duration_ms:
                record.started_at = record.expires_at - timedelta(milliseconds=record.duration_ms)
            record.status = "ACTIVE" if on and utc(record.expires_at) > now else "EXPIRED"
        elif not on:
            record.status = "EXPIRED"
        elif record.started_at:
            record.status = "UNAVAILABLE"
        else:
            record.status = "WAITING" if record.issued_at else "RESERVED"


async def latest(db, user_id):
    return await db.scalar(
        select(PaidLink)
        .where(PaidLink.user_id == user_id, PaidLink.issued_at.is_not(None))
        .order_by(PaidLink.issued_at.desc(), PaidLink.id.desc())
        .limit(1)
    )
