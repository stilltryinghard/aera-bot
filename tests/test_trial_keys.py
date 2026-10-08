import json
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote
from uuid import uuid4

import httpx
import pytest

from app.integrations.xui.exceptions import XUIError
from app.integrations.xui.trial_keys import (
    DURATION_MS,
    TrialKeyClient,
    TrialStarted,
    replacement_email,
)

ISSUED = datetime.now(UTC) - timedelta(days=1)


class TrialPanel:
    """Stateful stand-in for one 3.2.8 trial client on inbound 1."""

    def __init__(self, uuid, email, expiry=-DURATION_MS, up=0, down=0, last_online=0):
        self.uuid, self.email, self.sub_id, self.expiry = uuid, email, uuid.replace("-", ""), expiry
        self.up, self.down, self.last_online = up, down, last_online
        self.posts = []

    def client(self):
        return {
            "id": self.uuid,
            "email": self.email,
            "subId": self.sub_id,
            "expiryTime": self.expiry,
            "enable": True,
            "totalGB": 0,
            "limitIp": 1,
        }

    def __call__(self, request):
        path = unquote(request.url.raw_path.decode().split("?")[0].lstrip("/"))
        if request.method == "POST":
            body = json.loads(request.content)
            self.posts.append((path, body))
            self.uuid = body["id"]
            self.email = body.get("email", self.email)
            self.sub_id = body.get("subId", self.sub_id)
            self.expiry = body["expiryTime"]
            return ok()
        if path == "panel/api/inbounds/get/1":
            return ok({"id": 1, "protocol": "vless", "settings": {"clients": [self.client()]}})
        if path == f"panel/api/clients/get/{self.email}":
            record = {**self.client(), "id": 17, "uuid": self.uuid, "reset": 0, "createdAt": 5}
            return ok({"inboundIds": [1], "client": record})
        if path == f"panel/api/clients/traffic/{self.email}":
            return ok(
                {
                    "email": self.email,
                    "up": self.up,
                    "down": self.down,
                    "expiryTime": self.expiry,
                    "lastOnline": self.last_online,
                    "enable": True,
                }
            )
        return httpx.Response(404)


def ok(obj=None):
    return httpx.Response(200, json={"success": True, "obj": obj})


def make(panel):
    return TrialKeyClient("https://panel", "token", 1, httpx.MockTransport(panel))


def identities():
    old = str(uuid4())
    return old, str(uuid4()), f"aera-trial-old-{old}"


async def test_replaces_unused_trial_key():
    old, new, email = identities()
    panel = TrialPanel(old, email, up=10, down=5)
    total = await make(panel).replace_trial_key(old, new, email, "UNUSED", ISSUED, 15)
    assert total == 15
    assert (panel.uuid, panel.email, panel.expiry) == (new, replacement_email(new), -DURATION_MS)
    ((path, body),) = panel.posts
    assert path == f"panel/api/clients/update/{email}"
    assert body["created_at"] == 5 and "uuid" not in body and body["enable"] is True


async def test_resumes_interrupted_replacement_without_second_update():
    old, new, email = identities()
    panel = TrialPanel(new, replacement_email(new))
    total = await make(panel).replace_trial_key(old, new, email, "UNUSED", ISSUED, 0)
    assert total == 0 and panel.posts == []


async def test_replaces_expired_trial_key():
    old, new, email = identities()
    past = int((datetime.now(UTC) - timedelta(hours=1)).timestamp() * 1000)
    panel = TrialPanel(old, email, expiry=past, up=100)
    await make(panel).replace_trial_key(old, new, email, "EXPIRED", ISSUED, 0)
    assert panel.uuid == new


@pytest.mark.parametrize(
    "reason, panel_kwargs, baseline",
    [
        ("UNUSED", {"up": 1}, 0),  # traffic moved since issue
        ("UNUSED", {"last_online": int(datetime.now(UTC).timestamp() * 1000)}, 0),
        ("UNUSED", {"expiry": 0}, 0),  # deadline cleared: timer started
        ("EXPIRED", {"expiry": int((datetime.now(UTC) + timedelta(days=1)).timestamp() * 1000)}, 0),
    ],
)
async def test_refuses_to_rotate_started_trials(reason, panel_kwargs, baseline):
    old, new, email = identities()
    panel = TrialPanel(old, email, **panel_kwargs)
    with pytest.raises(TrialStarted):
        await make(panel).replace_trial_key(old, new, email, reason, ISSUED, baseline)
    assert panel.posts == []


async def test_idle_rotation_requires_premature_deadline():
    old, new, email = identities()
    panel = TrialPanel(old, email)
    with pytest.raises(XUIError, match="premature"):
        await make(panel).replace_trial_key(old, new, email, "IDLE", ISSUED, 0)


async def test_rejects_invalid_identities_and_reasons():
    old, new, email = identities()
    client = make(TrialPanel(old, email))
    with pytest.raises(XUIError, match="identity"):
        await client.replace_trial_key(old, old, email, "UNUSED", ISSUED, 0)
    with pytest.raises(XUIError, match="identity"):
        await client.replace_trial_key(old.upper(), new, email, "UNUSED", ISSUED, 0)
    with pytest.raises(XUIError, match="Unsupported"):
        await client.replace_trial_key(old, new, email, "OTHER", ISSUED, 0)


async def test_only_rotation_route_may_be_written():
    client = make(TrialPanel(*identities()[::2]))
    with pytest.raises(XUIError, match="selected trial key"):
        await client.request("POST", "panel/api/clients/add", {})


async def test_rejects_ambiguous_or_shared_trial():
    old, new, email = identities()
    panel = TrialPanel(old, email)
    original = panel.client
    panel.client = lambda: {**original(), "totalGB": 5}
    with pytest.raises(XUIError, match="limits"):
        await make(panel).replace_trial_key(old, new, email, "UNUSED", ISSUED, 0)
    with pytest.raises(XUIError, match="ambiguous"):
        await make(TrialPanel(old, "someone-else")).replace_trial_key(
            old, new, email, "UNUSED", ISSUED, 0
        )


async def test_confirms_first_use_deadline():
    identity = str(uuid4())
    email = f"aera-trial-{identity}"
    deadline = int((datetime.now(UTC) + timedelta(days=2)).timestamp() * 1000)
    panel = TrialPanel(identity, email, up=50)
    await make(panel).confirm_trial_deadline(identity, email, deadline, ISSUED, 0)
    assert panel.expiry == deadline and len(panel.posts) == 1
    await make(panel).confirm_trial_deadline(identity, email, deadline, ISSUED, 0)
    assert len(panel.posts) == 1  # already set: no second write


async def test_deadline_requires_confirmed_use():
    identity = str(uuid4())
    email = f"aera-trial-{identity}"
    panel = TrialPanel(identity, email)
    with pytest.raises(XUIError, match="not confirmed"):
        await make(panel).confirm_trial_deadline(identity, email, 1, ISSUED, 0)
