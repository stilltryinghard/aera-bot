import json
import logging
from datetime import UTC, datetime, timedelta

from conftest import add_user
from sqlalchemy import select

from app.core.logging import log_error
from app.db.models import (
    AppSetting,
    Notification,
    Payment,
    Plan,
    ProvisioningJob,
    Referral,
    Server,
    Subscription,
    User,
    VPNClient,
)
from app.integrations.xui.mock import MockXUIClient
from app.integrations.xui.schemas import Traffic
from app.services.catalog import FAMILIES, catalog, family_of
from app.services.commerce import utc
from app.services.provisioning import ProvisioningService
from app.services.referrals import register_referral, reward_referrals
from app.services.seed import seed
from app.services.settings import set_setting, setting


async def subscription(
    db, plan_id, telegram_id=1, days=30, status="PENDING_PROVISIONING", **user_values
):
    user = User(telegram_id=telegram_id, **user_values)
    db.add(user)
    await db.flush()
    sub = Subscription(
        user_id=user.id,
        plan_id=plan_id,
        status=status,
        expires_at=datetime.now(UTC) + timedelta(days=days),
        traffic_limit_bytes=0,
        device_limit=1,
    )
    db.add(sub)
    await db.flush()
    return user, sub


async def server(db):
    record = Server(name="s", code="s", capacity=10)
    db.add(record)
    await db.flush()
    return record


async def run_job(sessions, vault, adapter, payment=True, **sub_values):
    async with sessions.begin() as db:
        plan_id = (await db.scalar(select(Plan))).id
        await server(db)
        user, sub = await subscription(db, plan_id, **sub_values)
        payment_id = None
        if payment:
            pay = Payment(
                user_id=user.id, plan_id=plan_id, amount_minor=1, currency="RUB", status="PAID"
            )
            db.add(pay)
            await db.flush()
            payment_id = pay.id
        job = ProvisioningJob(subscription_id=sub.id, payment_id=payment_id)
        db.add(job)
        await db.flush()
        job_id, sub_id = job.id, sub.id
    service = ProvisioningService(sessions, adapter, vault)
    return service, await service.run(job_id), job_id, sub_id


async def test_run_creates_enabled_client_and_notifies(sessions, vault, plan_id):
    adapter = MockXUIClient()
    service, ok, job_id, sub_id = await run_job(sessions, vault, adapter)
    assert ok
    async with sessions() as db:
        client = await db.scalar(select(VPNClient))
        assert client.enabled and client.email == f"aera-{client.uuid}"
        assert vault.reveal(client.token_encrypted)
        assert (await db.get(Subscription, sub_id)).status == "ACTIVE"
        assert (await db.get(ProvisioningJob, job_id)).status == "DONE"
        note = await db.scalar(select(Notification))
        assert note.dedupe_key == f"ready:{job_id}"
    assert adapter.clients[client.uuid].enabled
    assert await service.run(job_id) is True  # already done
    assert await service.run("missing") is True


async def test_run_retries_with_backoff_and_keeps_identity(sessions, vault, plan_id):
    adapter = MockXUIClient()
    adapter.available = False
    service, ok, job_id, _ = await run_job(sessions, vault, adapter)
    assert ok is False
    async with sessions() as db:
        job = await db.get(ProvisioningJob, job_id)
        uuid = (await db.scalar(select(VPNClient))).uuid
    assert (job.status, job.attempts) == ("RETRY", 1)
    assert utc(job.next_attempt_at) > datetime.now(UTC)
    adapter.available = True
    assert await service.run(job_id)
    async with sessions() as db:
        assert (await db.scalar(select(VPNClient))).uuid == uuid
        assert len(list(await db.scalars(select(VPNClient)))) == 1


async def test_run_suspends_blocked_user(sessions, vault, plan_id):
    _, ok, _, sub_id = await run_job(sessions, vault, MockXUIClient(), is_blocked=True)
    async with sessions() as db:
        assert (await db.get(Subscription, sub_id)).status == "SUSPENDED"
        assert not (await db.scalar(select(VPNClient))).enabled


async def test_run_marks_trial_without_paid_history(sessions, vault, plan_id):
    _, _, _, sub_id = await run_job(
        sessions, vault, MockXUIClient(), payment=False, trial_used=True
    )
    async with sessions() as db:
        assert (await db.get(Subscription, sub_id)).status == "TRIAL"
        assert await db.scalar(select(Notification)) is None


async def test_tick_runs_due_jobs_and_syncs_state(sessions, vault, plan_id):
    adapter = MockXUIClient()
    service, _, _, _ = await run_job(sessions, vault, adapter)
    async with sessions.begin() as db:
        client = await db.scalar(select(VPNClient))
        client.traffic_limit_bytes = 100
        email = client.email
    adapter.traffic[email] = Traffic(60, 50)
    await service.tick()
    async with sessions() as db:
        client = await db.scalar(select(VPNClient))
        assert client.traffic_used_bytes == 110 and client.enabled is False


async def test_tick_expires_and_flags_expiring(sessions, vault, plan_id):
    adapter = MockXUIClient()
    service, _, _, sub_id = await run_job(sessions, vault, adapter)
    async with sessions.begin() as db:
        sub = await db.get(Subscription, sub_id)
        sub.expires_at = datetime.now(UTC) + timedelta(days=2)
    await service.tick()
    async with sessions.begin() as db:
        sub = await db.get(Subscription, sub_id)
        assert sub.status == "EXPIRING"
        sub.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await service.tick()
    async with sessions() as db:
        assert (await db.get(Subscription, sub_id)).status == "EXPIRED"
        assert not (await db.scalar(select(VPNClient))).enabled


async def test_tick_disables_blocked_and_tolerates_panel_errors(sessions, vault, plan_id):
    adapter = MockXUIClient()
    service, _, _, sub_id = await run_job(sessions, vault, adapter)
    async with sessions.begin() as db:
        user = await db.get(User, (await db.get(Subscription, sub_id)).user_id)
        user.is_blocked = True
    adapter.available = False
    await service.tick()
    async with sessions() as db:
        assert not (await db.scalar(select(VPNClient))).enabled


async def test_tick_reschedules_crashing_job(sessions, vault, plan_id, monkeypatch):
    adapter = MockXUIClient()
    service, _, job_id, _ = await run_job(sessions, vault, adapter)
    async with sessions.begin() as db:
        job = await db.get(ProvisioningJob, job_id)
        job.status = "RETRY"

    async def crash(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "run", crash)
    await service.tick()
    async with sessions() as db:
        job = await db.get(ProvisioningJob, job_id)
        assert job.status == "RETRY" and job.attempts == 1


# ---------- referrals ----------


async def test_register_referral_rules(sessions):
    async with sessions.begin() as db:
        referrer = await add_user(db, 1)
        newcomer = await add_user(db, 2)
        veteran = await add_user(db, 3)
        veteran.created_at = datetime.now(UTC) - timedelta(minutes=5)
        await set_setting(db, "referral_reward_days", "10")
        code = referrer.referral_code
        assert not await register_referral(db, newcomer, "unknown")
        assert not await register_referral(db, referrer, code)
        assert not await register_referral(db, veteran, code)
        assert await register_referral(db, newcomer, code)
        assert not await register_referral(db, newcomer, code)  # only once
        referral = await db.scalar(select(Referral))
        assert referral.reward_value == 10 and newcomer.referred_by_id == referrer.id


async def test_reward_referrals_extends_referrer(sessions, plan_id):
    async with sessions.begin() as db:
        referrer, sub = await subscription(db, plan_id, telegram_id=1, days=5, status="ACTIVE")
        _, suspended = await subscription(db, plan_id, telegram_id=2, status="SUSPENDED")
        friend = await add_user(db, 3)
        other = await add_user(db, 4)
        lonely = await add_user(db, 5)
        db.add_all(
            [
                Referral(
                    referrer_user_id=referrer.id,
                    referred_user_id=friend.id,
                    code="c",
                    status="ELIGIBLE",
                    reward_value=7,
                ),
                Referral(
                    referrer_user_id=suspended.user_id,
                    referred_user_id=other.id,
                    code="c",
                    status="ELIGIBLE",
                ),
                Referral(
                    referrer_user_id=lonely.id,
                    referred_user_id=lonely.id,
                    code="c",
                    status="ELIGIBLE",
                ),
            ]
        )
        before = utc(sub.expires_at)
        sub_id = sub.id
    async with sessions.begin() as db:
        await reward_referrals(db)
    async with sessions() as db:
        sub = await db.get(Subscription, sub_id)
        assert utc(sub.expires_at) - before == timedelta(days=7)
        assert sub.status == "PENDING_PROVISIONING"
        statuses = sorted(r.status for r in await db.scalars(select(Referral)))
        assert statuses == ["ELIGIBLE", "ELIGIBLE", "REWARDED"]


# ---------- settings / catalog / seed / logging ----------


async def test_settings_round_trip(sessions):
    async with sessions.begin() as db:
        assert await setting(db, "k", "default") == "default"
        await set_setting(db, "k", "1")
    async with sessions.begin() as db:
        await set_setting(db, "k", "2")
    async with sessions() as db:
        assert (await db.get(AppSetting, "k")).value == "2"


def test_catalog_contents():
    plans = list(catalog())
    assert len(plans) == len(FAMILIES) * 2
    assert all(p["stars_price"] is None for p in plans)
    business_6m = next(p for p in plans if p["slug"] == "business-6m")
    assert business_6m["is_active"] is False  # above the 10 000 RUB cap
    assert [p for p in plans if p["is_featured"]][0]["slug"].startswith("plus")
    assert next(catalog(mock=True))["stars_price"] == 100


def test_family_of():
    class P:
        def __init__(self, slug):
            self.slug = slug

    assert family_of(P("plus-6m")) == "plus"
    assert family_of(P("custom-1m")) is None


async def test_seed_is_idempotent(sessions):
    async with sessions.begin() as db:
        await seed(db, mock=True)
    async with sessions.begin() as db:
        await seed(db, mock=True)
    async with sessions() as db:
        assert len(list(await db.scalars(select(Plan)))) == len(FAMILIES) * 2
        assert len(list(await db.scalars(select(Server)))) == 1


def test_log_error_excludes_message(caplog):
    try:
        raise ValueError("secret-token")
    except ValueError as error:
        with caplog.at_level(logging.ERROR, logger="aera"):
            request_id = log_error("payment", error, user="u1")
    record = json.loads(caplog.records[-1].message)
    assert record["request_id"] == request_id and record["error_type"] == "ValueError"
    assert record["user"] == "u1" and record["stack"]
    assert "secret-token" not in caplog.text
