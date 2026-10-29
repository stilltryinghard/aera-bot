"""Retire trial generations only after a verified, available paid purchase."""
import asyncio
from datetime import UTC,datetime,timedelta
from uuid import uuid4
from urllib.parse import urlsplit,parse_qs
from sqlalchemy import select,update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import TrialLink,PaidLink,SaleOrder,DeviceBinding,User,TrialUsage
from app.core.security import TokenVault
from app.core.trial_policy import DURATION_MS
from app.services.commerce import utc

async def schedule_paid_trials(db,settings,user_id=None):
    query=select(TrialLink).where(TrialLink.user_id.is_not(None),TrialLink.pending_uuid.is_(None),
                                TrialLink.status.in_(['ACTIVE','WAITING','ACTIVATING','EXPIRED','BLOCKED']))
    if user_id:query=query.where(TrialLink.user_id==user_id)
    vault=TokenVault(settings.app_secret);count=0
    from app.subscription.multinode import owned_panel_url
    from app.services.trial_pool import fingerprint
    for trial in await db.scalars(query):
        user=await db.get(User,trial.user_id)
        if not user or not user.is_active or user.is_blocked:continue
        purchases=list(await db.scalars(select(PaidLink).join(SaleOrder,SaleOrder.id==PaidLink.order_id).where(
            PaidLink.user_id==user.id,PaidLink.issued_at.is_not(None),SaleOrder.paid_at.is_not(None),
            PaidLink.status.in_(['ACTIVE','WAITING','ACTIVATING']),PaidLink.checked_at>datetime.now(UTC)-timedelta(minutes=2))))
        available=False
        for paid in purchases:
            if await owned_panel_url(db,user,paid,vault,settings):available=True;break
        if not available:continue
        await db.execute(update(User).where(User.id==user.id).values(is_active=User.is_active))
        await db.refresh(trial)
        if trial.pending_uuid or trial.user_id!=user.id:continue
        trial.pending_uuid=str(uuid4());trial.recycle_reason='PURCHASED';trial.status='RECYCLING'
        insert=pg_insert if db.bind.dialect.name=='postgresql' else sqlite_insert
        await db.execute(insert(TrialUsage).values(fingerprint=fingerprint(user.telegram_id,settings.app_secret),activated_at=trial.started_at)
                         .on_conflict_do_nothing(index_elements=[TrialUsage.fingerprint]))
        user.trial_used=True;count+=1
    return count

async def prepare_paid_recycle(sessions,settings,config,identity,trial_client):
    from app.services.device_access import provision
    from app.subscription.multinode import panel_state
    from scripts.panel_sync import bridge,NODES,mirror_uuid
    vault=TokenVault(settings.app_secret)
    async with sessions.begin() as db:
        row=await db.get(TrialLink,identity)
        if not row or row.recycle_reason!='PURCHASED' or not row.pending_uuid or not row.user_id:return False
        old,new,email=row.client_uuid,row.pending_uuid,row.panel_email
        marked=utc(row.updated_at).timestamp()
        if await db.scalar(select(PaidLink.id).where(PaidLink.client_uuid==old)):
            raise ValueError('Trial identity collides with paid access')
        children=list(await db.scalars(select(DeviceBinding).where(DeviceBinding.trial_link_id==row.id,DeviceBinding.parent_uuid==old)))
        ids=[r.id for r in children];child_uuids=[r.client_uuid for r in children]
        for child in children:
            if child.status!='REVOKED':child.status='REVOKING';child.bundle_encrypted=None
        expiry=int(utc(row.expires_at).timestamp()*1000) if row.expires_at else -DURATION_MS
        flow=parse_qs(urlsplit(vault.reveal(row.link_encrypted)).query).get('flow',[''])[0]
    for child_id in ids:
        async with sessions() as db:
            child=await db.get(DeviceBinding,child_id)
            done=child.status=='REVOKED' and child.bundle_encrypted is None
        if not done:await provision(sessions,settings,child_id)
        async with sessions() as db:
            child=await db.get(DeviceBinding,child_id)
            if child.status!='REVOKED' or child.bundle_encrypted:return False
    await asyncio.gather(*(bridge({'version':1,'partial':True,'clients':[dict(uuid=mirror_uuid(n,old),limit_ip=3,
        expiry_ms=expiry,enable=False,flow=flow)]},n) for n in NODES))
    # A completed full sync after the tombstone prevents an older in-flight
    # full sync from re-enabling trial credentials after partial revocation.
    async with sessions() as db:
        state=await panel_state(db,vault)
        if not state or state['checked_at']<marked:return False
        if any(state.get('entries',{}).get(u,{}).get('fi_enabled',False) for u in [old]+child_uuids):return False
    snapshots=await asyncio.gather(*(bridge({'version':1,'clients':[],'inspect_only':True},n) for n in NODES))
    for n,reply in zip(NODES,snapshots):
        if 'states' not in reply:return False
        for u in [old]+child_uuids:
            if reply['states'].get(mirror_uuid(n,u),{}).get('enable',False):return False
    await trial_client.disable_converted_trial(old,new,email)
    return True
