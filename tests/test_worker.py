import pytest
from bot_harness import make_bot

from app.services import (
    checkout,
    manual_bank,
    notifications,
    paid_pool,
    provisioning,
    trial_pool,
)
from app.workers import run


class StopLoop(BaseException):
    """Escapes the worker's `except Exception` so a test can end the loop."""


@pytest.fixture
def worker(monkeypatch, app_sessions, app_settings):
    calls = []

    def record(name, error=None):
        async def fake(*_args, **kwargs):
            calls.append((name, kwargs.get("allowed_types")))
            if error:
                raise error

        return fake

    async def stop(_seconds):
        raise StopLoop

    class Engine:
        async def dispose(self):
            calls.append(("dispose", None))

    monkeypatch.setattr(run.asyncio, "sleep", stop)
    monkeypatch.setattr(run, "engine", Engine())
    monkeypatch.setattr(trial_pool, "synchronize", record("synchronize", RuntimeError("panel")))
    monkeypatch.setattr(manual_bank, "poll", record("bank_poll"))
    monkeypatch.setattr(paid_pool, "retry_fulfillment", record("retry_fulfillment"))
    monkeypatch.setattr(checkout.CheckoutService, "poll", record("checkout_poll"))
    monkeypatch.setattr(provisioning.ProvisioningService, "tick", record("tick"))
    monkeypatch.setattr(notifications, "schedule_expiry", record("schedule_expiry"))
    monkeypatch.setattr(notifications, "send_notifications", record("send_notifications"))
    monkeypatch.setattr(notifications, "send_broadcasts", record("send_broadcasts"))
    bot = make_bot()
    monkeypatch.setattr("app.bot.client.create_bot", lambda settings: bot)
    return calls, app_settings


async def test_manual_mode_tick(worker):
    calls, app_settings = worker
    app_settings(bot_token="42:TEST")
    with pytest.raises(StopLoop):
        await run.main()
    names = [name for name, _ in calls]
    # A failing trial observer must not stop bank polling or deliveries.
    assert names == [
        "synchronize",
        "bank_poll",
        "retry_fulfillment",
        "send_notifications",
        "send_broadcasts",
        "dispose",
    ]
    allowed = dict(calls)["send_notifications"]
    assert "ACCESS_READY" in allowed and "READY" not in allowed


async def test_automatic_mode_tick_without_bot(worker):
    calls, app_settings = worker
    app_settings(manual_sales=False, bot_token="")
    with pytest.raises(StopLoop):
        await run.main()
    assert [name for name, _ in calls] == ["checkout_poll", "tick", "schedule_expiry", "dispose"]


async def test_tick_errors_are_contained(worker, monkeypatch):
    calls, app_settings = worker
    app_settings(manual_sales=False, bot_token="")

    async def broken(*_args):
        raise RuntimeError("db down")

    monkeypatch.setattr(checkout.CheckoutService, "poll", broken)
    with pytest.raises(StopLoop):
        await run.main()
    assert [name for name, _ in calls] == ["dispose"]
