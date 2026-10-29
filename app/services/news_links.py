"""Direct Telegram news links. Observe posts; edit tracked notices, never broadcast."""
import asyncio
import json
import re
import time
import urllib.request
from uuid import uuid4
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.db.models import AppSetting

CHANNEL = 'aeraavpn'
ROOT_URL = 'https://t.me/' + CHANNEL
LATEST_KEY = 'notice_news_latest_v1'
TRACK_PREFIX = 'notice_news_message_v1:'
CURSOR_KEY = 'notice_news_edit_cursor_v1'
PAUSE_KEY = 'notice_news_edit_pause_v1'
ANCHOR = re.compile(r'(<a\s+href=")https://t\.me/aeraavpn(?:/\d+)?(">\s*Информация об изменениях\s*</a>)')


def _data(row):
    try:
        value = json.loads(row.value) if row else {}
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


async def _ensure(db, key, value='{}'):
    insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
    await db.execute(insert(AppSetting).values(key=key, value=value).on_conflict_do_nothing(index_elements=[AppSetting.key]))


async def _locked(db, key, default='{}'):
    await _ensure(db, key, default)
    await db.execute(update(AppSetting).where(AppSetting.key == key).values(value=AppSetting.value))
    return await db.get(AppSetting, key, populate_existing=True)


def post_id(text):
    match = ANCHOR.search(text or '')
    if not match:
        return 0
    number = re.search(r'/([1-9]\d*)"', match.group(0))
    return int(number.group(1)) if number else 0


def with_link(template, identity):
    url = ROOT_URL + '/' + str(identity) if type(identity) is int and 0 < identity < 2**31 else ROOT_URL
    return ANCHOR.sub(lambda m: m.group(1) + url + m.group(2), template, count=1)


async def render(db, template):
    value = _data(await db.get(AppSetting, LATEST_KEY))
    return with_link(template, value.get('post_id'))


async def observe(db, chat_id, identity, *, source='event', now=None):
    """Out-of-order events never rewind. Three delayed public checks confirm deletion."""
    if type(chat_id) is not int or chat_id >= 0 or type(identity) is not int or not 0 < identity < 2**31:
        return False
    now = time.time() if now is None else now
    row = await _locked(db, LATEST_KEY)
    value = _data(row)
    if value.get('chat_id') not in (None, chat_id):
        return False
    current = value.get('post_id', 0)
    changed = identity > current
    if source == 'public' and identity < current and now - value.get('changed_at', now) >= 180:
        candidate = value.get('lower_candidate')
        count = value.get('lower_count', 0) + 1 if candidate == identity else 1
        value.update(lower_candidate=identity, lower_count=count)
        changed = count >= 3
    elif identity >= current:
        value.pop('lower_candidate', None)
        value.pop('lower_count', None)
    if changed:
        value.update(chat_id=chat_id, post_id=identity, changed_at=now, source=source)
        value.pop('lower_candidate', None)
        value.pop('lower_count', None)
    if source == 'public':
        value['public_checked_at'] = now
    row.value = json.dumps(value)
    return changed


def parse_public(html):
    ids = [int(x) for x in re.findall(r'data-post=["\']aeraavpn/([1-9]\d*)["\']', html, re.I)]
    ids = [n for n in ids if n < 2**31]
    if not ids:
        raise ValueError('No public channel posts')
    return max(ids)


def _public_latest():
    request = urllib.request.Request('https://t.me/s/' + CHANNEL, headers={'User-Agent': 'AERA-News-Link/1.0'})
    with urllib.request.urlopen(request, timeout=8) as response:
        if response.status != 200:
            raise ValueError('Public channel unavailable')
        html = response.read(1024 * 1024).decode('utf-8', 'replace')
    return parse_public(html)


async def reconcile(sessions, bot):
    """Reconcile missed/bot-authored/deleted posts; HTTP happens only on the server."""
    async with sessions() as db:
        previous = _data(await db.get(AppSetting, LATEST_KEY))
    chat = await bot.get_chat(previous.get('chat_id') or '@' + CHANNEL)
    if chat.type != 'channel' or (chat.username or '').casefold() != CHANNEL:
        return False
    identity = await asyncio.to_thread(_public_latest)
    async with sessions.begin() as db:
        return await observe(db, chat.id, identity, source='public')


async def remember(db, source, chat_id, message_id, template, markup=None):
    """Store only confirmed sent message IDs; this never sends a message."""
    if type(chat_id) is not int or chat_id <= 0 or type(message_id) is not int or message_id <= 0:
        return False
    if not re.fullmatch(r'[a-zA-Z0-9:_-]{1,90}', source) or not ANCHOR.search(template):
        return False
    key = TRACK_PREFIX + source
    value = {'chat_id': chat_id, 'message_id': message_id, 'template': template,
             'markup': markup.model_dump(mode='json', exclude_none=True) if markup else None,
             'post_id': post_id(template), 'status': 'ACTIVE', 'retry_at': 0, 'attempts': 0}
    await _ensure(db, key, json.dumps(value))
    return True


async def edit_tracked(sessions, bot, *, limit=5, now=None):
    """Bounded, restart-safe edits. Unknown results can be retried idempotently."""
    now = time.time() if now is None else now
    async with sessions.begin() as db:
        pause = _data(await db.get(AppSetting, PAUSE_KEY)).get('until', 0)
        latest = _data(await db.get(AppSetting, LATEST_KEY)).get('post_id')
        if pause > now or type(latest) is not int or not 0 < latest < 2**31:
            return 0
        cursor = await _locked(db, CURSOR_KEY, '')
        query = select(AppSetting.key).where(AppSetting.key.startswith(TRACK_PREFIX, autoescape=True))
        if cursor.value:
            query = query.where(AppSetting.key > cursor.value)
        keys = list(await db.scalars(query.order_by(AppSetting.key).limit(limit)))
        cursor.value = keys[-1] if keys else ''
    completed = 0
    for key in keys:
        async with sessions.begin() as db:
            row = await _locked(db, key)
            value = _data(row)
            if value.get('status') == 'RETIRED' or value.get('post_id') == latest or value.get('retry_at', 0) > now:
                continue
            if value.get('status') == 'EDITING' and value.get('claimed_at', 0) > now - 90:
                continue
            template = value.get('template', '')
            if not ANCHOR.search(template) or type(value.get('message_id')) is not int or type(value.get('chat_id')) is not int:
                value['status'] = 'RETIRED';row.value = json.dumps(value);continue
            claim = uuid4().hex
            value.update(status='EDITING', claim=claim, claimed_at=now)
            row.value = json.dumps(value)
            body = with_link(template, latest)
            markup = InlineKeyboardMarkup.model_validate(value['markup']) if value.get('markup') else None
        outcome, delay, flood = 'DONE', 0, False
        try:
            await bot.edit_message_text(body, chat_id=value['chat_id'], message_id=value['message_id'], parse_mode='HTML', reply_markup=markup)
        except TelegramForbiddenError:
            outcome = 'RETIRED'
        except TelegramRetryAfter as error:
            outcome, delay, flood = 'RETRY', max(1, int(error.retry_after)), True
        except TelegramBadRequest as error:
            outcome = 'DONE' if 'message is not modified' in str(error).lower() else 'RETIRED'
        except Exception:
            outcome, delay = 'RETRY', 30
        async with sessions.begin() as db:
            row = await db.get(AppSetting, key)
            saved = _data(row)
            if not row or saved.get('claim') != claim:
                continue
            saved['status'] = 'RETIRED' if outcome == 'RETIRED' else 'ACTIVE'
            saved.pop('claim', None)
            if outcome == 'DONE':
                saved.update(post_id=latest, attempts=0, retry_at=0)
                completed += 1
            elif outcome == 'RETRY':
                saved['attempts'] = saved.get('attempts', 0) + 1
                saved['retry_at'] = now + max(delay, min(300, 2 ** min(saved['attempts'], 8)))
            row.value = json.dumps(saved)
        # A 429 pauses all news edits, without occupying the payment sender.
        if flood:
            async with sessions.begin() as db:
                pause_row = await _locked(db, PAUSE_KEY)
                pause_row.value = json.dumps({'until': now + delay})
            break
        await asyncio.sleep(.15)
    return completed
