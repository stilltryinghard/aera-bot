"""Durable two-issue trial budget; independent of recycling and Redis."""
from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import AppSetting, Notification, TrialLink, TrialUsage

MAX_ISSUES = 2
PREFIX = "trial_issue_count_v1:"

def key(telegram_id, secret):
    from app.services.trial_pool import fingerprint
    return PREFIX + fingerprint(telegram_id, secret)

async def issued_count(db, user, secret):
    marker = await db.get(AppSetting, key(user.telegram_id, secret))
    count = int(marker.value) if marker is not None else 0
    if count < 0:
        raise ValueError("Invalid trial issue counter")
    # Every completed unused rotation retains one durable, unique notification.
    # Current assignment is separate; opening that assignment again is not an issue.
    recycled = int(await db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id,
        Notification.dedupe_key.startswith("trial-recycled:", autoescape=True))) or 0)
    current = await db.scalar(select(TrialLink.id).where(
        TrialLink.user_id == user.id, TrialLink.issued_at.is_not(None)))
    digest = key(user.telegram_id, secret)[len(PREFIX):]
    usage = await db.get(TrialUsage, digest)
    history = await db.get(AppSetting, "trial_issued_history_v1:" + digest)
    return max(count, recycled + int(bool(current or usage)), int(bool(history or user.trial_used)))

async def remember_count(db, user, secret, count):
    # Call under the existing per-user database write lock, in the allocation tx.
    if count < 0:
        raise ValueError("Invalid trial issue counter")
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    await db.execute(insert(AppSetting).values(key=key(user.telegram_id, secret), value=str(count))
        .on_conflict_do_update(index_elements=[AppSetting.key], set_={"value": str(count)}))
