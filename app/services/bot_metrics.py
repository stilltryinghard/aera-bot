"""Unique section clicks; only action-specific HMAC markers and the value 1."""
import hashlib
import hmac
import logging
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery
from sqlalchemy import select,func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import AppSetting,User

PREFIX='section_seen_v1:'
LABELS={
 'cabinet':'Личный кабинет', 'plans':'Выбор подписки', 'trial':'Пробная подписка',
 'referral':'Партнёрская программа', 'about':'О сервисе', 'devices':'Настройка тарифа',
 'checkout':'Кнопка оплаты', 'renew':'Продление', 'sbp':'Выбор СБП',
 'crypto':'Выбор криптовалюты', 'stars':'Выбор Stars', 'order':'Заказ',
 'cancel':'Отмена заказа', 'connect':'Подключение', 'support':'Поддержка',
 'guide':'Инструкции в боте', 'qr':'Партнёрский QR', 'withdraw':'Вывод средств',
 'privacy':'Политика данных', 'language':'Язык',
}

def section(data):
    """Never persist callback payloads, order IDs, tariff IDs or arbitrary keys."""
    if not isinstance(data,str):return None
    exact={'menu':'cabinet','profile':'cabinet','plans':'plans','devices':'plans',
           'hiddify:plans':'plans','trial':'trial','trial:get':'trial','referral':'referral',
           'v2:about':'about','v2:referral_qr':'qr','v2:withdraw':'withdraw',
           'connect':'connect','support':'support','how':'guide','privacy':'privacy','language':'language'}
    if data in exact:return exact[data]
    if data.startswith(('profile:','v2:cabinet:')):return 'cabinet'
    parts=data.split(':')
    if len(parts)==4 and parts[:2] in [['v2','devices'],['v2','checkout']]:
        from app.services.tariffs_v2 import price
        try:price(int(parts[2]),int(parts[3]))
        except (ValueError,TypeError):return None
        return 'devices' if parts[1]=='devices' else 'checkout'
    if len(parts)==3 and parts[0]=='platega' and parts[1] in {'sbp','crypto'} and parts[2]:return parts[1]
    if len(parts)==3 and parts[0]=='renew' and parts[1] in {'paid','trial','manual','family'} and parts[2]:return 'renew'
    if len(parts)==2 and parts[1]:
        if parts[0]=='guide' and parts[1] in {'ios','android','windows','macos'}:return 'guide'
        if parts[0]=='privacy' and parts[1] in {'0','1','2'}:return 'privacy'
        return {'order':'order','terms':'stars','stars':'stars','cancelorder':'cancel',
                'plan':'checkout','family':'plans','changeplan':'plans'}.get(parts[0])
    return None

async def record_section(db,telegram_id,action,secret):
    if action not in LABELS:raise ValueError('Unknown metric')
    from app.services.analytics_report import record_section_day
    await record_section_day(db,telegram_id,action,secret)
    marker=hmac.new(secret.encode(),f'aera-section-v1:{action}:{telegram_id}'.encode(),hashlib.sha256).hexdigest()
    insert=pg_insert if db.bind.dialect.name=='postgresql' else sqlite_insert
    await db.execute(insert(AppSetting).values(key=PREFIX+action+':'+marker,value='1')
                     .on_conflict_do_nothing(index_elements=[AppSetting.key]))

async def section_counts(db):
    # Strip the fixed 64-character digest plus its separator; only aggregates leave DB.
    group=func.substr(AppSetting.key,1,func.length(AppSetting.key)-65)
    result=await db.execute(select(group,func.count()).where(AppSetting.key.startswith(PREFIX)).group_by(group))
    counts={k[len(PREFIX):]:n for k,n in result}
    return {action:counts.get(action,0) for action in LABELS}

REPORT_ACTIONS = ('cabinet', 'plans', 'trial', 'referral', 'about', 'devices',
                  'checkout', 'sbp', 'crypto', 'stars', 'cancel', 'qr', 'withdraw')

async def report(db):
    from app.services.start_counter import start_count
    from app.services.funnel_metrics import outcomes
    from app.services.analytics_report import formatted_report
    from app.config import get_settings
    return formatted_report(await outcomes(db,get_settings().app_secret), await start_count(db), await section_counts(db))


class SectionMetricsMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        action=section(event.data) if isinstance(event,CallbackQuery) else None
        if action and event.from_user and not event.from_user.is_bot and event.message and event.message.chat.type=='private':
            from app.db.session import sessions
            from app.config import get_settings
            try:
                async with sessions.begin() as db:
                    user=await db.scalar(select(User).where(User.telegram_id==event.from_user.id))
                    if user and user.is_active and not user.is_blocked:
                        await record_section(db,event.from_user.id,action,get_settings().app_secret)
            except Exception as error:
                # Analytics must never block navigation or an actual payment handler.
                logging.getLogger('aera').warning('section_metrics_failed type=%s',type(error).__name__)
        return await handler(event,data)
