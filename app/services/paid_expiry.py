"""Expiry notices for the finite paid/manual access shown in the current cabinet."""
import hashlib
import json
from datetime import UTC,datetime,timedelta
from urllib.parse import urlsplit
from cryptography.fernet import InvalidToken
from sqlalchemy import select
from app.db.models import PaidLink,ManualAccess,User,AccessRenewal,SaleOrder,Payment
from app.core.security import TokenVault
from app.services.commerce import utc
from app.subscription.multinode import panel_state
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

KIND='PAID_EXPIRY'
MODELS={'paid':PaidLink,'manual':ManualAccess}


def message(hours):
    if hours == 24:
        title = '⏳ <b>Ваша подписка заканчивается завтра</b>'
        first = 'Продлите доступ заранее 💛'
    elif hours == 1:
        title = '⌛ <b>До окончания подписки остался час</b>'
        first = 'Продлите доступ удобным способом 💛'
    else:
        raise ValueError('Unsupported paid notice')
    return (title + '\n\n<blockquote>' + first + '\n'
            'Сохраните текущий тариф или выберите другой.\n'
            'Подберите подходящий срок и количество устройств.</blockquote>'
            '\n\nКоманда AERA ©')


def markup(kind=None, identity=None):
    renew = 'renew:' + kind + ':' + identity if kind in MODELS and identity else 'v2:renew'
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🔄 Продлить подписку', callback_data=renew)],
        [InlineKeyboardButton(text='⚙️ Изменить тариф', callback_data='plans')],
        [InlineKeyboardButton(text='👤 Личный кабинет', callback_data='profile')],
    ])


def token(kind,row,hours):
    return json.dumps(dict(kind=kind,id=row.id,user=row.user_id,expires=utc(row.expires_at).isoformat(),
        link=hashlib.sha256(row.link_encrypted.encode()).hexdigest(),hours=hours),sort_keys=True)


async def eligible(db,text,settings,*,user_id=None,now=None):
    now=now or datetime.now(UTC)
    try:
        data=json.loads(text)
        if not isinstance(data,dict) or set(data)!={'kind','id','user','expires','link','hours'}:
            return False
        kind=data['kind'];hours=data['hours']
        if kind not in MODELS or type(hours) is not int or hours not in (24,1):
            return False
        row=await db.get(MODELS[kind],data['id'],populate_existing=True)
        if (not row or not row.user_id or (user_id is not None and row.user_id!=user_id)
                or not row.expires_at or not row.link_encrypted or not row.started_at
                or token(kind,row,hours)!=text):
            return False
        if kind=='paid' and (row.status!='ACTIVE' or not row.issued_at or not row.checked_at
                or not timedelta(0)<=now-utc(row.checked_at)<timedelta(minutes=2)):
            return False
        left=utc(row.expires_at)-now
        if not (timedelta(hours=23)<left<=timedelta(hours=24) if hours==24 else timedelta(0)<left<=timedelta(hours=1)):
            return False
        user=await db.get(User,row.user_id,populate_existing=True)
        if not user or not user.is_active or user.is_blocked:
            return False
        vault=TokenVault(settings.app_secret)
        state=await panel_state(db,vault,now)
        identity=urlsplit(vault.reveal(row.link_encrypted)).username
        entry=(state or {}).get('entries',{}).get(identity,{})
        # Unlimited access and panel/DB drift must never produce a payment reminder.
        expiry=int(utc(row.expires_at).timestamp()*1000)
        if not entry.get('enabled') or entry.get('status')!='ACTIVE' or entry.get('expiry_ms')!=expiry or expiry<=0:
            return False
        # A paid renewal may be waiting for the panel; don't ask the customer to pay again.
        pending=await db.scalar(select(AccessRenewal.id).join(SaleOrder,SaleOrder.id==AccessRenewal.order_id)
            .join(Payment,Payment.id==SaleOrder.payment_id).where(AccessRenewal.user_id==row.user_id,
                AccessRenewal.access_kind==kind,AccessRenewal.access_id==row.id,AccessRenewal.status!='DONE',
                SaleOrder.paid_at.is_not(None),Payment.status=='PAID',Payment.paid_at.is_not(None)).limit(1))
        return pending is None
    except (ValueError,TypeError,KeyError,AttributeError,InvalidToken):
        return False


async def queue_expiry_notices(sessions,settings,*,now=None):
    from app.services.notifications import enqueue
    now=now or datetime.now(UTC)
    async with sessions.begin() as db:
        for kind,model in MODELS.items():
            rows=list(await db.scalars(select(model).where(model.user_id.is_not(None),
                model.expires_at>now,model.expires_at<=now+timedelta(hours=24))))
            for row in rows:
                if not row.link_encrypted:
                    continue
                for hours in (24,1):
                    text=token(kind,row,hours)
                    if await eligible(db,text,settings,user_id=row.user_id,now=now):
                        # Device children are deliberately not scanned: one notice per subscription.
                        key='paid-expiry:'+hashlib.sha256(text.encode()).hexdigest()
                        await enqueue(db,key,row.user_id,KIND,text)
