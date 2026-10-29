"""Durable same-key renewals. Verified payments only; retry exact deadlines."""

import asyncio
import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit, quote

from sqlalchemy import select, update
from app.db.models import (
    AccessRenewal,
    AppSetting,
    ManualAccess,
    PaidLink,
    Plan,
    SaleOrder,
    SupportTicket,
    User,
)
from app.core.security import TokenVault
from app.integrations.xui.v328 import XUI328Client
from app.services.notifications import enqueue

_lock = asyncio.Lock()


async def intent(db, order):
    return await db.scalar(select(AccessRenewal).where(AccessRenewal.order_id == order.id))


async def owned_access(db, user_id, kind, identity):
    model = {"manual": ManualAccess, "paid": PaidLink}.get(kind)
    if model is None:
        raise ValueError("Only paid or manual subscriptions can be renewed")
    row = await db.get(model, identity)
    if not row or row.user_id != user_id or not row.link_encrypted:
        raise ValueError("Subscription ownership mismatch")
    if kind == "paid" and not row.issued_at:
        raise ValueError("Unissued stock cannot be renewed")
    return row


async def create_renewal(db, user, kind, identity):
    if user.is_blocked or not user.is_active:
        raise ValueError("Account unavailable")
    await db.execute(update(User).where(User.id == user.id).values(is_active=User.is_active))
    access = await owned_access(db, user.id, kind, identity)
    plan = await db.get(Plan, access.plan_id)
    if not access.expires_at:
        raise ValueError("Connect your subscription before renewing it")
    if not plan or not plan.is_active or plan.price_minor > 1000000:
        raise ValueError("Plan unavailable")
    from app.services.payment_timeout import expire_open_orders
    await expire_open_orders(db, user_id=user.id)
    existing = await db.scalar(
        select(AccessRenewal)
        .join(SaleOrder, SaleOrder.id == AccessRenewal.order_id)
        .join(SupportTicket, SupportTicket.id == SaleOrder.ticket_id)
        .where(
            AccessRenewal.user_id == user.id,
            AccessRenewal.access_kind == kind,
            AccessRenewal.access_id == identity,
            AccessRenewal.status != "DONE",
            SupportTicket.status.in_(["OPEN", "IN_PROGRESS"]),
        )
    )
    if existing:
        return await db.get(SaleOrder, existing.order_id)
    from app.config import get_settings

    settings = get_settings()
    raw = TokenVault(settings.app_secret).reveal(access.link_encrypted)
    client_uuid = urlsplit(raw).username
    reader = await reader_for(db, settings)
    try:
        client = await read_client(reader, client_uuid)
    finally:
        await reader.close()
    if int(client.get("expiryTime", 0)) <= 0:
        raise ValueError("Subscription must have a finite running term")
    if not client.get("enable") and int(client["expiryTime"]) > int(
        datetime.now(UTC).timestamp() * 1000
    ):
        raise ValueError("Disabled subscription requires review")
    ticket = SupportTicket(
        user_id=user.id,
        category="TARIFF_REQUEST",
        subject="Продление существующей подписки · " + plan.name,
    )
    db.add(ticket)
    await db.flush()
    from app.services.portal import ensure_order

    order = await ensure_order(db, ticket, plan)
    db.add(
        AccessRenewal(
            order_id=order.id,
            user_id=user.id,
            access_kind=kind,
            access_id=identity,
            duration_days=plan.duration_days,
            client_uuid=client_uuid,
            panel_email=client["email"],
            fingerprint=fingerprint(client),
        )
    )
    await db.flush()
    return order


def fingerprint(client):
    # Everything except expiry/enable must survive the update verbatim.
    return {
        k: v
        for k, v in client.items()
        if k not in {"expiryTime", "enable", "created_at", "updated_at", "createdAt", "updatedAt"}
    }


async def read_client(reader, identity):
    inbound = await reader.get_inbound(reader.inbound_id)
    if inbound.get("protocol") != "vless" or inbound.get("nodeId") not in {None, 0}:
        raise ValueError("Unsupported inbound")
    settings = inbound.get("settings", {})
    if isinstance(settings, str):
        settings = json.loads(settings)
    matches = [c for c in settings.get("clients", []) if c.get("id") == identity]
    if len(matches) != 1:
        raise ValueError("Client missing or ambiguous")
    client = matches[0]
    global_row = await reader.request(
        "GET", "panel/api/clients/get/" + quote(client["email"], safe="")
    )
    if not isinstance(global_row, dict) or global_row.get("inboundIds") != [reader.inbound_id]:
        raise ValueError("Shared client cannot be renewed")
    global_client = global_row.get("client", {})
    if (
        global_client.get("uuid", global_client.get("id")) != identity
        or global_client.get("email") != client["email"]
    ):
        raise ValueError("Global identity mismatch")
    return client


async def reader_for(db, settings):
    vault = TokenVault(settings.app_secret)
    stored = await db.get(AppSetting, "trial_observer")
    if not stored:
        raise ValueError("Panel not configured")
    config = json.loads(vault.reveal(stored.value))
    return XUI328Client(
        config["base_url"], config["api_token"], config["inbound_id"], allow_writes=True
    )


async def prepare(sessions, settings, identity):
    async with sessions.begin() as db:
        renewal = await db.get(AccessRenewal, identity)
        order = await db.get(SaleOrder, renewal.order_id)
        if not order.paid_at or renewal.status == "DONE":
            return False
        user = await db.get(User, renewal.user_id)
        if user.is_blocked or not user.is_active:
            raise ValueError("Account unavailable")
        access = await owned_access(db, user.id, renewal.access_kind, renewal.access_id)
        if order.plan_id != access.plan_id:
            raise ValueError("Tariff changed")
        if renewal.target_expiry_ms is not None:
            return True
        raw = TokenVault(settings.app_secret).reveal(access.link_encrypted)
        client_uuid = urlsplit(raw).username
        if client_uuid != renewal.client_uuid:
            raise ValueError("Subscription key changed after checkout")
        reader = await reader_for(db, settings)
        try:
            client = await read_client(reader, client_uuid)
        finally:
            await reader.close()
        if fingerprint(client) != renewal.fingerprint:
            raise ValueError("Panel settings changed after checkout")
        old = int(client.get("expiryTime", 0))
        now = datetime.now(UTC)
        if old <= 0:
            raise ValueError("Only subscriptions with a running finite term can be renewed")
        if not client.get("enable") and old > int(now.timestamp() * 1000):
            raise ValueError("Disabled active subscription requires review")
        base = max(old, int(now.timestamp() * 1000))
        # Match the stock plan's exact day-based duration, including six-month stock.
        renewal.original_expiry_ms = old
        renewal.target_expiry_ms = base + renewal.duration_days * 86400000
        renewal.client_uuid, renewal.panel_email = client_uuid, client["email"]
        renewal.fingerprint = fingerprint(client)
        renewal.status = "PREPARED"
    # This deadline is committed BEFORE the panel request. A timeout cannot add days twice.
    return True


async def apply(sessions, settings, identity):
    async with sessions.begin() as db:
        renewal = await db.get(AccessRenewal, identity)
        order = await db.get(SaleOrder, renewal.order_id)
        if renewal.status == "DONE" or not order.paid_at or not renewal.target_expiry_ms:
            return
        user = await db.get(User, renewal.user_id)
        if user.is_blocked or not user.is_active:
            raise ValueError("Account unavailable")
        access = await owned_access(db, user.id, renewal.access_kind, renewal.access_id)
        if (
            access.plan_id != order.plan_id
            or urlsplit(TokenVault(settings.app_secret).reveal(access.link_encrypted)).username
            != renewal.client_uuid
        ):
            raise ValueError("Subscription changed")
        reader = await reader_for(db, settings)
        try:
            client = await read_client(reader, renewal.client_uuid)
            if fingerprint(client) != renewal.fingerprint:
                raise ValueError("Panel settings changed; manual review required")
            deadline = int(client.get("expiryTime", 0))
            if deadline not in {renewal.original_expiry_ms, renewal.target_expiry_ms}:
                raise ValueError("Panel deadline changed; manual review required")
            if deadline != renewal.target_expiry_ms:
                payload = {**client, "expiryTime": renewal.target_expiry_ms, "enable": True}
                await reader.request(
                    "POST",
                    "panel/api/clients/update/"
                    + quote(renewal.panel_email, safe="")
                    + f"?inboundIds={reader.inbound_id}",
                    payload,
                )
            checked = await read_client(reader, renewal.client_uuid)
            if (
                int(checked.get("expiryTime", 0)) != renewal.target_expiry_ms
                or checked.get("enable") is not True
                or fingerprint(checked) != renewal.fingerprint
            ):
                raise ValueError("Panel read-back failed")
        finally:
            await reader.close()
        access.expires_at = datetime.fromtimestamp(renewal.target_expiry_ms / 1000, UTC)
        if renewal.access_kind == "paid":
            access.status, access.checked_at = "ACTIVE", datetime.now(UTC)
        renewal.status, renewal.completed_at = "DONE", datetime.now(UTC)
        ticket = await db.get(SupportTicket, order.ticket_id)
        ticket.status, ticket.closed_at = "CONNECTED", renewal.completed_at
        date = access.expires_at.strftime("%d.%m.%Y %H:%M UTC")
        message = (
            f"✅ Подписка продлена до {date}. Ваша ссылка осталась прежней — в INCY / Happ ничего менять не нужно."
            if user.language_code == "ru"
            else f"✅ Subscription renewed until {date}. Your link is unchanged; no changes in INCY / Happ are needed."
        )
        await enqueue(db, "renewal-done:" + order.id, user.id, "PORTAL", message)


async def process_pending(sessions, settings):
    # One worker at a time, including across processes/restarts on the production host.
    import fcntl

    with open("/opt/aera/.renewal-worker.lock", "a") as lockfile:
        try:
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        async with _lock:
            async with sessions() as db:
                identities = list(
                    await db.scalars(
                        select(AccessRenewal.id)
                        .join(SaleOrder, SaleOrder.id == AccessRenewal.order_id)
                        .where(
                            SaleOrder.paid_at.is_not(None),
                            AccessRenewal.status.in_(["PENDING", "PREPARED"]),
                        )
                        .order_by(SaleOrder.paid_at, AccessRenewal.created_at)
                        .limit(25)
                    )
                )
            failed_targets = set()
            for identity in identities:
                async with sessions() as db:
                    r = await db.get(AccessRenewal, identity)
                    target = (r.access_kind, r.access_id)
                if target in failed_targets:
                    continue
                try:
                    if await prepare(sessions, settings, identity):
                        await apply(sessions, settings, identity)
                except Exception as error:
                    failed_targets.add(target)
                    logging.getLogger("aera").error(
                        "renewal_failed id=%s type=%s", identity, type(error).__name__
                    )
                    async with sessions.begin() as db:
                        for telegram_id in settings.admin_ids:
                            admin = await db.scalar(
                                select(User).where(User.telegram_id == telegram_id)
                            )
                            if admin:
                                await enqueue(
                                    db,
                                    f"renewal-review:{identity}:{telegram_id}",
                                    admin.id,
                                    "PORTAL",
                                    f"⚠️ Оплаченное продление {identity[:8]} ожидает проверки панели. Новый ключ не выдавался; заказ будет повторён автоматически.",
                                )
