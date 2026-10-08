import logging
import time

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message
from redis.asyncio import Redis

from app.bot.texts import ru
from app.config import get_settings, redis_options
from app.db.session import sessions
from app.services.settings import setting


class GuardMiddleware(BaseMiddleware):
    def __init__(self):
        settings = get_settings()
        self.redis = Redis.from_url(settings.redis_url, **redis_options(settings))

    async def __call__(self, handler, event, data):
        if not isinstance(event, (Message, CallbackQuery)) or not event.from_user:
            return await handler(event, data)
        # Payment receipts must never be dropped by rate limiting or maintenance.
        if isinstance(event, Message) and event.successful_payment:
            return await handler(event, data)
        user_id = event.from_user.id
        try:
            key = f"bot-rate:{user_id}:{int(time.time()) // 60}"
            count = await self.redis.incr(key)
            if count == 1:
                await self.redis.expire(key, 120)
            if count > 45:
                if isinstance(event, CallbackQuery):
                    await event.answer(ru.RATE_LIMIT, show_alert=True)
                else:
                    await event.answer(ru.RATE_LIMIT)
                return
            async with sessions() as db:
                maintenance = await setting(db, "maintenance_mode", "false")
            if maintenance == "true" and user_id not in get_settings().admin_ids:
                if isinstance(event, CallbackQuery):
                    await event.answer(ru.MAINTENANCE, show_alert=True)
                else:
                    await event.answer(ru.MAINTENANCE)
                return
            return await handler(event, data)
        except Exception as error:
            if isinstance(event, CallbackQuery) and isinstance(error, TelegramBadRequest):
                reason = error.message.lower()
                if "query is too old" in reason or "query id is invalid" in reason:
                    # Pending clicks after downtime cannot be acknowledged again.
                    # Stop here instead of retrying the expired answer or payment action.
                    logging.getLogger("aera").info("expired_callback_ignored")
                    return
            logging.getLogger("aera").error("bot_failed type=%s", type(error).__name__)
            if isinstance(event, Message) and event.successful_payment:
                raise
            if isinstance(event, CallbackQuery):
                await event.answer(ru.ERROR, show_alert=True)
            else:
                await event.answer(ru.ERROR)
