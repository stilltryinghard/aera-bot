import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from conftest import add_user, make_settings
from sqlalchemy import select

from app.db.models import AppSetting, Notification, PaidLink, TrialLink, TrialUsage, User
from app.integrations.xui.exceptions import XUIError
from app.integrations.xui.trial_keys import TrialStarted, replacement_email
from app.services import trial_activation, trial_pool, trial_recycling
from app.services.commerce import utc
from app.services.trial_pool import (
    DURATION_MS,
    apply_snapshot,
    claim_link,
    confirm_activation,
    fingerprint,
    import_links,
    parse_link,
)
from app.services.trial_recycling import recycle_trials, replace_link

SECRET = "development-only-change-before-production"


def now_ms(offset=timedelta()):
    return int((datetime.now(UTC) + offset).timestamp() * 1000)


def link(identity=None, label="Trial"):
    return f"vless://{identity or uuid4()}@vpn.example:443?security=reality#{label}"


async def free_link(db, vault, status="FREE", **values):
    identity = str(uuid4())
    record = TrialLink(
        label="t",
        client_uuid=identity,
        source_uuid=identity,
        panel_email=f"aera-trial-{identity}",
        link_encrypted=vault.cipher.encrypt(link(identity).encode()).decode(),
        status=status,
        checked_at=datetime.now(UTC),
        **values,
    )
    db.add(record)
    await db.flush()
    return record


# ---------- links and claiming ----------


def test_parse_link():
    identity = str(uuid4())
    assert parse_link(f" {link(identity, 'My%20Key')} ") == (identity, "My Key")
    assert parse_link(f"vless://{identity}@h:1")[1] == "AERA Trial"
    for bad in ("vmess://x@h", f"vless://{identity}:pw@h:1", "vless://not-a-uuid@h:1"):
        with pytest.raises(ValueError):
            parse_link(bad)


def test_fingerprint_is_keyed():
    assert fingerprint(1, "a") != fingerprint(1, "b")
    assert fingerprint(1, "a") == fingerprint(1, "a")


async def test_import_links_skips_known_identities(sessions, vault):
    identity = str(uuid4())
    async with sessions.begin() as db:
        await import_links(db, [link(identity)], vault)
        await db.flush()
        await import_links(db, [link(identity)], vault)
        await db.flush()
        records = list(await db.scalars(select(TrialLink)))
    assert len(records) == 1 and records[0].status == "UNVERIFIED"


async def test_claim_link_once_per_telegram_account(sessions, vault):
    async with sessions.begin() as db:
        stale = await free_link(db, vault)
        stale.checked_at = datetime.now(UTC) - timedelta(minutes=5)
        fresh = await free_link(db, vault)
        user = await add_user(db, 1)
        record, outcome = await claim_link(db, user, SECRET)
        assert (record.id, outcome) == (fresh.id, "new")
        assert record.status == "WAITING" and user.trial_used
        assert await claim_link(db, user, SECRET) == (record, "existing")
        record.pending_uuid = str(uuid4())
        assert (await claim_link(db, user, SECRET))[1] == "used"
        # The same Telegram account re-registered as a new user is still remembered.
        record.user_id = None
        await db.flush()
        await db.delete(user)
        await db.flush()
        again = await add_user(db, 1)
        assert await claim_link(db, again, SECRET) == (None, "used")
        newcomer = await add_user(db, 2)
        assert await claim_link(db, newcomer, SECRET) == (None, "empty")


# ---------- activation ----------


async def test_confirm_activation_notifies_admin_once(sessions, vault):
    async with sessions.begin() as db:
        await add_user(db, 999)
        user = await add_user(db, 1)
        record = await free_link(db, vault, user_id=user.id, status="ACTIVATING")
        record.started_at = datetime.now(UTC)
        record.activation_deadline_ms = now_ms(timedelta(days=2))
        db.add(TrialUsage(fingerprint=fingerprint(1, SECRET)))
        await db.flush()
        settings = make_settings(admin_telegram_ids="999")
        await confirm_activation(db, record, settings)
        await confirm_activation(db, record, settings)
        assert record.status == "ACTIVE"
        notes = list(await db.scalars(select(Notification)))
        assert len(notes) == 1 and notes[0].notification_type == "TRIAL_ACTIVE"
        record.activation_deadline_ms = now_ms(-timedelta(minutes=1))
        await confirm_activation(db, record, settings)
        assert record.status == "EXPIRED"
        record.user_id = None
        await confirm_activation(db, record, settings)  # unknown user: no-op


def client(record, **values):
    entry = {
        "id": record.client_uuid,
        "email": record.panel_email,
        "enable": True,
        "limitIp": 1,
        "totalGB": 0,
        "reset": 0,
        "expiryTime": -DURATION_MS,
    }
    entry.update(values)
    return entry


def snapshot(clients, stats=()):
    return {"settings": json.dumps({"clients": clients}), "clientStats": list(stats)}


async def test_apply_snapshot_verifies_unassigned_stock(sessions, vault):
    async with sessions.begin() as db:
        ready = await free_link(db, vault, status="UNVERIFIED")
        bad_limits = await free_link(db, vault, status="UNVERIFIED")
        used = await free_link(db, vault, status="UNVERIFIED")
        timed = await free_link(db, vault, status="UNVERIFIED", ready_at=datetime.now(UTC))
        gone = await free_link(db, vault, status="UNVERIFIED")
        retired = await free_link(db, vault, issued_at=datetime.now(UTC))
        pending = await free_link(db, vault, pending_uuid=str(uuid4()), status="RECYCLING")
        clients = [
            client(ready),
            client(bad_limits, limitIp=2),
            client(used),
            client(timed, expiryTime=now_ms(timedelta(days=1))),
            client(retired),
            client(pending),
        ]
        stats = [{"email": used.panel_email, "up": 10}]
        await apply_snapshot(db, snapshot(clients, stats), make_settings())
        assert ready.status == "FREE" and ready.ready_at
        assert bad_limits.status == "UNAVAILABLE" and used.status == "UNAVAILABLE"
        assert timed.status == "NEEDS_RESET" and gone.status == "UNAVAILABLE"
        assert retired.status == "RETIRED" and pending.status == "RECYCLING"


async def test_apply_snapshot_tracks_assigned_trial_lifecycle(sessions, vault):
    settings = make_settings()
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        issued = datetime.now(UTC) - timedelta(hours=2)
        record = await free_link(db, vault, user_id=user.id, status="WAITING", issued_at=issued)
        # Not used yet: stays waiting.
        await apply_snapshot(db, snapshot([client(record)]), settings)
        assert record.status == "WAITING"
        # First use: deadline computed from lastOnline.
        online = now_ms(-timedelta(minutes=30))
        await apply_snapshot(
            db,
            snapshot([client(record)], [{"email": record.panel_email, "lastOnline": online}]),
            settings,
        )
        assert record.status == "ACTIVATING"
        assert record.activation_deadline_ms == online + DURATION_MS
        # Panel shows a different expiry than our deadline: still activating.
        await apply_snapshot(db, snapshot([client(record)]), settings)
        assert record.status == "ACTIVATING"
        # Panel confirms the deadline: active.
        confirmed = client(record, expiryTime=record.activation_deadline_ms)
        await apply_snapshot(db, snapshot([confirmed]), settings)
        assert record.status == "ACTIVE" and utc(record.expires_at) > datetime.now(UTC)


async def test_apply_snapshot_finished_trial_never_restarts(sessions, vault):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        record = await free_link(
            db,
            vault,
            user_id=user.id,
            status="WAITING",
            issued_at=datetime.now(UTC) - timedelta(days=3),
        )
        expired = now_ms(-timedelta(hours=1))
        stats = [{"email": record.panel_email, "up": 5, "expiryTime": expired}]
        await apply_snapshot(db, snapshot([client(record)], stats), make_settings())
        assert record.status == "EXPIRED" and record.activation_deadline_ms == expired


async def test_apply_snapshot_repairs_false_activation(sessions, vault):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        record = await free_link(
            db,
            vault,
            user_id=user.id,
            status="ACTIVE",
            issued_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
        db.add(TrialUsage(fingerprint=fingerprint(1, SECRET), activated_at=datetime.now(UTC)))
        db.add(
            Notification(
                dedupe_key=f"trial-active:{record.id}:x",
                user_id=user.id,
                notification_type="TRIAL_ACTIVE",
                text="t",
            )
        )
        await db.flush()
        await apply_snapshot(db, snapshot([client(record, enable=False)]), make_settings())
        assert record.status == "BLOCKED" and record.started_at is None
        assert (await db.scalar(select(Notification))).status == "CANCELLED"
        assert (await db.scalar(select(TrialUsage))).activated_at is None
        # The panel no longer has the key: assigned records are left alone.
        await apply_snapshot(db, snapshot([]), make_settings())
        assert record.status == "BLOCKED"


async def test_synchronize(sessions, vault, monkeypatch):
    calls = []

    class Reader:
        def __init__(self, *args, **kwargs):
            calls.append(("init", args, kwargs))

        async def get_inbound(self, inbound_id):
            return {"settings": {"clients": []}}

        async def close(self):
            calls.append(("close",))

    async def record(name):
        async def inner(*_args, **_kwargs):
            calls.append((name,))

        return inner

    import app.integrations.xui.v328 as v328

    monkeypatch.setattr(v328, "XUI328Client", Reader)
    monkeypatch.setattr(trial_activation, "reconcile_activations", await record("activations"))
    monkeypatch.setattr(trial_recycling, "recycle_trials", await record("recycle"))
    await trial_pool.synchronize(sessions, vault, make_settings())
    assert calls == []  # not configured
    config = {"base_url": "https://panel", "api_token": "t", "inbound_id": 1}
    async with sessions.begin() as db:
        db.add(
            AppSetting(
                key="trial_observer",
                value=vault.cipher.encrypt(json.dumps(config).encode()).decode(),
            )
        )
    await trial_pool.synchronize(sessions, vault, make_settings())
    assert [c[0] for c in calls] == ["init", "close", "activations", "recycle"]
    assert calls[0][2] == {"allow_writes": False}


# ---------- activation reconciliation ----------


class FakeTrialClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.confirmed, self.replaced = [], []
        self.fail = FakeTrialClient.fail
        self.closed = False
        FakeTrialClient.instances.append(self)

    async def confirm_trial_deadline(self, key, email, deadline, issued, baseline):
        if self.fail:
            raise self.fail
        self.confirmed.append(key)

    async def replace_trial_key(self, old, new, email, reason, issued, baseline):
        if self.fail:
            raise self.fail
        self.replaced.append((old, new, reason))
        return 42

    async def close(self):
        self.closed = True


@pytest.fixture
def trial_client(monkeypatch):
    FakeTrialClient.instances = []
    FakeTrialClient.fail = None
    monkeypatch.setattr(trial_activation, "TrialKeyClient", FakeTrialClient)
    monkeypatch.setattr(trial_recycling, "TrialKeyClient", FakeTrialClient)
    return FakeTrialClient


CONFIG = {"base_url": "https://panel", "api_token": "t", "inbound_id": 1}


async def test_reconcile_activations(sessions, vault, trial_client, plan_id):
    await trial_activation.reconcile_activations(sessions, make_settings(), CONFIG)
    assert trial_client.instances == []  # nothing to do
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        record = await free_link(
            db,
            vault,
            user_id=user.id,
            status="ACTIVATING",
            issued_at=datetime.now(UTC),
            activation_deadline_ms=now_ms(timedelta(days=2)),
        )
        sold = await free_link(
            db,
            vault,
            user_id=(await add_user(db, 2)).id,
            status="ACTIVATING",
            issued_at=datetime.now(UTC),
            activation_deadline_ms=now_ms(timedelta(days=2)),
        )
        db.add(
            PaidLink(label="p", client_uuid=sold.client_uuid, link_encrypted="x", plan_id=plan_id)
        )
        record_id = record.id
    await trial_activation.reconcile_activations(sessions, make_settings(), CONFIG)
    fake = trial_client.instances[-1]
    assert fake.confirmed and fake.closed and len(fake.confirmed) == 1
    async with sessions() as db:
        assert (await db.get(TrialLink, record_id)).status == "ACTIVE"


async def test_reconcile_activations_tolerates_panel_errors(sessions, vault, trial_client):
    trial_client.fail = XUIError("down")
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        record = await free_link(
            db,
            vault,
            user_id=user.id,
            status="ACTIVATING",
            issued_at=datetime.now(UTC),
            activation_deadline_ms=now_ms(timedelta(days=2)),
        )
        record_id = record.id
    await trial_activation.reconcile_activations(sessions, make_settings(), CONFIG)
    async with sessions() as db:
        assert (await db.get(TrialLink, record_id)).status == "ACTIVATING"


# ---------- recycling ----------


def test_replace_link():
    old, new = str(uuid4()), str(uuid4())
    assert replace_link(link(old), old, new) == link(new)
    with pytest.raises(ValueError):
        replace_link(link(old), new, old)


async def unused_assignment(sessions, vault):
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        record = await free_link(
            db,
            vault,
            user_id=user.id,
            status="WAITING",
            issued_at=datetime.now(UTC) - timedelta(hours=2),
        )
        return record.id, record.client_uuid, user.id


async def test_recycles_unused_trial_and_notifies_user(sessions, vault, trial_client):
    record_id, old_uuid, user_id = await unused_assignment(sessions, vault)
    await recycle_trials(sessions, vault, make_settings(), CONFIG)
    ((old, new, reason),) = trial_client.instances[-1].replaced
    assert (old, reason) == (old_uuid, "UNUSED")
    async with sessions() as db:
        record = await db.get(TrialLink, record_id)
        assert (record.client_uuid, record.status, record.user_id) == (new, "FREE", None)
        assert record.panel_email == replacement_email(new) and record.baseline_bytes == 42
        assert new in vault.reveal(record.link_encrypted)
        note = await db.scalar(select(Notification))
        assert note.user_id == user_id and note.dedupe_key.startswith("trial-recycled:")


async def test_recycle_backs_off_when_trial_started(sessions, vault, trial_client):
    trial_client.fail = TrialStarted("used")
    record_id, old_uuid, _ = await unused_assignment(sessions, vault)
    await recycle_trials(sessions, vault, make_settings(), CONFIG)
    async with sessions() as db:
        record = await db.get(TrialLink, record_id)
        assert record.client_uuid == old_uuid and record.status == "UNVERIFIED"
        assert record.pending_uuid is None


async def test_recycle_failure_keeps_pending_and_alerts_admin(sessions, vault, trial_client):
    trial_client.fail = XUIError("down")
    record_id, _, _ = await unused_assignment(sessions, vault)
    async with sessions.begin() as db:
        await add_user(db, 999)
    await recycle_trials(sessions, vault, make_settings(admin_telegram_ids="999"), CONFIG)
    async with sessions() as db:
        record = await db.get(TrialLink, record_id)
        assert record.status == "RECYCLING" and record.pending_uuid
        assert "trial-rotation-error" in (await db.scalar(select(Notification))).dedupe_key


async def test_recycles_expired_and_idle_keys(sessions, vault, trial_client):
    now = datetime.now(UTC)
    async with sessions.begin() as db:
        user = await add_user(db, 1)
        expired = await free_link(
            db,
            vault,
            user_id=user.id,
            status="EXPIRED",
            issued_at=now - timedelta(days=3),
            started_at=now - timedelta(days=2, hours=1),
            expires_at=now - timedelta(hours=1),
        )
        idle = await free_link(
            db, vault, status="NEEDS_RESET", ready_at=now, expires_at=now + timedelta(days=1)
        )
        skipped = await free_link(
            db,
            vault,
            status="EXPIRED",
            user_id=(await add_user(db, 2)).id,
            started_at=now - timedelta(days=10),
            expires_at=now - timedelta(hours=1),
        )
        ids = expired.id, idle.id, skipped.id
    await recycle_trials(sessions, vault, make_settings(), CONFIG)
    reasons = sorted(reason for _, _, reason in trial_client.instances[-1].replaced)
    assert reasons == ["EXPIRED", "IDLE"]
    async with sessions() as db:
        assert (await db.get(TrialLink, ids[2])).status == "EXPIRED"
        assert (await db.get(User, (await db.get(TrialLink, ids[2])).user_id)) is not None
