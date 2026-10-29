"""Read-only business outcomes; retain trial assignment history across unused recycling."""
from datetime import UTC,datetime,timedelta
import re
from sqlalchemy import select,func,union
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import AppSetting,TrialUsage,TrialLink,PaidLink,SaleOrder,Payment,SupportTicket,User,AccessRenewal

ISSUED='trial_issued_history_v1:'

async def preserve_trial_issue(db,digest):
    if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Invalid trial fingerprint')
    insert=pg_insert if db.bind.dialect.name=='postgresql' else sqlite_insert
    await db.execute(insert(AppSetting).values(key=ISSUED+digest,value='1').on_conflict_do_nothing(index_elements=[AppSetting.key]))

def issued_query():
    return union(select(TrialUsage.fingerprint.label('fingerprint')),
                 select(func.substr(AppSetting.key,len(ISSUED)+1).label('fingerprint')).where(
                     AppSetting.key.startswith(ISSUED,autoescape=True),AppSetting.value=='1'))

async def outcomes(db,secret):
    from app.services.trial_pool import fingerprint
    now=datetime.now(UTC);fresh=now-timedelta(minutes=2)
    issued=int(await db.scalar(select(func.count()).select_from(issued_query().subquery())) or 0)
    activated=int(await db.scalar(select(func.count()).select_from(TrialUsage).where(TrialUsage.activated_at.is_not(None))) or 0)
    # Canonical order + actual confirmed payment + matching owner. No pending,
    # manual access binding, reward/free order or chargeback is a paid purchase.
    paid=list((await db.execute(select(SaleOrder.id,SaleOrder.paid_at,SupportTicket.user_id,User.telegram_id)
          .join(Payment,Payment.id==SaleOrder.payment_id)
          .join(SupportTicket,SupportTicket.id==SaleOrder.ticket_id)
          .join(User,User.id==SupportTicket.user_id)
          .where(SaleOrder.paid_at.is_not(None),Payment.status=='PAID',Payment.paid_at.is_not(None),
                 Payment.provider.in_(['platega','telegram_stars']),
                 Payment.user_id==SupportTicket.user_id,Payment.plan_id==SaleOrder.plan_id))).all())
    buyers={r.user_id for r in paid}
    usage={fp:at for fp,at in await db.execute(select(TrialUsage.fingerprint,TrialUsage.activated_at).where(TrialUsage.activated_at.is_not(None)))}
    from app.services.commerce import utc
    after_trial={r.user_id for r in paid if (at:=usage.get(fingerprint(r.telegram_id,secret))) and utc(at)<=utc(r.paid_at)}
    paid_ids={r.id for r in paid}
    fulfilled=set(await db.scalars(select(PaidLink.order_id).where(PaidLink.order_id.in_(paid_ids),PaidLink.issued_at.is_not(None)))) if paid_ids else set()
    renewed=set(await db.scalars(select(AccessRenewal.order_id).where(AccessRenewal.order_id.in_(paid_ids),AccessRenewal.status=='DONE'))) if paid_ids else set()
    manual_done=set(await db.scalars(select(SaleOrder.id).join(SupportTicket).where(SaleOrder.id.in_(paid_ids),SupportTicket.status=='CONNECTED'))) if paid_ids else set()
    free_trial=int(await db.scalar(select(func.count()).select_from(TrialLink).where(TrialLink.status=='FREE',TrialLink.user_id.is_(None),TrialLink.pending_uuid.is_(None),TrialLink.checked_at>fresh)) or 0)
    free_paid=int(await db.scalar(select(func.count()).select_from(PaidLink).where(PaidLink.status=='FREE',PaidLink.order_id.is_(None),PaidLink.user_id.is_(None),PaidLink.checked_at>fresh)) or 0)
    return dict(trial_issued=issued,trial_activated=activated,buyers=len(buyers),paid_orders=len(paid_ids),
                trial_buyers=len(after_trial),awaiting_access=len(paid_ids-fulfilled-renewed-manual_done),free_trial=free_trial,free_paid=free_paid)

async def report_outcomes(db,secret):
    n=await outcomes(db,secret)
    labels=[('Пробную получили, история','trial_issued'),('Пробную активировали, история','trial_activated'),
            ('Покупателей, подтверждено','buyers'),('Оплаченных заказов, подтверждено','paid_orders'),
            ('Оплатили после активации пробной','trial_buyers'),('Оплаты без отметки выдачи','awaiting_access'),
            ('Пробных готово сейчас','free_trial'),('Платных готово сейчас','free_paid')]
    lines = [f'{label}: {n[key]}' for label,key in labels]
    def percent(numerator, denominator):
        return f'{100*numerator/denominator:.1f}%'.replace('.', ',') if denominator else '—'
    lines += [
        'Конверсия пробная → подключение: ' + percent(n['trial_activated'], n['trial_issued']),
        'Конверсия подключение → покупка: ' + percent(n['trial_buyers'], n['trial_activated']),
        'Конверсия пробная → покупка после подключения: ' + percent(n['trial_buyers'], n['trial_issued']),
    ]
    return lines
