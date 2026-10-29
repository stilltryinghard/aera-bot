"""Daily unique people, in Yekaterinburg time; durable HMAC markers only."""
from datetime import UTC, datetime, timedelta, timezone
import hashlib, hmac
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import AppSetting, TrialUsage, SaleOrder, Payment, SupportTicket
TZ = timezone(timedelta(hours=5), 'Asia/Yekaterinburg')
LEGACY_PREFIX = 'daily_funnel_v1:'
PREFIX = 'daily_funnel_22_v2:'
SINCE = 'daily_funnel_started_v1'
def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
def day(value):
    return (utc(value).astimezone(TZ) + timedelta(hours=2)).date().isoformat()
async def record(db, action, identity, secret, at=None):
    if action not in {'start', 'issued'}:
        raise ValueError('Unknown daily metric')
    at = at or datetime.now(UTC)
    digest = hmac.new(secret.encode(), f'aera-daily-v1:{identity}'.encode(), hashlib.sha256).hexdigest()
    insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
    for prefix, date in [(LEGACY_PREFIX, utc(at).astimezone(TZ).date().isoformat()), (PREFIX, day(at))]:
        await db.execute(insert(AppSetting).values(key=f'{prefix}{date}:{action}:{digest}', value='1')
                         .on_conflict_do_nothing(index_elements=[AppSetting.key]))

async def daily_rows(db, now=None, days=7):
    now = now or datetime.now(UTC)
    setting = await db.get(AppSetting, SINCE)
    if not setting:
        return None, []
    since = utc(datetime.fromisoformat(setting.value))
    usage = list(await db.execute(select(TrialUsage.fingerprint, TrialUsage.activated_at).where(TrialUsage.activated_at.is_not(None))))
    paid = list(await db.execute(select(SaleOrder.id, SaleOrder.paid_at, SupportTicket.user_id)
        .join(Payment, Payment.id==SaleOrder.payment_id)
        .join(SupportTicket, SupportTicket.id==SaleOrder.ticket_id)
        .where(SaleOrder.paid_at.is_not(None), Payment.status=='PAID', Payment.paid_at.is_not(None),
               Payment.provider.in_(['platega','telegram_stars']), Payment.user_id==SupportTicket.user_id,
               Payment.plan_id==SaleOrder.plan_id)))
    earliest = min([datetime.fromisoformat(day(since)).date()] + [datetime.fromisoformat(day(r[1])).date() for r in usage + paid if utc(r[1])<=utc(now)])
    first = max(earliest, datetime.fromisoformat(day(now)).date() - timedelta(days=days-1))
    dates = [(first + timedelta(days=i)).isoformat() for i in range((datetime.fromisoformat(day(now)).date()-first).days+1)]
    rows = {d: dict(start=set(), issued=set(), activated=set(), buyers=set(), orders=set()) for d in dates}
    markers = await db.execute(select(AppSetting.key, AppSetting.updated_at).where(
        AppSetting.key.startswith(PREFIX, autoescape=True) | AppSetting.key.startswith(LEGACY_PREFIX, autoescape=True)))
    for key, at in markers:
        legacy = key.startswith(LEGACY_PREFIX)
        parts = key[len(LEGACY_PREFIX if legacy else PREFIX):].split(':')
        if len(parts)!=3 or parts[1] not in {'start','issued'}:
            continue
        date = day(at) if legacy and at is not None else parts[0]
        if date in rows:
            rows[date][parts[1]].add(parts[2])
    for fp, at in usage:
        if utc(at) <= utc(now) and day(at) in rows:
            rows[day(at)]['activated'].add(fp)
    for order, at, user in paid:
        if utc(at) <= utc(now) and day(at) in rows:
            rows[day(at)]['buyers'].add(user); rows[day(at)]['orders'].add(order)
    result = []
    for date, row in rows.items():
        counts = {k:len(v) for k,v in row.items()}
        if date < day(since):
            counts['start'] = counts['issued'] = None
        result.append((date, counts))
    return since, result
