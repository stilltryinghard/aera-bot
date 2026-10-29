"""Only the actual configured AERA channel can update the latest-news pointer."""
from aiogram import Router
from app.db.session import sessions
from app.services.news_links import CHANNEL, observe

router = Router(name='aera_news_posts')

@router.channel_post()
@router.edited_channel_post()
async def channel_post(message):
    if message.chat.type != 'channel' or (message.chat.username or '').casefold() != CHANNEL:
        return
    if not (message.text or message.caption or message.photo or message.video or message.document or message.animation or message.poll):
        return
    async with sessions.begin() as db:
        await observe(db, message.chat.id, message.message_id)
