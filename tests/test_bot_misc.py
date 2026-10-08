import json
import time
from types import SimpleNamespace

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetMe, SendDocument
from aiogram.types import BufferedInputFile, Message
from bot_harness import make_bot, message_payload
from sqlalchemy import select

from app.bot import installers, presentation, web_login
from app.bot.client import HttpxSession, create_bot
from app.bot.keyboards import menu, welcome_menu
from app.bot.texts import ru
from app.bot.texts.portal import text as tr
from app.db.models import AppSetting, User

METHOD = SimpleNamespace()


# ---------- guard middleware ----------


def guard_redis(driver):
    from app.bot.middleware import GuardMiddleware

    for middleware in driver.dp.message.outer_middleware._middlewares:
        if isinstance(middleware, GuardMiddleware):
            return middleware.redis


async def test_rate_limit(bot_driver):
    redis = guard_redis(bot_driver)
    await redis.set(f"bot-rate:1:{int(time.time()) // 60}", 45)
    await bot_driver.send(1, "/start")
    await bot_driver.click(1, "menu")
    assert bot_driver.api.texts() == [ru.RATE_LIMIT]
    (alert,) = bot_driver.api.named("answerCallbackQuery")
    assert alert.text == ru.RATE_LIMIT and alert.show_alert


async def test_maintenance_mode_spares_admins(bot_driver, app_sessions):
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="maintenance_mode", value="true"))
    await bot_driver.send(1, "/start")
    assert bot_driver.api.last_text() == ru.MAINTENANCE
    await bot_driver.click(1, "menu")
    assert bot_driver.api.named("answerCallbackQuery")[-1].text == ru.MAINTENANCE
    await bot_driver.send(999, "/admin")
    assert bot_driver.api.last_text().startswith(ru.ADMIN_TITLE)


async def test_expired_callback_is_ignored(bot_driver):
    bot_driver.api.fail["answerCallbackQuery"] = TelegramBadRequest(METHOD, "query is too old")
    await bot_driver.click(1, "menu")
    assert bot_driver.api.texts() == []


async def test_handler_errors_reply_generic_message(bot_driver, monkeypatch):
    bot_driver.api.fail["sendPhoto"] = RuntimeError("boom")
    await bot_driver.send(1, "/start")
    assert bot_driver.api.last_text() == ru.ERROR


# ---------- website login ----------


class FlowStore:
    """Mimics the Lua scripts' state machine for one login request."""

    def __init__(self, flow=None):
        self.flow = flow

    async def operation(self, script, request, *args):
        if not web_login.REQUEST.fullmatch(request) or self.flow is None:
            return None
        if script == web_login.CLAIM:
            if self.flow["status"] != "pending":
                return ""
            self.flow["pending_user"] = args[0]
            return self.flow["code"]
        if script == web_login.APPROVE:
            if self.flow["status"] != "pending" or self.flow.get("pending_user") != args[0]:
                return 0
            self.flow.update(telegram_id=args[0], status=args[1])
            return 1
        return json.dumps(self.flow)


REQUEST_ID = "A" * 43


@pytest.fixture
def flows(monkeypatch):
    store = FlowStore()
    monkeypatch.setattr(web_login, "operation", store.operation)
    return store


async def test_web_login_approves_pending_flow(bot_driver, flows, app_sessions):
    flows.flow = {"status": "pending", "code": "123"}
    await bot_driver.send(1, f"/start web_{REQUEST_ID}")
    assert bot_driver.api.last_text().startswith("✅ Вход на сайт AERA выполнен")
    assert flows.flow["status"] == "approved" and flows.flow["telegram_id"] == "1"
    async with app_sessions() as db:
        assert await db.scalar(select(User.telegram_id)) == 1


async def test_web_login_with_selected_plan_opens_checkout(bot_driver, flows, app_plan_id):
    flows.flow = {"status": "pending", "code": "1", "plan_id": app_plan_id}
    await bot_driver.send(1, f"/start web_{REQUEST_ID}")
    assert bot_driver.api.named("sendPhoto")  # payment screen


async def test_web_login_expired_and_blocked(bot_driver, flows, app_sessions):
    flows.flow = {"status": "approved", "code": "1"}
    await bot_driver.send(1, f"/start web_{REQUEST_ID}")
    assert bot_driver.api.last_text().startswith("Запрос входа истёк")
    async with app_sessions.begin() as db:
        db.add(User(telegram_id=2, is_blocked=True))
    flows.flow = {"status": "pending", "code": "1"}
    await bot_driver.send(2, f"/start web_{REQUEST_ID}")
    assert bot_driver.api.last_text() == "Профиль AERA недоступен. Обратитесь в поддержку."


async def test_web_login_store_failure(bot_driver, monkeypatch):
    async def broken(*_args):
        raise ConnectionError

    monkeypatch.setattr(web_login, "operation", broken)
    await bot_driver.send(1, f"/start web_{REQUEST_ID}")
    assert bot_driver.api.last_text().startswith("Вход временно недоступен")


async def test_web_login_confirm_buttons(bot_driver, flows):
    flows.flow = {"status": "pending", "code": "1", "pending_user": "1"}
    await bot_driver.click(1, f"webok:{REQUEST_ID}")
    assert bot_driver.api.named("answerCallbackQuery")[-1].text == "Вход подтверждён"
    assert bot_driver.api.last_text().startswith("✅ Вход подтверждён")
    await bot_driver.click(1, f"webno:{REQUEST_ID}")
    answer = bot_driver.api.named("answerCallbackQuery")[-1]
    assert answer.text == "Запрос уже завершён или истёк" and answer.show_alert


async def test_web_login_confirm_errors(bot_driver, monkeypatch):
    async def broken(*_args):
        raise ConnectionError

    monkeypatch.setattr(web_login, "operation", broken)
    await bot_driver.click(1, f"webok:{REQUEST_ID}")
    assert bot_driver.api.named("answerCallbackQuery")[-1].text == "Попробуйте снова позже."


async def test_web_login_operation_validates_request(monkeypatch):
    assert await web_login.operation(web_login.CLAIM, "short") is None
    calls = []

    class Cache:
        async def eval(self, *args):
            calls.append(args)
            return "code"

        async def aclose(self):
            calls.append("closed")

    monkeypatch.setenv("AERA_WEB_LOGIN_REDIS_URL", "redis://localhost/1")
    monkeypatch.setattr(web_login.Redis, "from_url", lambda *a, **k: Cache())
    assert await web_login.operation(web_login.CLAIM, REQUEST_ID, "1") == "code"
    assert calls[0][2] == web_login.key(REQUEST_ID) and calls[-1] == "closed"


# ---------- installers ----------


def bound_message(bot, chat_id=1):
    return Message.model_validate(message_payload(chat_id, "x"), context={"bot": bot})


@pytest.fixture
def installer(tmp_path, monkeypatch):
    path = tmp_path / "setup.exe"
    path.write_bytes(b"MZ installer")
    monkeypatch.setattr(installers, "WINDOWS_INSTALLER", path)
    return path


async def test_installer_uploads_once_then_reuses_file_id(app_sessions, installer):
    bot = make_bot()
    await installers.send_windows_installer(bound_message(bot))
    await installers.send_windows_installer(bound_message(bot))
    first, second = bot.session.named("sendDocument")
    assert isinstance(first.document, object) and not isinstance(first.document, str)
    assert second.document == "document-id"


async def test_installer_reuploads_when_file_id_is_stale(app_sessions, installer):
    bot = make_bot()
    await installers.send_windows_installer(bound_message(bot))
    bot.session.fail["sendDocument"] = TelegramBadRequest(METHOD, "wrong remote file id")
    with pytest.raises(TelegramBadRequest):
        await installers.send_windows_installer(bound_message(bot))
    async with app_sessions() as db:
        assert all(not record.value for record in await db.scalars(select(AppSetting)))


async def test_installer_other_errors_propagate(app_sessions, installer):
    bot = make_bot()
    await installers.send_windows_installer(bound_message(bot))
    bot.session.fail["sendDocument"] = TelegramBadRequest(METHOD, "chat not found")
    with pytest.raises(TelegramBadRequest):
        await installers.send_windows_installer(bound_message(bot))


async def test_installer_missing(app_sessions, tmp_path, monkeypatch):
    monkeypatch.setattr(installers, "WINDOWS_INSTALLER", tmp_path / "absent.exe")
    bot = make_bot()
    await installers.send_windows_installer(bound_message(bot), unavailable="нет файла")
    assert bot.session.last_text() == "нет файла"


# ---------- presentation ----------


async def test_send_screen_long_text_falls_back_to_message():
    bot = make_bot()
    await presentation.send_screen(bound_message(bot), "x" * 1100)
    assert bot.session.named("sendMessage") and not bot.session.named("sendPhoto")


async def test_send_screen_caches_photo_file_id():
    bot = make_bot()
    await presentation.send_screen(bound_message(bot), "hi", art="plans")
    await presentation.send_screen(bound_message(bot), "hi", art="plans")
    first, second = bot.session.named("sendPhoto")
    assert second.photo == "photo-id" and first.photo != "photo-id"


async def test_edit_screen_variants():
    bot = make_bot()
    payload = message_payload(1, None)
    payload["photo"] = [{"file_id": "p", "file_unique_id": "u", "width": 1, "height": 1}]
    photo = Message.model_validate(payload, context={"bot": bot})
    text = bound_message(bot)
    await presentation.edit_screen(photo, "x" * 1100)
    await presentation.edit_screen(photo, "caption")
    await presentation.edit_screen(text, "text")
    await presentation.edit_screen(text, "media", art="profile")
    names = [name for name, _ in bot.session.calls]
    assert names == ["sendMessage", "editMessageCaption", "editMessageText", "editMessageMedia"]


async def test_edit_screen_ignores_not_modified_only():
    bot = make_bot()
    bot.session.fail["editMessageText"] = TelegramBadRequest(METHOD, "message is not modified")
    await presentation.edit_screen(bound_message(bot), "same")
    bot.session.fail["editMessageText"] = TelegramBadRequest(METHOD, "message to edit not found")
    with pytest.raises(TelegramBadRequest):
        await presentation.edit_screen(bound_message(bot), "gone")


@pytest.mark.parametrize(
    "count, unlimited, label",
    [
        (1, False, "1 устройство"),
        (3, False, "3 устройства"),
        (5, False, "5 устройств"),
        (12, False, "12 устройств"),
        (22, False, "22 устройства"),
        (1, True, "Безлимит устройств"),
    ],
)
def test_devices_label(count, unlimited, label):
    assert presentation.devices_label(count, unlimited) == label


def test_labels_and_captions():
    plan = SimpleNamespace(
        name="X <b>",
        slug="plus-1m",
        price_minor=150,
        currency="XTR",
        duration_months=None,
        duration_days=14,
        device_limit=2,
        unlimited_devices=False,
        traffic_limit_bytes=2 * 1024**3,
    )
    assert presentation.price_label(plan) == "150 ⭐"
    assert presentation.period_label(plan) == "14 дней"
    assert presentation.traffic_label(plan.traffic_limit_bytes) == "Трафик: 2 ГБ"
    assert "X &lt;b&gt;" in presentation.plan_caption(plan)
    assert "PLUS" in presentation.catalog_caption([plan])
    assert "14 дней" in presentation.family_caption("plus", [plan])
    sub = SimpleNamespace(
        traffic_limit_bytes=1024**3,
        expires_at=__import__("datetime").datetime(2030, 1, 2),
        device_limit=1,
        unlimited_devices=False,
    )
    client = SimpleNamespace(traffic_used_bytes=512 * 1024**2)
    caption = presentation.subscription_caption(plan, sub, client, "Активна")
    assert "использовано 0.5 ГБ" in caption and "До 02.01.2030" in caption


# ---------- keyboards ----------


def test_keyboards():
    welcome = {b.callback_data for row in welcome_menu().inline_keyboard for b in row}
    assert welcome == {"plans", "connect", "subscription", "how", "support"}
    assert welcome_menu().inline_keyboard[0][0].style == "primary"
    assert {"devices", "referral"} <= {
        b.callback_data for row in menu().inline_keyboard for b in row
    }


# ---------- httpx transport ----------


def test_create_bot_picks_transport(app_settings):
    settings = app_settings(bot_token="42:TEST", telegram_http_client="httpx")
    assert isinstance(create_bot(settings).session, HttpxSession)
    settings = app_settings(telegram_http_client="aiohttp")
    assert not isinstance(create_bot(settings).session, HttpxSession)


async def test_httpx_session_posts_and_parses():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200, json={"ok": True, "result": {"id": 42, "is_bot": True, "first_name": "AERA"}}
        )

    session = HttpxSession(httpx.MockTransport(handler))
    bot = make_bot()
    me = await session.make_request(bot, GetMe())
    assert me.id == 42 and seen[0].url.path == "/bot42:TEST/getMe"
    await session.close()


async def test_httpx_session_uploads_files_and_maps_errors():
    uploads = []

    def handler(request):
        uploads.append(request.headers["content-type"])
        result = message_payload(1, None, document={"file_id": "d", "file_unique_id": "u"})
        return httpx.Response(200, json={"ok": True, "result": result})

    session = HttpxSession(httpx.MockTransport(handler))
    method = SendDocument(chat_id=1, document=BufferedInputFile(b"data", filename="a.txt"))
    assert (await session.make_request(make_bot(), method)).document.file_id == "d"
    assert uploads[0].startswith("multipart/form-data")

    def broken(request):
        raise httpx.ConnectError("down")

    failing = HttpxSession(httpx.MockTransport(broken))
    with pytest.raises(TelegramNetworkError):
        await failing.make_request(make_bot(), GetMe())


async def test_httpx_session_streams_content():
    session = HttpxSession(httpx.MockTransport(lambda r: httpx.Response(200, content=b"abc")))
    chunks = [chunk async for chunk in session.stream_content("https://files/x")]
    assert b"".join(chunks) == b"abc"


# ---------- runtime entry point ----------


async def test_runtime_main_requires_token(app_settings):
    from app.bot import runtime

    app_settings(bot_token="")
    with pytest.raises(RuntimeError):
        await runtime.main()


async def test_runtime_main_polls_and_closes(app_settings, monkeypatch):
    from app.bot import runtime

    app_settings(bot_token="42:TEST")
    events = []

    class Dispatcher:
        async def start_polling(self, bot):
            events.append("polling")

    bot = make_bot()

    async def close():
        events.append("closed")

    monkeypatch.setattr(bot.session, "close", close)
    monkeypatch.setattr(runtime, "create_bot", lambda settings: bot)
    monkeypatch.setattr(runtime, "create_dispatcher", Dispatcher)
    await runtime.main()
    assert events == ["polling", "closed"]


def test_portal_texts_have_all_languages():
    from app.bot.texts.portal import COPY, LANGUAGES

    for key, values in COPY.items():
        assert len(values) in (1, len(LANGUAGES)), key
    assert tr("welcome", "zz") == tr("welcome", "ru")


def test_welcome_copy_has_no_debug_marker():
    from app.bot.texts.portal import COPY

    assert all("plat chek" not in value for value in COPY["welcome"])
