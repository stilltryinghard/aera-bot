"""Website login approval: Telegram identity comes only from private Bot API updates."""

import hashlib
import json
import os
import re

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from redis.asyncio import Redis

router = Router(name="website_login")
REQUEST = re.compile(r"^[A-Za-z0-9_-]{43}$")
CLAIM = """
local raw=redis.call('GET',KEYS[1]); if not raw then return '' end
local flow=cjson.decode(raw)
if flow.status ~= 'pending' or (flow.pending_user and tostring(flow.pending_user) ~= ARGV[1]) then return '' end
flow.pending_user=ARGV[1]
redis.call('SET',KEYS[1],cjson.encode(flow),'KEEPTTL')
return flow.code
"""
APPROVE = """
local raw=redis.call('GET',KEYS[1]); if not raw then return 0 end
local flow=cjson.decode(raw)
if flow.status ~= 'pending' or tostring(flow.pending_user) ~= ARGV[1] then return 0 end
flow.telegram_id=ARGV[1]; flow.status=ARGV[2]
redis.call('SET',KEYS[1],cjson.encode(flow),'KEEPTTL')
return 1
"""


def key(request):
    return "aera-web:bot-flow:" + hashlib.sha256(request.encode()).hexdigest()


async def operation(script, request, *args):
    if not REQUEST.fullmatch(request):
        return None
    from app.config import get_settings, redis_options

    cache = Redis.from_url(
        os.environ["AERA_WEB_LOGIN_REDIS_URL"],
        decode_responses=True,
        **redis_options(get_settings()),
    )
    try:
        return await cache.eval(script, 1, key(request), *args)
    finally:
        await cache.aclose()


@router.message(F.chat.type == "private", F.text.regexp(r"^/start(?:@\w+)? web_[A-Za-z0-9_-]{43}$"))
async def start(message: Message):
    request = message.text.split()[1][4:]
    try:
        code = await operation(CLAIM, request, str(message.from_user.id))
        if code:
            from app.config import get_settings
            from app.core.security import TokenVault
            from app.db.session import sessions
            from app.services.commerce import CommerceService

            async with sessions.begin() as db:
                user = await CommerceService(db, TokenVault(get_settings().app_secret)).user(
                    message.from_user.id,
                    username=message.from_user.username,
                    first_name=message.from_user.first_name,
                )
                if not user.is_active or user.is_blocked:
                    await message.answer("Профиль AERA недоступен. Обратитесь в поддержку.")
                    return
            raw = await operation("return redis.call('GET',KEYS[1])", request)
            plan_id = json.loads(raw).get("plan_id") if raw else None
            checkout_method = json.loads(raw).get("checkout_method", "stars") if raw else "stars"
            code = await operation(APPROVE, request, str(message.from_user.id), "approved")
    except Exception:
        await message.answer("Вход временно недоступен. Попробуйте снова на сайте.")
        return
    if not code:
        await message.answer("Запрос входа истёк или уже использован. Начните заново на сайте.")
        return
    if plan_id:
        from app.bot.portal import send_selected_checkout

        await send_selected_checkout(message, plan_id, checkout_method)
        return
    await message.answer(
        "✅ Вход на сайт AERA выполнен. Вернитесь в исходную вкладку браузера — личный кабинет откроется автоматически."
    )


@router.callback_query(F.data.startswith("webok:") | F.data.startswith("webno:"))
async def confirm(call: CallbackQuery):
    if not call.message or call.message.chat.type != "private":
        await call.answer("Вход доступен только в личном чате.", show_alert=True)
        return
    accepted = call.data.startswith("webok:")
    try:
        result = await operation(
            APPROVE,
            call.data.split(":", 1)[1],
            str(call.from_user.id),
            "approved" if accepted else "cancelled",
        )
    except Exception:
        await call.answer("Попробуйте снова позже.", show_alert=True)
        return
    await call.answer(
        "Вход подтверждён"
        if result and accepted
        else "Вход отменён"
        if result
        else "Запрос уже завершён или истёк",
        show_alert=not bool(result),
    )
    if result:
        await call.message.edit_text(
            "✅ Вход подтверждён. Вернитесь в браузер, где начали вход: кабинет откроется автоматически."
            if accepted
            else "Вход отменён."
        )
