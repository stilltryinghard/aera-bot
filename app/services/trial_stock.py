"""Add-only recoverable trial inventory. Existing clients are never changed."""
import asyncio
import json
from uuid import uuid4
from datetime import UTC,datetime
from sqlalchemy import select,func
from app.db.models import AppSetting,TrialLink,User
from app.core.security import TokenVault
from app.core.trial_policy import DURATION_MS,TRIAL_IP_LIMIT
from app.integrations.xui.v328 import XUI328Client
from app.integrations.xui.trial_keys import replacement_email
from app.services.source_links import primary_link

_lock=asyncio.Lock()
MAX_TOTAL=1000
BATCH=40

async def replenish_trials(sessions,settings,target=40):
    try:
        return await _replenish_trials(sessions,settings,target)
    except Exception:
        from app.services.notifications import enqueue
        try:
            async with sessions.begin() as db:
                for aid in settings.admin_ids:
                    admin=await db.scalar(select(User).where(User.telegram_id==aid))
                    if admin:await enqueue(db,'trial-stock-error:'+datetime.now(UTC).strftime('%Y%m%d%H')+f':{aid}',admin.id,'PORTAL',
                        '⚠️ Не удалось пополнить пробный запас. Бот повторит попытку автоматически; проверьте остаток перед рекламой.')
        except Exception:
            pass
        raise

async def _replenish_trials(sessions,settings,target=40):
    if type(target) is not int or not 1<=target<=200:
        raise ValueError("Invalid trial reserve target")
    async with _lock:
        vault=TokenVault(settings.app_secret)
        async with sessions() as db:
            flag=await db.get(AppSetting,"trial_stock_enabled")
            if not flag or flag.value!="true":return 0
            observer=await db.get(AppSetting,"trial_observer")
            if not observer:raise ValueError("Verified primary observer unavailable")
            config=json.loads(vault.reveal(observer.value))
        writer=XUI328Client(config['base_url'],config['api_token'],config['inbound_id'],allow_writes=True)
        try:
            inbound=await writer.get_inbound(writer.inbound_id)
            primary_link(inbound,"00000000-0000-0000-0000-000000000001","xtls-rprx-vision","validation")
            raw=inbound['settings'];raw=json.loads(raw) if isinstance(raw,str) else raw
            current={c['id']:c for c in raw['clients']}
            async with sessions.begin() as db:
                reserve=await db.scalar(select(func.count()).select_from(TrialLink).where(
                    TrialLink.user_id.is_(None),TrialLink.issued_at.is_(None),
                    TrialLink.status.in_(['FREE','PROVISIONING'])))
                total=await db.scalar(select(func.count()).select_from(TrialLink))
                amount=min(BATCH,max(0,target-reserve),max(0,MAX_TOTAL-total))
                if reserve<target and total>=MAX_TOTAL:
                    from app.services.notifications import enqueue
                    for aid in settings.admin_ids:
                        admin=await db.scalar(select(User).where(User.telegram_id==aid))
                        if admin:await enqueue(db,f'trial-stock-cap:{MAX_TOTAL}:{aid}',admin.id,'PORTAL',
                            '⚠️ Достигнут защитный предел пробного пула. Проверьте запас перед продолжением рекламы.')
                for _ in range(amount):
                    identity=str(uuid4());label=f'AERA Trial {identity[:8]}'
                    db.add(TrialLink(client_uuid=identity,source_uuid=identity,label=label,
                        panel_email=replacement_email(identity),status='PROVISIONING',
                        link_encrypted=vault.cipher.encrypt(primary_link(inbound,identity,'xtls-rprx-vision',label).encode()).decode()))
                await db.flush()
                candidates=[(r.client_uuid,r.panel_email) for r in await db.scalars(select(TrialLink).where(
                    TrialLink.user_id.is_(None),TrialLink.issued_at.is_(None),TrialLink.status=='PROVISIONING'))]
            created=0
            for identity,email in candidates:
                desired=dict(id=identity,email=email,flow='xtls-rprx-vision',limitIp=TRIAL_IP_LIMIT,
                    totalGB=0,expiryTime=-DURATION_MS,enable=True,reset=0,subId=identity.replace('-',''))
                existing=current.get(identity)
                if existing:
                    if any(existing.get(k)!=v for k,v in desired.items()):
                        raise ValueError('Pending trial differs; no existing client changed')
                else:
                    if any(c.get('email')==email for c in current.values()):raise ValueError('Trial email conflict')
                    await writer.request('POST','panel/api/clients/add',{'client':desired,'inboundIds':[writer.inbound_id]})
                    current[identity]=desired;created+=1
            # Fresh read-only observer alone can turn PROVISIONING into FREE.
            return created
        finally:
            await writer.close()
