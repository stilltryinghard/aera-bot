"""Network calls must not run inside transactions that hold row locks.

Each test changes the rows a service is working on from a second session *while* the
service's panel or Telegram call is in flight. If the service still held its row locks,
that write would block until the call returned, so it is bounded by a short timeout.
Row locks are only real on PostgreSQL (set TEST_DATABASE_URL); on SQLite these tests
still check that concurrent changes are not overwritten with stale state.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from aiogram.exceptions import TelegramRetryAfter
from conftest import add_user
from sqlalchemy import select, update

from app.db.models import (
    Broadcast,
    BroadcastDelivery,
    Payment,
    ProvisioningJob,
    Server,
    Subscription,
    VPNClient,
)
from app.integrations.xui.mock import MockXUIClient
from app.integrations.xui.schemas import Traffic
from app.services import notifications
from app.services.commerce import utc
from app.services.notifications import send_broadcasts
from app.services.provisioning import ProvisioningService

UNBLOCKED = 2  # seconds a concurrent write may wait before we call it blocked


async def write_concurrently(sessions, statement):
    async def write():
        async with sessions.begin() as db:
            await db.execute(statement)

    await asyncio.wait_for(write(), UNBLOCKED)


class Panel(MockXUIClient):
    """Runs a hook during the first panel call."""

    def __init__(self, during_first_call):
        super().__init__()
        self.during_first_call = during_first_call
        self.calls = []

    async def ensure_client(self, spec):
        self.calls.append(spec)
        if len(self.calls) == 1:
            await self.during_first_call()
        await super().ensure_client(spec)


async def paid_job(sessions, plan_id, days=30):
    async with sessions.begin() as db:
        db.add(Server(name="s", code="s"))
        user = await add_user(db, 1)
        sub = Subscription(
            user_id=user.id,
            plan_id=plan_id,
            expires_at=datetime.now(UTC) + timedelta(days=days),
            traffic_limit_bytes=0,
            device_limit=1,
        )
        db.add(sub)
        await db.flush()
        payment = Payment(
            user_id=user.id, plan_id=plan_id, amount_minor=1, currency="RUB", status="PAID"
        )
        db.add(payment)
        await db.flush()
        job = ProvisioningJob(subscription_id=sub.id, payment_id=payment.id)
        db.add(job)
        await db.flush()
        return job.id, sub.id


async def test_run_panel_call_holds_no_locks_and_repushes_changes(sessions, vault, plan_id):
    job_id, sub_id = await paid_job(sessions, plan_id)
    extended = datetime.now(UTC) + timedelta(days=60)

    async def extend():
        # e.g. a second payment confirmed while the first push is in flight
        await write_concurrently(
            sessions,
            update(Subscription).where(Subscription.id == sub_id).values(expires_at=extended),
        )

    panel = Panel(extend)
    assert await ProvisioningService(sessions, panel, vault).run(job_id)
    first, second = panel.calls
    assert utc(second.expires_at) == extended != utc(first.expires_at)
    async with sessions() as db:
        client = await db.scalar(select(VPNClient))
        assert utc(client.expires_at) == extended
        assert (await db.get(ProvisioningJob, job_id)).status == "DONE"


async def test_run_failure_is_recorded_without_holding_locks(sessions, vault, plan_id):
    job_id, sub_id = await paid_job(sessions, plan_id)

    class Down(MockXUIClient):
        async def ensure_client(self, spec):
            await write_concurrently(
                sessions,
                update(Subscription).where(Subscription.id == sub_id).values(device_limit=2),
            )
            raise ConnectionError

    assert await ProvisioningService(sessions, Down(), vault).run(job_id) is False
    async with sessions() as db:
        job = await db.get(ProvisioningJob, job_id)
        assert (job.status, job.attempts) == ("RETRY", 1)


async def test_run_gives_up_when_state_keeps_changing(sessions, vault, plan_id):
    job_id, sub_id = await paid_job(sessions, plan_id)

    class Moving(MockXUIClient):
        calls = 0

        async def ensure_client(self, spec):
            Moving.calls += 1
            await write_concurrently(
                sessions,
                update(Subscription)
                .where(Subscription.id == sub_id)
                .values(expires_at=datetime.now(UTC) + timedelta(days=30 + Moving.calls)),
            )

    assert await ProvisioningService(sessions, Moving(), vault).run(job_id) is False
    async with sessions() as db:
        assert (await db.get(ProvisioningJob, job_id)).status != "DONE"
    assert Moving.calls == 3


async def test_tick_panel_calls_hold_no_locks_and_skip_stale_results(sessions, vault, plan_id):
    job_id, sub_id = await paid_job(sessions, plan_id)
    service = ProvisioningService(sessions, MockXUIClient(), vault)
    assert await service.run(job_id)
    async with sessions.begin() as db:
        client = await db.scalar(select(VPNClient))
        client.traffic_limit_bytes = 100
        client_id, email = client.id, client.email

    async def change_client():
        await write_concurrently(
            sessions,
            update(VPNClient).where(VPNClient.id == client_id).values(traffic_limit_bytes=10**9),
        )

    panel = Panel(change_client)
    panel.traffic[email] = Traffic(80, 80)
    service.adapter = panel
    await service.tick()
    async with sessions() as db:
        client = await db.get(VPNClient, client_id)
    # The measurement belonged to the old limit, so it is not applied to the new one.
    assert client.enabled and client.traffic_used_bytes == 0
    await service.tick()
    async with sessions() as db:
        assert (await db.get(VPNClient, client_id)).traffic_used_bytes == 160


async def broadcast_to(sessions, count):
    async with sessions.begin() as db:
        users = [await add_user(db, i) for i in range(1, count + 1)]
        broadcast = Broadcast(admin_user_id=9, text="news", total=count)
        db.add(broadcast)
        await db.flush()
        for user in users:
            db.add(BroadcastDelivery(broadcast_id=broadcast.id, user_id=user.id))
        return broadcast.id


class Bot:
    def __init__(self, during_send=None, error=None):
        self.during_send, self.error, self.sent = during_send, error, []

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.during_send:
            await self.during_send()
        if self.error:
            raise self.error
        self.sent.append(chat_id)


async def no_sleep(_seconds):
    return None


async def test_broadcast_send_holds_no_locks(sessions, monkeypatch):
    monkeypatch.setattr(notifications.asyncio, "sleep", no_sleep)
    broadcast_id = await broadcast_to(sessions, 2)

    async def touch_broadcast():
        await write_concurrently(
            sessions,
            update(Broadcast).where(Broadcast.id == broadcast_id).values(button_text=None),
        )

    await send_broadcasts(sessions, Bot(touch_broadcast))
    async with sessions() as db:
        broadcast = await db.get(Broadcast, broadcast_id)
        assert (broadcast.sent, broadcast.status) == (2, "DONE")


async def test_broadcast_retry_after_releases_all_claims(sessions, monkeypatch):
    monkeypatch.setattr(notifications.asyncio, "sleep", no_sleep)
    await broadcast_to(sessions, 3)
    await send_broadcasts(sessions, Bot(error=TelegramRetryAfter(object(), "slow", 5)))
    async with sessions() as db:
        statuses = {d.status for d in await db.scalars(select(BroadcastDelivery))}
    assert statuses == {"PENDING"}


async def test_broadcast_reclaims_interrupted_sends(sessions, monkeypatch):
    monkeypatch.setattr(notifications.asyncio, "sleep", no_sleep)
    broadcast_id = await broadcast_to(sessions, 1)
    async with sessions.begin() as db:
        delivery = await db.scalar(select(BroadcastDelivery))
        delivery.status = "SENDING"
    async with sessions.begin() as db:
        delivery = await db.scalar(select(BroadcastDelivery))
        delivery.updated_at = datetime.now(UTC) - timedelta(minutes=5)
    bot = Bot()
    await send_broadcasts(sessions, bot)
    assert bot.sent == [1]
    async with sessions() as db:
        assert (await db.get(Broadcast, broadcast_id)).status == "DONE"
