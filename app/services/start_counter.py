"""Unique /start counters with daily HMAC aggregates."""
import hashlib
import hmac
from sqlalchemy import select,func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import AppSetting

PREFIX = "start_seen_v1:"

async def record_start(db, telegram_id, secret):
    from app.services.daily_metrics import record
    await record(db, "start", telegram_id, secret)
    marker = hmac.new(secret.encode(), f"aera-start-v1:{telegram_id}".encode(), hashlib.sha256).hexdigest()
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    await db.execute(insert(AppSetting).values(key=PREFIX + marker, value="1").on_conflict_do_nothing(index_elements=[AppSetting.key]))

async def start_count(db):
    return await db.scalar(select(func.count()).select_from(AppSetting).where(AppSetting.key.startswith(PREFIX)))
