"""One optional help reminder per current, genuinely unstarted trial assignment."""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from cryptography.fernet import InvalidToken
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import AppSetting, DeviceBinding, TrialLink, TrialUsage, User
from app.core.security import TokenVault
from app.services.commerce import utc
from app.services.trial_pool import fingerprint

KIND = 'TRIAL_REMINDER'
START_KEY = 'trial_reminders_start_at_v1'
MESSAGE = ('Не получилось подключиться? Поможем 💛\n\n'
           'Открой инструкцию или напиши в поддержку.\n'
           'Пробные 5 дней начнутся после первого подключения.')


def markup():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='💡 Инструкция', url='https://t.me/aerasetting/12'),
        InlineKeyboardButton(text='💬 Поддержка', url='https://t.me/aerasuppbot'),
    ]])


def assignment(row):
    return json.dumps(dict(id=row.id, uuid=row.client_uuid, issued=utc(row.issued_at).isoformat()), sort_keys=True)


async def eligible(db, token, settings, *, user_id=None, now=None):
    now = now or datetime.now(UTC)
    try:
        data = json.loads(token)
        if not isinstance(data, dict) or set(data) != {'id', 'uuid', 'issued'}:
            return False
        row = await db.get(TrialLink, data['id'], populate_existing=True)
        cutoff = await db.get(AppSetting, START_KEY)
        if (not row or not row.user_id or not row.issued_at or not cutoff
                or utc(row.issued_at).timestamp() < float(cutoff.value)
                or (user_id is not None and row.user_id != user_id)
                or row.status != 'WAITING' or row.started_at or row.expires_at
                or row.activation_deadline_ms or row.pending_uuid
                or row.client_uuid != data['uuid'] or utc(row.issued_at).isoformat() != data['issued']
                or not timedelta(minutes=45) <= now-utc(row.issued_at) < timedelta(hours=1)
                or not row.checked_at or not timedelta(0) <= now-utc(row.checked_at) <= timedelta(seconds=45)):
            return False
        user = await db.get(User, row.user_id, populate_existing=True)
        if not user or not user.is_active or user.is_blocked:
            return False
        usage = await db.get(TrialUsage, fingerprint(user.telegram_id, settings.app_secret))
        if not usage or usage.activated_at:
            return False
        if await db.get(AppSetting, 'trial-node-first-use.v1:' + row.id):
            return False
        snapshot = await db.get(AppSetting, 'multinode.panel.v1')
        state = json.loads(TokenVault(settings.app_secret).reveal(snapshot.value)) if snapshot else {}
        if not 0 <= now.timestamp()-state.get('checked_at', 0) <= 30:
            return False
        available = state.get('nodes_available', {})
        if not all(available.get(node) is True for node in ('fi','de','uk')):
            return False
        entries = state.get('entries', {})
        issued_ms = int(utc(row.issued_at).timestamp()*1000)
        def used(entry, baseline):
            if not entry:
                return True  # Missing observation is uncertainty, not proof of no use.
            primary = entry.get('nl_stats', {})
            if (int(primary.get('up',0))+int(primary.get('down',0)) > baseline
                    or int(primary.get('last_online',0)) > issued_ms):
                return True
            remotes = entry.get('remote_states', [])
            if len(remotes) != 3:
                return True
            return any(int(r.get('up',0))+int(r.get('down',0)) > 0
                       or int(r.get('last_online',0)) > issued_ms for r in remotes)
        if used(entries.get(row.client_uuid), row.baseline_bytes):
            return False
        for child in await db.scalars(select(DeviceBinding).where(DeviceBinding.trial_link_id == row.id,
                                                                  DeviceBinding.status != 'REVOKED')):
            if child.started_at or child.expires_at or used(entries.get(child.client_uuid), 0):
                return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError, InvalidToken):
        return False


async def queue_reminders(sessions, settings, *, now=None):
    from app.services.notifications import enqueue
    now = now or datetime.now(UTC)
    async with sessions.begin() as db:
        if not await db.get(AppSetting, START_KEY):
            insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
            await db.execute(insert(AppSetting).values(key=START_KEY, value=str(now.timestamp()))
                             .on_conflict_do_nothing(index_elements=[AppSetting.key]))
            return 0  # Existing assignments never receive a surprise retrospective reminder.
        candidates = list(await db.scalars(select(TrialLink).where(
            TrialLink.status == 'WAITING', TrialLink.user_id.is_not(None),
            TrialLink.issued_at > now-timedelta(hours=1),
            TrialLink.issued_at <= now-timedelta(minutes=45))))
        queued = 0
        for row in candidates:
            token = assignment(row)
            if await eligible(db, token, settings, user_id=row.user_id, now=now):
                await enqueue(db, 'trial-help:' + hashlib.sha256(token.encode()).hexdigest(), row.user_id, KIND, token)
                queued += 1
        return queued
