"""Drive the real aiogram dispatcher against a recording Bot API stand-in."""

import itertools

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Update

BOT_USER = {"id": 42, "is_bot": True, "first_name": "AERA"}
_ids = itertools.count(1)


def user_payload(user_id, **values):
    return {"id": user_id, "is_bot": False, "first_name": f"User{user_id}", **values}


def message_payload(chat_id, text=None, **values):
    payload = {
        "message_id": next(_ids),
        "date": 0,
        "chat": {"id": chat_id, "type": "private"},
        "from": user_payload(chat_id),
    }
    if text is not None:
        payload["text"] = text
    payload.update(values)
    return payload


class RecordingSession(BaseSession):
    """Records every Bot API call and returns plausible results."""

    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail = {}

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        name = method.__api_method__
        self.calls.append((name, method))
        if name in self.fail:
            raise self.fail[name]
        if name == "createInvoiceLink":
            return "https://t.me/$invoice-" + method.payload
        returning = getattr(method, "__returning__", None)
        if returning is Message or name in {"sendMessage", "sendPhoto", "sendDocument"}:
            chat_id = getattr(method, "chat_id", None) or 1
            extra = {}
            if name == "sendPhoto":
                extra["photo"] = [
                    {"file_id": "photo-id", "file_unique_id": "p", "width": 1, "height": 1}
                ]
            if name == "sendDocument":
                extra["document"] = {"file_id": "document-id", "file_unique_id": "d"}
            payload = message_payload(chat_id, "ok", **extra)
            payload["from"] = BOT_USER
            return Message.model_validate(payload, context={"bot": bot})
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    # ---------- inspection helpers ----------

    def named(self, *names):
        return [method for name, method in self.calls if name in names]

    def texts(self):
        out = []
        for name, method in self.calls:
            if name in {"sendMessage", "editMessageText"}:
                out.append(method.text)
            elif name in {"sendPhoto", "sendDocument", "editMessageCaption"}:
                out.append(method.caption)
            elif name == "editMessageMedia":
                out.append(method.media.caption)
        return out

    def last_text(self):
        texts = self.texts()
        return texts[-1] if texts else None

    def last_markup(self):
        for name, method in reversed(self.calls):
            markup = getattr(method, "reply_markup", None)
            if markup is not None:
                return markup
        return None

    def buttons(self):
        markup = self.last_markup()
        if not markup:
            return {}
        return {
            button.text: button.callback_data
            or button.url
            or (button.web_app and button.web_app.url)
            for row in markup.inline_keyboard
            for button in row
        }

    def clear(self):
        self.calls.clear()


def make_bot():
    return Bot("42:TEST", session=RecordingSession())


class Driver:
    def __init__(self, dispatcher, bot):
        self.dp, self.bot = dispatcher, bot
        self.api = bot.session

    async def feed(self, payload):
        update = Update.model_validate(
            {"update_id": next(_ids), **payload}, context={"bot": self.bot}
        )
        return await self.dp.feed_update(self.bot, update)

    async def send(self, user_id, text, **user_values):
        message = message_payload(user_id, text)
        message["from"] = user_payload(user_id, **user_values)
        return await self.feed({"message": message})

    async def click(self, user_id, data, photo=False, **user_values):
        message = message_payload(user_id, None if photo else "screen")
        message["from"] = BOT_USER
        if photo:
            message["photo"] = [
                {"file_id": "old-photo", "file_unique_id": "o", "width": 1, "height": 1}
            ]
            message["caption"] = "screen"
        return await self.feed(
            {
                "callback_query": {
                    "id": str(next(_ids)),
                    "from": user_payload(user_id, **user_values),
                    "chat_instance": "ci",
                    "data": data,
                    "message": message,
                }
            }
        )
