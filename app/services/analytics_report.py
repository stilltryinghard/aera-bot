"""Owner report presentation and dated analytics; never change access/payment state."""
from datetime import UTC, datetime
import hashlib
import hmac
import json
from html import escape
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import AppSetting, User, TrialUsage, SaleOrder, Payment, SupportTicket
from app.services.daily_metrics import day, utc, PREFIX, LEGACY_PREFIX, daily_rows

SECTION_PREFIX = 'daily_section_22_v1:'
SECTION_SINCE = 'daily_section_started_v1'
STOCK_PREFIX = 'daily_stock_22_v1:'
SHORT_LABELS = {'cabinet':'ЛК','plans':'Выбор подписки','trial':'Пробная подписка',
 'referral':'Партнёрка','about':'О сервисе','devices':'Настройка тарифа',
 'checkout':'Кнопка оплаты','sbp':'Выбор СБП','crypto':'Выбор криптовалюты',
 'stars':'Выбор Stars','cancel':'Отмена заказа','qr':'Партнёрский QR','withdraw':'Вывод средств'}

def ratio(numerator, denominator):
    return numerator / denominator if denominator else None

def percent(value):
    return f'{value*100:.1f}%'.replace('.', ',') if value is not None else '—'

def formatted_report(n, starts, sections):
    def block(lines):
        return '<blockquote><b>' + escape('\n'.join(lines)) + '</b></blockquote>'
    return '\n\n'.join([
        '<b>📊 AERA · статистика</b>\n' + block([
            f'Старт — {starts}',
            f'Пробная: получили / активировали — {n["trial_issued"]} / {n["trial_activated"]}',
            f'Покупателей — {n["buyers"]}', f'Оплата — {n["paid_orders"]}',
            f'Оплата после пробной — {n["trial_buyers"]}']),
        '<b>Остаток доступов</b>\n' + block([
            f'Пробных — {n["free_trial"]}', f'Платных — {n["free_paid"]}']),
        '<b>Конверсия</b>\n' + block([
            f'Пробная → подключение — {n["trial_activated"]} · {percent(ratio(n["trial_activated"],n["trial_issued"]))}',
            f'Пробная → покупка — {n["trial_buyers"]} · {percent(ratio(n["trial_buyers"],n["trial_issued"]))}']),
        '<b>Разделы бота</b>\n' + block([f'{label} — {sections.get(action,0)}' for action,label in SHORT_LABELS.items()])
    ])

async def record_section_day(db, telegram_id, action, secret, at=None):
    if action not in SHORT_LABELS:
        return
    at = at or datetime.now(UTC)
    digest = hmac.new(secret.encode(), f'aera-section-v1:{action}:{telegram_id}'.encode(), hashlib.sha256).hexdigest()
    insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
    await db.execute(insert(AppSetting).values(key=f'{SECTION_PREFIX}{day(at)}:{action}:{digest}',value='1')
        .on_conflict_do_nothing(index_elements=[AppSetting.key]))

async def capture_stock(db, settings):
    from app.services.funnel_metrics import outcomes
    n = await outcomes(db, settings.app_secret)
    now = datetime.now(UTC)
    payload = json.dumps({'at':now.isoformat(),'trial':n['free_trial'],'paid':n['free_paid']})
    insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
    await db.execute(insert(AppSetting).values(key=STOCK_PREFIX+day(now),value=payload)
        .on_conflict_do_update(index_elements=[AppSetting.key],set_={'value':payload,'updated_at':now}))

async def daily_details(db, secret, now=None):
    now = now or datetime.now(UTC)
    since, basic = await daily_rows(db, now, days=50000)
    section_setting = await db.get(AppSetting, SECTION_SINCE)
    section_since = utc(datetime.fromisoformat(section_setting.value)) if section_setting else None
    rows = {date:{**values,'trial_buyers':0,'free_trial':None,'free_paid':None,
                 'stock_at':None,'cohort_activated':None,'cohort_buyers':None,
                 'activation_rate':None,'purchase_rate':None,
                 'sections':{a:None for a in SHORT_LABELS}} for date,values in basic}
    for date,row in rows.items():
        if section_since and date > day(section_since):
            row['sections'] = {a:0 for a in SHORT_LABELS}
    markers = await db.execute(select(AppSetting.key,AppSetting.value,AppSetting.updated_at).where(
        AppSetting.key.startswith(SECTION_PREFIX,autoescape=True) |
        AppSetting.key.startswith(STOCK_PREFIX,autoescape=True) |
        AppSetting.key.startswith(PREFIX,autoescape=True) |
        AppSetting.key.startswith(LEGACY_PREFIX,autoescape=True)))
    issued, sections = {}, {}
    for key,value,at in markers:
        if key.startswith(STOCK_PREFIX):
            date=key[len(STOCK_PREFIX):]
            if date in rows:
                stock=json.loads(value)
                rows[date].update(free_trial=stock['trial'],free_paid=stock['paid'],stock_at=stock['at'])
        elif key.startswith(SECTION_PREFIX):
            date,action,digest=key[len(SECTION_PREFIX):].split(':')
            if date in rows and action in SHORT_LABELS:
                sections.setdefault((date,action),set()).add(digest)
        else:
            legacy=key.startswith(LEGACY_PREFIX)
            parts=key[len(LEGACY_PREFIX if legacy else PREFIX):].split(':')
            if len(parts)!=3 or parts[1]!='issued' or at is None:
                continue
            date=day(at) if legacy else parts[0]
            if date in rows:
                people=issued.setdefault(date,{})
                people[parts[2]]=min(people.get(parts[2],utc(at)),utc(at))
    for (date,action),people in sections.items():
        if rows[date]['sections'][action] is not None:
            rows[date]['sections'][action]=len(people)
    users=list(await db.execute(select(User.id,User.telegram_id)))
    by_digest={hmac.new(secret.encode(),f'aera-daily-v1:{tg}'.encode(),hashlib.sha256).hexdigest():(uid,tg) for uid,tg in users}
    usage={fp:utc(at) for fp,at in await db.execute(select(TrialUsage.fingerprint,TrialUsage.activated_at).where(TrialUsage.activated_at.is_not(None)))}
    from app.services.trial_pool import fingerprint
    activated={uid:usage.get(fingerprint(tg,secret)) for uid,tg in users}
    paid=list(await db.execute(select(SaleOrder.paid_at,SupportTicket.user_id)
        .join(Payment,Payment.id==SaleOrder.payment_id).join(SupportTicket,SupportTicket.id==SaleOrder.ticket_id)
        .where(SaleOrder.paid_at.is_not(None),Payment.status=='PAID',Payment.paid_at.is_not(None),
               Payment.provider.in_(['platega','telegram_stars']),Payment.user_id==SupportTicket.user_id,
               Payment.plan_id==SaleOrder.plan_id)))
    purchase_dates={}
    daily_trial_buyers={}
    for at,uid in paid:
        at=utc(at); activation=activated.get(uid)
        if at<=now and activation and activation<=at:
            purchase_dates.setdefault(uid,[]).append(at)
            daily_trial_buyers.setdefault(day(at),set()).add(uid)
    for date,row in rows.items():
        row['trial_buyers']=len(daily_trial_buyers.get(date,set()))
        if date<=day(since):
            continue
        cohort=issued.get(date,{})
        if any(digest not in by_digest for digest in cohort):
            continue  # Missing identities are not silently counted as failed conversion.
        connected,buyers=set(),set()
        for digest,issued_at in cohort.items():
            uid,_=by_digest[digest];activation=activated.get(uid)
            if activation and issued_at<=activation<=now:
                connected.add(uid)
                if any(at>=activation for at in purchase_dates.get(uid,[])):
                    buyers.add(uid)
        row.update(cohort_activated=len(connected),cohort_buyers=len(buyers),
                   activation_rate=ratio(len(connected),len(cohort)),purchase_rate=ratio(len(buyers),len(cohort)))
    return {'since':since.isoformat(),'section_since':section_since.isoformat() if section_since else None,
            'rows':list(rows.items())}
