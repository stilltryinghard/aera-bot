"""Recoverable trial-only rotation; a key is FREE only after fresh panel confirmation."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import delete, or_, select, update

from app.core.trial_policy import SUPPORTED_DURATION_MS
from app.db.models import Notification, PaidLink, TrialLink, TrialUsage, User, AppSetting
from app.integrations.xui.trial_keys import TrialKeyClient, TrialStarted, replacement_email
from app.services.commerce import utc
from app.services.notifications import enqueue

_lock = asyncio.Lock()


def replace_link(raw, old_uuid, new_uuid):
    parsed = urlsplit(raw)
    if parsed.scheme != "vless" or parsed.username != old_uuid or parsed.password:
        raise ValueError("Trial source link mismatch")
    return urlunsplit(parsed._replace(netloc=new_uuid + "@" + parsed.netloc.split("@", 1)[1]))


async def recycle_trials(sessions, vault, settings, config):
    async with _lock:
        async with sessions() as db:
            candidates = list(
                await db.scalars(
                    select(TrialLink.id).where(
                        or_(
                            TrialLink.pending_uuid.is_not(None),
                            TrialLink.status.in_(["WAITING", "BLOCKED"])
                            & (TrialLink.issued_at <= datetime.now(UTC) - timedelta(hours=1))
                            & TrialLink.started_at.is_(None),
                            (TrialLink.status == "EXPIRED")
                            & (TrialLink.expires_at <= datetime.now(UTC)),
                            (TrialLink.status == "NEEDS_RESET")
                            & TrialLink.user_id.is_(None)
                            & TrialLink.ready_at.is_not(None),
                        )
                    )
                )
            )
        client = TrialKeyClient(config["base_url"], config["api_token"], config["inbound_id"])
        try:
            for identity in candidates:
                from app.services.trial_retry import prepare_unused_retry
                try:
                    if not await prepare_unused_retry(sessions, settings, config, identity):
                        continue
                except Exception as error:
                    logging.getLogger("aera").warning("trial_retry_verification_pending type=%s", type(error).__name__)
                    continue
                async with sessions.begin() as db:
                    record = await db.get(TrialLink, identity)
                    # Serialize with claim/forget/observer operations, including SQLite.
                    if record.user_id:
                        await db.execute(
                            update(User)
                            .where(User.id == record.user_id)
                            .values(is_active=User.is_active)
                        )
                    await db.refresh(record)
                    if await db.scalar(
                        select(PaidLink.id).where(PaidLink.client_uuid == record.client_uuid)
                    ):
                        continue
                    if not record.pending_uuid:
                        if not record.panel_email or not record.source_uuid:
                            continue
                        now = datetime.now(UTC)
                        unused = (
                            record.user_id
                            and record.status in {"WAITING", "BLOCKED"}
                            and record.started_at is None
                            and record.issued_at
                            and utc(record.issued_at) <= now - timedelta(hours=1)
                        )
                        expired = (
                            record.status == "EXPIRED"
                            and record.expires_at
                            and utc(record.expires_at) <= now
                            and record.started_at
                            and any(
                                abs(
                                    (
                                        utc(record.expires_at) - utc(record.started_at)
                                    ).total_seconds()
                                    - duration / 1000
                                )
                                < 2
                                for duration in SUPPORTED_DURATION_MS
                            )
                        )
                        idle = (
                            not record.user_id
                            and record.status == "NEEDS_RESET"
                            and record.ready_at
                            and not record.issued_at
                            and record.expires_at
                        )
                        if not unused and not expired and not idle:
                            continue
                        record.pending_uuid = str(uuid4())
                        record.recycle_reason = (
                            "IDLE" if idle else "UNUSED" if unused else "EXPIRED"
                        )
                        record.status = "RECYCLING"
                    old, new, email = record.client_uuid, record.pending_uuid, record.panel_email
                    reason, issued, baseline = (
                        record.recycle_reason,
                        utc(
                            record.ready_at if record.recycle_reason == "IDLE" else record.issued_at
                        ),
                        record.baseline_bytes,
                    )
                try:
                    if reason == "PURCHASED":
                        from app.services.trial_conversion import prepare_paid_recycle
                        if not await prepare_paid_recycle(sessions, settings, config, identity, client):
                            continue
                    baseline = await client.replace_trial_key(
                        old, new, email, reason, issued, baseline
                    )
                except TrialStarted:
                    async with sessions.begin() as db:
                        record = await db.get(TrialLink, identity)
                        record.pending_uuid, record.recycle_reason = None, None
                        record.status, record.checked_at = "UNVERIFIED", None
                        if not record.user_id:
                            record.ready_at = None
                    # The next GET observer restores the actual active/expired state.
                    continue
                except Exception as error:
                    logging.getLogger("aera").error(
                        "trial_rotation_failed type=%s", type(error).__name__
                    )
                    async with sessions.begin() as db:
                        for aid in settings.admin_ids:
                            admin = await db.scalar(select(User).where(User.telegram_id == aid))
                            if admin:
                                await enqueue(
                                    db,
                                    f"trial-rotation-error:{identity}:{new}:{aid}",
                                    admin.id,
                                    "PORTAL",
                                    "⚙️ Не удалось подтвердить замену пробного ключа. "
                                    "Бот повторит проверку; ссылка пока не выдаётся. "
                                    "Раздел /admin - «Пробные ссылки».",
                                )
                    continue
                async with sessions.begin() as db:
                    record = await db.get(TrialLink, identity)
                    if record.pending_uuid != new:
                        continue
                    old_user = await db.get(User, record.user_id) if record.user_id else None
                    if old_user and reason != "PURCHASED":
                        # Reset eligibility only after verified UNUSED rotation. A
                        # confirmed activation is permanent, including after expiry.
                        from app.services.trial_pool import fingerprint
                        digest = fingerprint(old_user.telegram_id, settings.app_secret)
                        usage = await db.get(TrialUsage, digest)
                        if reason == "UNUSED" and not record.started_at and not record.activation_deadline_ms:
                            if usage and usage.activated_at is not None:
                                raise ValueError("Activated trial cannot become retryable")
                            from app.services.funnel_metrics import preserve_trial_issue
                            await preserve_trial_issue(db, digest)
                            await db.execute(delete(TrialUsage).where(
                                TrialUsage.fingerprint == digest, TrialUsage.activated_at.is_(None)))
                            from app.services.trial_attempts import issued_count, remember_count
                            await remember_count(db, old_user, settings.app_secret,
                                                 await issued_count(db, old_user, settings.app_secret))
                            old_user.trial_used = False
                        from app.bot.texts.portal import text

                        from app.services.trial_attempts import MAX_ISSUES, issued_count
                        exhausted = (reason == "UNUSED" and
                                     await issued_count(db, old_user, settings.app_secret) >= MAX_ISSUES)
                        if not exhausted:
                            await enqueue(
                                db,
                                f"trial-recycled:{identity}:{new}",
                                old_user.id,
                                "PORTAL",
                                text(
                                    "trial_timeout" if reason == "UNUSED" else "trial_finished",
                                    old_user.language_code,
                                ),
                            )
                    # Do not deliver a late activation notice belonging to the old assignment.
                    await db.execute(
                        update(Notification)
                        .where(
                            Notification.dedupe_key.startswith(f"trial-active:{identity}:"),
                            Notification.status == "PENDING",
                        )
                        .values(status="CANCELLED")
                    )
                    record.link_encrypted = vault.cipher.encrypt(
                        replace_link(vault.reveal(record.link_encrypted), old, new).encode()
                    ).decode()
                    # First-use intents belong to a credential generation, not
                    # the reusable stock row. Preserve history, clear its latch.
                    marker = await db.get(AppSetting, "trial-node-first-use.v1:" + record.id)
                    if marker:
                        history_key = "trial-node-first-use.history:" + old
                        history = await db.get(AppSetting, history_key)
                        if history is None:
                            db.add(AppSetting(key=history_key, value=marker.value))
                        else:
                            assert history.value == marker.value, "Trial intent history conflict"
                        await db.delete(marker)
                    record.client_uuid = new
                    record.panel_email = replacement_email(new)
                    record.user_id = None
                    record.issued_at = record.started_at = record.expires_at = None
                    record.activation_deadline_ms = None
                    record.pending_uuid = record.recycle_reason = None
                    record.baseline_bytes = baseline
                    record.status, record.checked_at = "FREE", datetime.now(UTC)
                    record.ready_at = record.checked_at
        finally:
            await client.close()
