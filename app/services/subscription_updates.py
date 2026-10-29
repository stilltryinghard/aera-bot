"""Owner-triggered subscription update notices; no automatic release watcher."""
import re
from datetime import UTC,datetime,timedelta
from aiogram.types import InlineKeyboardButton as B,InlineKeyboardMarkup as M
from sqlalchemy import select,or_,update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import AppSetting,User,PaidLink,TrialLink,ManualAccess,FamilyAccess

KIND='SUBSCRIPTION_UPDATE'
DRAFT_PREFIX='subscription_update_draft:'
CURRENT_PREFIX='subscription_update_current:'
MESSAGE='<b>Обновления AERA ⚡</b>\n\n<blockquote>Мы обновили настройки подключения 🚀\n\n<a href="https://t.me/aeraavpn">Информация об изменениях</a>\n\nОбновите вашу подписку в INCY или Happ 🔄</blockquote>\n\nAERA ©'

async def message(db):
    from app.services.news_links import render
    return await render(db, MESSAGE)


def markup():
    return M(inline_keyboard=[
        [B(text='💡 Инструкция',url='https://t.me/aerasetting'),B(text='💬 Поддержка',url='https://t.me/aerasuppbot')],
        [B(text='👤 Личный кабинет',callback_data='profile')]])

async def has_subscription(db,user,now=None):
    if not user or not user.is_active or user.is_blocked:return False
    now=now or datetime.now(UTC)
    for model in [PaidLink,TrialLink]:
        q=select(model.id).where(model.user_id==user.id,model.issued_at.is_not(None),
            model.status.in_(['ACTIVE','WAITING','ACTIVATING']),or_(model.expires_at.is_(None),model.expires_at>now))
        if model is TrialLink:
            q=q.where(model.pending_uuid.is_(None),or_(model.started_at.is_not(None),model.issued_at>now-timedelta(hours=1)))
        if await db.scalar(q.limit(1)):return True
    if await db.scalar(select(ManualAccess.id).where(ManualAccess.user_id==user.id,ManualAccess.expires_at>now,ManualAccess.link_encrypted.is_not(None)).limit(1)):return True
    family=await db.scalar(select(FamilyAccess).where(FamilyAccess.user_id==user.id,FamilyAccess.is_active.is_(True),FamilyAccess.link_encrypted.is_not(None)))
    if family:
        from app.subscription.multinode import owned_panel_url
        from app.config import get_settings
        from app.core.security import TokenVault
        settings=get_settings()
        return bool(await owned_panel_url(db,user,family,TokenVault(settings.app_secret),settings))
    return False

async def recipients(db,now=None):
    # Only actual assigned records are candidates; the exact predicate is rechecked.
    relevant=or_(*[select(m.id).where(m.user_id==User.id).exists() for m in [PaidLink,TrialLink,ManualAccess,FamilyAccess]])
    result=[]
    for user in await db.scalars(select(User).where(User.is_active.is_(True),User.is_blocked.is_(False),relevant)):
        if await has_subscription(db,user,now):result.append(user.id)
    return result

async def draft(db,owner_id,*,new=False,now=None):
    import json
    from uuid import uuid4
    now=now or datetime.now(UTC)
    pointer=await db.get(AppSetting,CURRENT_PREFIX+str(owner_id))
    if pointer and not new:
        row=await db.get(AppSetting,DRAFT_PREFIX+pointer.value)
        value=json.loads(row.value) if row else None
        if value and value['status']=='PUBLISHED':return value
        if value and value['status']=='DRAFT' and now.timestamp()-value['created']<1800:return value
    token=uuid4().hex
    value=dict(token=token,owner=owner_id,status='DRAFT',created=now.timestamp())
    db.add(AppSetting(key=DRAFT_PREFIX+token,value=json.dumps(value)))
    if pointer:pointer.value=token
    else:db.add(AppSetting(key=CURRENT_PREFIX+str(owner_id),value=token))
    await db.flush()
    return value

async def owned_draft(db,token,owner_id,settings,*,now=None):
    import json
    if owner_id not in settings.admin_ids or not isinstance(token,str) or not re.fullmatch('[0-9a-f]{32}',token):raise ValueError('Owner required')
    # Lock before reading; duplicate concurrent clicks cannot publish twice.
    await db.execute(update(AppSetting).where(AppSetting.key==DRAFT_PREFIX+token).values(value=AppSetting.value))
    row=await db.get(AppSetting,DRAFT_PREFIX+token)
    if not row:raise ValueError('Draft missing')
    value=json.loads(row.value)
    if value['owner']!=owner_id:raise ValueError('Draft owner mismatch')
    pointer=await db.get(AppSetting,CURRENT_PREFIX+str(owner_id))
    if not pointer or pointer.value!=token:raise ValueError('Open the current notice')
    if value['status']=='DRAFT' and (now or datetime.now(UTC)).timestamp()-value['created']>=1800:raise ValueError('Draft expired')
    return row,value

async def publish(db,token,owner_id,settings,now=None):
    import json
    row,value=await owned_draft(db,token,owner_id,settings,now=now)
    if value['status']=='PUBLISHED':return value['count']
    if value['status']!='DRAFT':raise ValueError('Draft cancelled')
    from app.services.notifications import enqueue
    ids=await recipients(db,now)
    for identity in ids:await enqueue(db,f'subscription-update:{token}:{identity}',identity,KIND,token)
    value.update(status='PUBLISHED',count=len(ids),published=(now or datetime.now(UTC)).timestamp())
    row.value=json.dumps(value)
    return len(ids)

async def cancel(db,token,owner_id,settings):
    import json
    row,value=await owned_draft(db,token,owner_id,settings)
    if value['status']!='DRAFT':raise ValueError('Already published')
    value['status']='CANCELLED';row.value=json.dumps(value)

async def published(db,token):
    import json
    if not re.fullmatch('[0-9a-f]{32}',token):return False
    row=await db.get(AppSetting,DRAFT_PREFIX+token)
    return bool(row and json.loads(row.value).get('status')=='PUBLISHED')

async def progress(db,token):
    from sqlalchemy import func
    from app.db.models import Notification
    prefix=f'subscription-update:{token}:'
    counts=dict((await db.execute(select(Notification.status,func.count()).where(Notification.dedupe_key.startswith(prefix,autoescape=True)).group_by(Notification.status))).all())
    return ('📨 Уведомление подписчикам\n\n'
            f"Отправлено: {counts.get('SENT',0)}\n"
            f"В очереди: {counts.get('PENDING',0)+counts.get('SENDING',0)}\n"
            f"Недоступны: {counts.get('BLOCKED',0)}\n"
            f"Пропущено: {counts.get('CANCELLED',0)}\n"
            f"Ошибки: {counts.get('FAILED',0)}\n"
            f"Требуют проверки доставки: {counts.get('UNCERTAIN',0)}")

