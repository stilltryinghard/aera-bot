"""Atomic trial inventory and synchronization with actual first use."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlsplit
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.trial_policy import DURATION_MS, LEGACY_DURATION_MS, TRIAL_IP_LIMIT
from app.db.models import AppSetting, Notification, TrialLink, TrialUsage, User
from app.services.commerce import utc
from app.services.notifications import enqueue


def fingerprint(telegram_id, secret):
    return hmac.new(
        secret.encode(), f"aera-trial:{telegram_id}".encode(), hashlib.sha256
    ).hexdigest()


def parse_link(link):
    parsed = urlsplit(link.strip())
    if parsed.scheme != "vless" or not parsed.hostname or parsed.password:
        raise ValueError("Invalid trial link")
    identity = str(UUID(parsed.username))
    return identity, unquote(parsed.fragment)[:128] or "AERA Trial"


async def import_links(db, links, vault):
    for raw in links:
        identity, label = parse_link(raw)
        if await db.scalar(
            select(TrialLink.id).where(
                or_(TrialLink.client_uuid == identity, TrialLink.source_uuid == identity)
            )
        ):
            continue
        db.add(
            TrialLink(
                client_uuid=identity,
                source_uuid=identity,
                label=label,
                link_encrypted=vault.cipher.encrypt(raw.strip().encode()).decode(),
            )
        )


async def claim_link(db, user, secret):
    await db.execute(update(User).where(User.id == user.id).values(is_active=User.is_active))
    previous = await db.scalar(select(TrialLink).where(TrialLink.user_id == user.id))
    if previous:
        if previous.pending_uuid or (not previous.started_at and previous.issued_at
                and utc(previous.issued_at) <= datetime.now(UTC) - timedelta(hours=1)
                and previous.status in {"WAITING", "BLOCKED", "RECYCLING"}):
            return previous, "pending"
        return previous, "existing"
    digest = fingerprint(user.telegram_id, secret)
    if await db.get(TrialUsage, digest):
        return None, "used"
    from app.services.trial_attempts import MAX_ISSUES, issued_count, remember_count
    attempts = await issued_count(db, user, secret)
    if attempts >= MAX_ISSUES:
        return None, "limit"
    # Worker confirms the full negative first-use duration before FREE.
    candidates = list(
        await db.scalars(
            select(TrialLink.id)
            .where(
                TrialLink.status == "FREE",
                TrialLink.user_id.is_(None),
                TrialLink.checked_at > datetime.now(UTC) - timedelta(minutes=2),
            )
            .order_by(TrialLink.created_at)
            .with_for_update(skip_locked=True)
        )
    )
    if not candidates:
        return None, "empty"
    for identity in candidates:
        result = await db.execute(
            update(TrialLink)
            .where(
                TrialLink.id == identity,
                TrialLink.status == "FREE",
                TrialLink.user_id.is_(None),
            )
            .values(user_id=user.id, status="WAITING", issued_at=datetime.now(UTC), expires_at=None)
        )
        if result.rowcount == 1:
            insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
            await db.execute(insert(TrialUsage).values(fingerprint=digest))
            from app.services.daily_metrics import record
            await record(db, "issued", user.telegram_id, secret)
            await remember_count(db, user, secret, attempts + 1)
            user.trial_used = True
            return await db.get(TrialLink, identity), "new"
    return None, "empty"


async def confirm_activation(db, record, settings, enabled=True):
    """Publish activation only once the full actual-use deadline is confirmed."""
    user = await db.get(User, record.user_id)
    if not user:
        return
    digest = fingerprint(user.telegram_id, settings.app_secret)
    usage = await db.get(TrialUsage, digest)
    first = usage is not None and usage.activated_at is None
    if usage:
        usage.activated_at = record.started_at
    record.expires_at = datetime.fromtimestamp(record.activation_deadline_ms / 1000, UTC)
    record.status = (
        "ACTIVE" if enabled and utc(record.expires_at) > datetime.now(UTC) else "EXPIRED"
    )
    if first and record.status == "ACTIVE":
        for admin_id in settings.admin_ids:
            admin = await db.scalar(select(User).where(User.telegram_id == admin_id))
            if admin:
                await enqueue(
                    db,
                    f"trial-active:{record.id}:{user.id}:verified:{admin_id}",
                    admin.id,
                    "TRIAL_ACTIVE",
                    f"🚀 Пробная ссылка активирована\n"
                    f"Клиент: {user.first_name or user.telegram_id}\n"
                    f"@{user.username or '-'}\n"
                    f"Ключ: {record.label}\nДо: {record.expires_at:%d.%m.%Y %H:%M} UTC",
                )


async def apply_snapshot(db, snapshot, settings):
    """Positive expiry alone is not use: 3.2.8 can set it on a zero-byte report."""
    config = snapshot.get("settings", {})
    if isinstance(config, str):
        config = json.loads(config)
    clients = {c.get("id"): c for c in config.get("clients", [])}
    traffic = {t.get("email"): t for t in snapshot.get("clientStats", [])}
    now = datetime.now(UTC)
    legacy_setting = await db.get(AppSetting, "trial_legacy_7_ids")
    legacy_ids = json.loads(legacy_setting.value) if legacy_setting else []
    if not isinstance(legacy_ids, list) or any(not isinstance(identity, str) for identity in legacy_ids):
        raise ValueError("Invalid grandfathered trial policy")
    legacy_ids = set(legacy_ids)
    cutoff_setting = await db.get(AppSetting, "trial5_start_at")
    legacy_cutoff = utc(datetime.fromisoformat(cutoff_setting.value)) if cutoff_setting else None
    if legacy_ids and legacy_cutoff is None:
        raise ValueError("Grandfathered trial policy requires deployment timestamp")
    for record in await db.scalars(select(TrialLink)):
        from app.db.models import PaidLink
        if await db.scalar(select(PaidLink.id).where(PaidLink.client_uuid == record.client_uuid)):
            record.status = "CONVERTED"
            continue
        # Only explicitly grandfathered assignments keep seven days; recycled
        # inventory becomes five days again when its previous owner is cleared.
        duration_ms = (LEGACY_DURATION_MS if record.user_id and record.id in legacy_ids
                       and record.issued_at and utc(record.issued_at) < legacy_cutoff else DURATION_MS)
        if record.pending_uuid:
            # A persisted key replacement must be reconciled before any assignment.
            continue
        client = clients.get(record.client_uuid)
        if client is None:
            if record.status == "PROVISIONING":
                continue
            if not record.user_id:
                record.status = "UNAVAILABLE"
            continue
        stats = traffic.get(client.get("email"), {})
        expiry_ms = int(stats.get("expiryTime") or client.get("expiryTime", 0))
        enabled = client.get("enable") is True and stats.get("enable", True) is True
        record.panel_email, record.checked_at = client.get("email"), now
        if not record.user_id:
            if record.issued_at:
                record.status = "RETIRED"
                continue
            total = int(stats.get("up", 0)) + int(stats.get("down", 0))
            last_online = int(stats.get("lastOnline", 0))
            used = total > record.baseline_bytes or (
                record.ready_at and last_online > int(utc(record.ready_at).timestamp() * 1000)
            )
            limits_ok = (
                int(client.get("limitIp", -1)) == TRIAL_IP_LIMIT
                and int(client.get("totalGB", 0)) == 0
                and int(client.get("reset", 0)) == 0
            )
            if limits_ok and not used and enabled and expiry_ms == -DURATION_MS:
                record.ready_at = record.ready_at or now
                record.status = "FREE"
                record.expires_at = None
            elif (
                limits_ok
                and not used
                and record.ready_at
                and 0 < expiry_ms <= int(now.timestamp() * 1000) + DURATION_MS + 60000
            ):
                # Never issue a date-based key: isolate the next generation from
                # the old email's Xray reports and verify a fresh first-use timer.
                record.status = "NEEDS_RESET"
                record.expires_at = datetime.fromtimestamp(expiry_ms / 1000, UTC)
            else:
                record.status = "UNAVAILABLE"
            continue
        issued_ms = int(utc(record.issued_at).timestamp() * 1000) if record.issued_at else 0
        last_online = int(stats.get("lastOnline", 0))
        used = (
            int(stats.get("up", 0)) + int(stats.get("down", 0)) > record.baseline_bytes
            or last_online > issued_ms > 0
        )
        if record.activation_deadline_ms:
            # Existing verified assignment deadlines are contractual; never shorten them.
            if expiry_ms == record.activation_deadline_ms:
                await confirm_activation(db, record, settings, enabled)
            else:
                record.status = "ACTIVATING"
            continue
        if not used:
            # Repair false activations created by the old expiry-only observer.
            if record.started_at:
                user = await db.get(User, record.user_id)
                if user:
                    await db.execute(
                        update(TrialUsage)
                        .where(
                            TrialUsage.fingerprint
                            == fingerprint(user.telegram_id, settings.app_secret)
                        )
                        .values(activated_at=None)
                    )
                await db.execute(
                    update(Notification)
                    .where(
                        Notification.dedupe_key.startswith(f"trial-active:{record.id}:"),
                        Notification.status == "PENDING",
                    )
                    .values(status="CANCELLED")
                )
            record.started_at = record.expires_at = None
            record.status = "WAITING" if enabled else "BLOCKED"
            continue
        if expiry_ms > 0 and expiry_ms <= int(now.timestamp() * 1000):
            # An already finished, used trial must never get a fresh trial.
            record.started_at = record.started_at or datetime.fromtimestamp((expiry_ms - duration_ms) / 1000, UTC)
            record.activation_deadline_ms = expiry_ms
            await confirm_activation(db, record, settings, enabled)
            continue
        start = (
            datetime.fromtimestamp(last_online / 1000, UTC)
            if issued_ms < last_online <= int(now.timestamp() * 1000)
            else now
        )
        record.started_at = start
        record.activation_deadline_ms = int(start.timestamp() * 1000) + duration_ms
        record.expires_at = None
        record.status = "ACTIVATING"


async def synchronize(sessions, vault, settings):
    from app.integrations.xui.v328 import XUI328Client

    async with sessions() as db:
        stored = await db.get(AppSetting, "trial_observer")
        if not stored:
            return
        config = json.loads(vault.reveal(stored.value))
    reader = XUI328Client(
        config["base_url"], config["api_token"], config["inbound_id"], allow_writes=False
    )
    try:
        snapshot = await reader.get_inbound(config["inbound_id"])
    finally:
        await reader.close()
    async with sessions.begin() as db:
        await apply_snapshot(db, snapshot, settings)
        from app.services.paid_pool import apply_paid_snapshot

        await apply_paid_snapshot(db, snapshot, settings)
        from app.services.trial_conversion import schedule_paid_trials
        await schedule_paid_trials(db, settings)
    from app.services.trial_activation import reconcile_activations
    from app.services.trial_recycling import recycle_trials

    await reconcile_activations(sessions, settings, config)
    await recycle_trials(sessions, vault, settings, config)
