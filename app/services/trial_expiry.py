"""Two durable, revalidated expiry notices for an active trial assignment."""
import hashlib
import json
from datetime import UTC, datetime, timedelta
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from app.db.models import TrialLink, User, Payment
from app.services.commerce import utc

KIND = 'TRIAL_EXPIRY'
HOURS = (24, 1)


def message(hours):
    if hours == 24:
        return ('⏳ <b>Пробный доступ закончится через сутки</b>\n\n'
                '<blockquote>До окончания пробной подписки остались <b>24 часа</b> 💛\n\n'
                'Чтобы оставаться на связи, выберите подписку и оплатите удобным способом.</blockquote>'
                '\n\nКоманда AERA ©')
    if hours == 1:
        return ('⌛ <b>Пробный доступ скоро закончится</b>\n\n'
                '<blockquote>До окончания пробной подписки остался <b>1 час</b> 💛\n\n'
                'Выберите подписку и оплатите удобным способом, чтобы продолжить пользоваться AERA.</blockquote>'
                '\n\nКоманда AERA ©')
    raise ValueError('Unsupported trial notice')


def markup():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💎 Выбрать подписку', callback_data='plans')],
        [InlineKeyboardButton(text='👤 Личный кабинет', callback_data='profile')],
    ])


def token(row, hours):
    return json.dumps(dict(id=row.id, uuid=row.client_uuid, issued=utc(row.issued_at).isoformat(),
                           expires=utc(row.expires_at).isoformat(), hours=hours), sort_keys=True)


async def eligible(db, text, *, user_id=None, now=None):
    now = now or datetime.now(UTC)
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or set(data) != {'id','uuid','issued','expires','hours'}:
            return False
        hours = data['hours']
        if type(hours) is not int or hours not in HOURS:
            return False
        row = await db.get(TrialLink, data['id'], populate_existing=True)
        if (not row or not row.user_id or (user_id is not None and row.user_id != user_id)
                or row.status != 'ACTIVE' or not row.started_at or not row.issued_at or not row.expires_at
                or row.pending_uuid or token(row, hours) != text
                or not row.checked_at or not timedelta(0) <= now-utc(row.checked_at) < timedelta(minutes=2)):
            return False
        left = utc(row.expires_at)-now
        if not (timedelta(hours=23) < left <= timedelta(hours=24) if hours == 24
                else timedelta(0) < left <= timedelta(hours=1)):
            return False
        user = await db.get(User, row.user_id, populate_existing=True)
        if not user or not user.is_active or user.is_blocked:
            return False
        # A confirmed purchase suppresses the notice even if fulfillment is still pending.
        paid = await db.scalar(select(Payment.id).where(Payment.user_id == row.user_id,
            Payment.status == 'PAID', Payment.paid_at.is_not(None), Payment.paid_at >= row.issued_at).limit(1))
        return paid is None
    except (ValueError,TypeError,KeyError,AttributeError):
        return False


async def queue_expiry_notices(sessions, *, now=None):
    from app.services.notifications import enqueue
    now = now or datetime.now(UTC)
    async with sessions.begin() as db:
        rows = list(await db.scalars(select(TrialLink).where(TrialLink.status == 'ACTIVE',
            TrialLink.user_id.is_not(None), TrialLink.expires_at > now,
            TrialLink.expires_at <= now+timedelta(hours=24))))
        for row in rows:
            if not row.issued_at or not row.expires_at:
                continue
            for hours in HOURS:
                text = token(row, hours)
                if await eligible(db, text, user_id=row.user_id, now=now):
                    key = 'trial-expiry:' + hashlib.sha256(text.encode()).hexdigest()
                    await enqueue(db, key, row.user_id, KIND, text)
