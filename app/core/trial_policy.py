"""Shared first-use trial duration for inventory and panel maintenance."""

TRIAL_DAYS = 5
TRIAL_IP_LIMIT = 3
DURATION_MS = TRIAL_DAYS * 86400000
LEGACY_DURATION_MS = 7 * 86400000
SUPPORTED_DURATION_MS = (2 * 86400000, LEGACY_DURATION_MS, DURATION_MS)


async def duration_for_assignment(db, record):
    """Keep the original seven-day grant; recycled assignments use five days."""
    import json
    from datetime import UTC, datetime
    from app.db.models import AppSetting
    marker = await db.get(AppSetting, "trial_legacy_7_ids")
    identities = json.loads(marker.value) if marker else []
    if not isinstance(identities, list) or any(not isinstance(value, str) for value in identities):
        raise ValueError("Invalid grandfathered trial policy")
    if record.id not in identities or not record.user_id or not record.issued_at:
        return DURATION_MS
    cutoff = await db.get(AppSetting, "trial5_start_at")
    if not cutoff:
        raise ValueError("Grandfathered trial cutoff unavailable")
    started = datetime.fromisoformat(cutoff.value)
    started = started.replace(tzinfo=UTC) if started.tzinfo is None else started
    issued = record.issued_at.replace(tzinfo=UTC) if record.issued_at.tzinfo is None else record.issued_at
    return LEGACY_DURATION_MS if issued < started else DURATION_MS


async def device_limit_for_assignment(db, record):
    """Only trials assigned after this rollout receive one HWID slot."""
    from datetime import UTC, datetime
    from app.db.models import AppSetting
    marker = await db.get(AppSetting, "trial_one_device_start_at")
    if not marker or not record or not record.issued_at:
        return TRIAL_IP_LIMIT
    cutoff = datetime.fromisoformat(marker.value)
    cutoff = cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff
    issued = record.issued_at.replace(tzinfo=UTC) if record.issued_at.tzinfo is None else record.issued_at
    return 1 if issued >= cutoff else TRIAL_IP_LIMIT
