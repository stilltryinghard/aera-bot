import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.config import Settings
from app.core.exceptions import ProvisioningError
from app.db.models import Server
from app.integrations.xui.exceptions import (
    XUIAuthenticationError,
    XUIError,
    XUIUnavailableError,
)
from app.integrations.xui.factory import RoutedXUIClient, create_adapter
from app.integrations.xui.mapper import client_payload
from app.integrations.xui.mock import MockXUIClient
from app.integrations.xui.real import XUI285Client
from app.integrations.xui.schemas import ClientSpec, Traffic
from app.integrations.xui.v328 import XUI328Client

EXPIRES = datetime(2030, 1, 1, tzinfo=UTC)


def spec(uuid=None, enabled=True, **overrides):
    uuid = uuid or str(uuid4())
    values = dict(
        uuid=uuid,
        email=f"aera-{uuid}",
        inbound_id=1,
        expires_at=EXPIRES,
        traffic_limit_bytes=0,
        enabled=enabled,
    )
    values.update(overrides)
    return ClientSpec(**values)


def ok(obj=None):
    return httpx.Response(200, json={"success": True, "obj": obj})


def inbound(clients, protocol="vless", **extra):
    return {"id": 1, "protocol": protocol, "settings": json.dumps({"clients": clients}), **extra}


class Panel:
    """Records requests and replies from a route table."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request):
        path = request.url.raw_path.decode().split("?")[0].lstrip("/")
        try:
            body = json.loads(request.content) if request.content else None
        except ValueError:
            body = request.content.decode()  # form-encoded login
        self.calls.append((request.method, path, body))
        for prefix, reply in self.routes.items():
            if path.startswith(prefix):
                return reply(request) if callable(reply) else reply
        return httpx.Response(404)

    def posted(self):
        return [(path, body) for method, path, body in self.calls if method == "POST"]


# ---------- mapper / schemas / mock ----------


def test_client_payload_preserves_existing_fields():
    s = spec()
    payload = client_payload(s, {"flow": "custom", "limitIp": 2, "subId": "keep"})
    assert payload["id"] == s.uuid and payload["email"] == s.email
    assert payload["flow"] == "custom" and payload["limitIp"] == 2 and payload["subId"] == "keep"
    assert payload["expiryTime"] == int(EXPIRES.timestamp() * 1000)
    naive = client_payload(spec(expires_at=datetime(2030, 1, 1)))
    assert naive["expiryTime"] == payload["expiryTime"]
    assert client_payload(s)["flow"] == "xtls-rprx-vision"


async def test_mock_xui_client():
    client = MockXUIClient()
    s = spec()
    await client.ensure_client(s)
    assert client.clients[s.uuid] == s
    assert await client.get_client_traffic("x") == Traffic()
    await client.delete_client(1, s.uuid)
    assert client.clients == {}
    client.available = False
    assert await client.get_server_status() is False
    with pytest.raises(ConnectionError):
        await client.ensure_client(s)


# ---------- 2.8.5 cookie adapter ----------


def client285(panel, allow_writes=True):
    return XUI285Client(
        "https://panel/base", "u", "p", httpx.MockTransport(panel), allow_writes=allow_writes
    )


async def test_285_logs_in_once_and_reads():
    panel = Panel({"base/login": ok(), "base/panel/api/inbounds/list": ok([{"id": 1}])})
    client = client285(panel)
    assert await client.get_inbounds() == [{"id": 1}]
    assert await client.get_inbounds() == [{"id": 1}]
    assert [p for _, p, _ in panel.calls].count("base/login") == 1


async def test_285_reauthenticates_on_redirect():
    state = {"n": 0}

    def status(request):
        state["n"] += 1
        return httpx.Response(302) if state["n"] == 1 else ok({"cpu": 1})

    panel = Panel({"base/login": ok(), "base/panel/api/server/status": status})
    assert await client285(panel).get_server_status() is True
    assert [p for _, p, _ in panel.calls].count("base/login") == 2


@pytest.mark.parametrize(
    "login",
    [
        httpx.Response(200, json={"success": False}),
        httpx.Response(500),
        httpx.Response(200, text="not json"),
    ],
)
async def test_285_login_failures(login):
    panel = Panel({"base/login": login})
    with pytest.raises(XUIAuthenticationError):
        await client285(panel).get_inbounds()


async def test_285_request_failures():
    panel = Panel(
        {
            "base/login": ok(),
            "base/panel/api/inbounds/list": httpx.Response(200, json={"success": False}),
            "base/panel/api/inbounds/get": httpx.Response(500),
        }
    )
    client = client285(panel)
    with pytest.raises(XUIError, match="rejected"):
        await client.get_inbounds()
    with pytest.raises(XUIUnavailableError):
        await client.get_inbound(1)


async def test_285_writes_disabled_by_default():
    client = client285(Panel({}), allow_writes=False)
    with pytest.raises(XUIError, match="disabled"):
        await client.delete_client(1, "x")


async def test_285_adds_new_client():
    s = spec()
    panel = Panel(
        {
            "base/login": ok(),
            "base/panel/api/inbounds/get/1": ok(inbound([])),
            "base/panel/api/inbounds/addClient": ok(),
        }
    )
    await client285(panel).add_client(s)
    ((path, body),) = panel.posted()[1:]
    assert path == "base/panel/api/inbounds/addClient"
    assert json.loads(body["settings"])["clients"][0]["id"] == s.uuid


async def test_285_updates_changed_client_and_skips_unchanged():
    s = spec()
    current = client_payload(s)
    stale = {**current, "enable": False}
    for existing, expect_update in ((stale, True), (current, False)):
        panel = Panel(
            {
                "base/login": ok(),
                "base/panel/api/inbounds/get/1": ok(inbound([existing])),
                "base/panel/api/inbounds/updateClient": ok(),
            }
        )
        await client285(panel).update_client(s)
        updates = [p for p, _ in panel.posted() if "updateClient" in p]
        assert bool(updates) is expect_update


async def test_285_rejects_conflicts_and_other_protocols():
    s = spec()
    conflict = Panel(
        {
            "base/login": ok(),
            "base/panel/api/inbounds/get/1": ok(inbound([{"id": "other", "email": s.email}])),
        }
    )
    with pytest.raises(XUIError, match="conflict"):
        await client285(conflict).ensure_client(s)
    vmess = Panel({"base/login": ok(), "base/panel/api/inbounds/get/1": ok(inbound([], "vmess"))})
    with pytest.raises(XUIError, match="VLESS"):
        await client285(vmess).ensure_client(s)


async def test_285_traffic_delete_and_reset():
    panel = Panel(
        {
            "base/login": ok(),
            "base/panel/api/inbounds/getClientTraffics/a%40b": ok({"up": 3, "down": "4"}),
            "base/panel/api/inbounds/getClientTraffics/none": ok(None),
            "base/panel/api/inbounds/1/": ok(),
        }
    )
    client = client285(panel)
    assert await client.get_client_traffic("a@b") == Traffic(3, 4)
    with pytest.raises(XUIError):
        await client.get_client_traffic("none")
    await client.delete_client(1, "u/1")
    await client.reset_client_traffic(1, "e")
    posted = [p for p, _ in panel.posted()]
    assert "base/panel/api/inbounds/1/delClient/u%2F1" in posted
    assert "base/panel/api/inbounds/1/resetClientTraffic/e" in posted
    await client.close()


# ---------- 3.2.8 bearer adapter ----------


def client328(panel, allow_writes=True):
    return XUI328Client(
        "https://panel", "token", 1, httpx.MockTransport(panel), allow_writes=allow_writes
    )


def test_328_requires_token_and_inbound():
    with pytest.raises(XUIError):
        XUI328Client("https://panel", "", 1)
    with pytest.raises(XUIError):
        XUI328Client("https://panel", "t", 0)


def test_328_identity_rules():
    uuid = str(uuid4())
    XUI328Client.identity(uuid, f"aera-{uuid}")
    for bad in ((uuid, "other"), ("not-a-uuid", "aera-not-a-uuid"), (uuid.upper(), "x")):
        with pytest.raises(XUIError):
            XUI328Client.identity(*bad)


async def test_328_sends_bearer_and_checks_scope():
    seen = {}

    def get(request):
        seen["auth"] = request.headers["Authorization"]
        return ok(inbound([]))

    client = client328(Panel({"panel/api/inbounds/get/1": get}))
    await client.authenticate()
    assert seen["auth"] == "Bearer token"
    with pytest.raises(XUIError, match="scope"):
        await client.get_inbound(2)
    mismatch = client328(Panel({"panel/api/inbounds/get/1": ok({"id": 9})}))
    with pytest.raises(XUIError, match="mismatch"):
        await mismatch.get_inbound(1)


@pytest.mark.parametrize("status", [302, 401, 404])
async def test_328_auth_failures(status):
    client = client328(Panel({"panel/api/inbounds/get/1": httpx.Response(status)}))
    with pytest.raises(XUIAuthenticationError):
        await client.get_inbound(1)


async def test_328_request_guards():
    client = client328(Panel({}), allow_writes=False)
    with pytest.raises(XUIError, match="disabled"):
        await client.request("POST", "panel/api/clients/add")
    with pytest.raises(XUIError, match="disabled"):
        await client.ensure_client(spec())
    writable = client328(Panel({}))
    with pytest.raises(XUIError, match="Unsupported"):
        await writable.request("POST", "panel/api/inbounds/del/1")
    broken = client328(Panel({"x": httpx.Response(500)}))
    with pytest.raises(XUIUnavailableError):
        await broken.request("GET", "x")
    rejected = client328(Panel({"x": httpx.Response(200, json=[1])}))
    with pytest.raises(XUIError, match="rejected"):
        await rejected.request("GET", "x")


async def test_328_adds_enabled_client_only():
    s = spec()
    panel = Panel({"panel/api/inbounds/get/1": ok(inbound([])), "panel/api/clients/add": ok()})
    await client328(panel).ensure_client(s)
    ((path, body),) = panel.posted()
    assert path == "panel/api/clients/add"
    assert body["inboundIds"] == [1] and body["client"]["subId"] == s.uuid

    disabled = Panel({"panel/api/inbounds/get/1": ok(inbound([]))})
    await client328(disabled).ensure_client(spec(enabled=False))
    assert disabled.posted() == []


async def test_328_updates_existing_client():
    s = spec()
    existing = {**client_payload(s), "enable": False}
    panel = Panel(
        {
            "panel/api/inbounds/get/1": ok(inbound([existing])),
            "panel/api/clients/get/": ok(
                {"inboundIds": [1], "client": {"uuid": s.uuid, "email": s.email}}
            ),
            "panel/api/clients/update/": ok(),
        }
    )
    await client328(panel).ensure_client(s)
    ((path, body),) = panel.posted()
    assert path == f"panel/api/clients/update/{s.email}"
    assert body["enable"] is True


async def test_328_skips_unchanged_client():
    s = spec()
    panel = Panel(
        {
            "panel/api/inbounds/get/1": ok(inbound([client_payload(s)])),
            "panel/api/clients/get/": ok(
                {"inboundIds": [1], "client": {"uuid": s.uuid, "email": s.email}}
            ),
        }
    )
    await client328(panel).ensure_client(s)
    assert panel.posted() == []


@pytest.mark.parametrize(
    "inbound_body, record",
    [
        (inbound([], "vmess"), None),
        (inbound([], nodeId=3), None),
        ({"id": 1, "protocol": "vless", "settings": "{bad"}, None),
        ({"id": 1, "protocol": "vless", "settings": {"clients": "x"}}, None),
        ("conflict", None),
        ("existing", {"inboundIds": [1, 2], "client": {}}),
        ("existing", {"inboundIds": [1], "client": {"uuid": "other"}}),
    ],
)
async def test_328_rejects_unsafe_states(inbound_body, record):
    s = spec()
    if inbound_body == "conflict":
        inbound_body = inbound([{"id": "other", "email": s.email}])
    elif inbound_body == "existing":
        inbound_body = inbound([client_payload(s)])
    panel = Panel(
        {"panel/api/inbounds/get/1": ok(inbound_body), "panel/api/clients/get/": ok(record)}
    )
    with pytest.raises(XUIError):
        await client328(panel).ensure_client(s)
    assert panel.posted() == []


# ---------- factory ----------


def settings(**overrides):
    values = dict(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        xui_mock_mode=False,
        xui_version="3.2.8",
        xui_credentials_json=json.dumps(
            {"nl": {"api_token": "t", "username": "u", "password": "p"}}
        ),
    )
    values.update(overrides)
    return Settings(**values)


async def add_server(sessions, url="https://panel.example", code="nl"):
    async with sessions.begin() as db:
        server = Server(
            code=code, name=code, xui_base_url=url, inbound_id=1, capacity=10, public_config={}
        )
        db.add(server)
        await db.flush()
        return server.id


def test_create_adapter_mock_mode(sessions):
    assert isinstance(create_adapter(sessions, settings(xui_mock_mode=True)), MockXUIClient)
    assert isinstance(create_adapter(sessions, settings()), RoutedXUIClient)


def test_routed_client_rejects_unknown_version(sessions):
    s = settings()
    s.xui_version = "1.0"
    with pytest.raises(ValueError):
        RoutedXUIClient(sessions, s)


async def test_routed_client_builds_cached_adapter_per_version(sessions):
    server_id = await add_server(sessions)
    routed = RoutedXUIClient(sessions, settings())
    adapter = await routed.for_server(server_id)
    assert isinstance(adapter, XUI328Client)
    assert await routed.for_server(server_id) is adapter
    legacy = RoutedXUIClient(sessions, settings(xui_version="2.8.5"))
    assert type(await legacy.for_server(server_id)) is XUI285Client
    await routed.close()
    await legacy.close()


async def test_routed_client_safety_checks(sessions):
    https_id = await add_server(sessions)
    http_id = await add_server(sessions, url="http://panel.example", code="de")
    routed = RoutedXUIClient(sessions, settings())
    with pytest.raises(ProvisioningError, match="Unknown"):
        await routed.for_server("missing")
    with pytest.raises(ProvisioningError, match="HTTPS"):
        await routed.for_server(http_id)
    protected = RoutedXUIClient(
        sessions, settings(xui_allow_writes=True, xui_protected_hosts="PANEL.example.")
    )
    with pytest.raises(ProvisioningError, match="protected"):
        await protected.for_server(https_id)


async def test_routed_client_delegates(sessions, monkeypatch):
    server_id = await add_server(sessions)
    routed = RoutedXUIClient(sessions, settings())
    fake = MockXUIClient()
    fake.close = lambda: _noop()
    routed.adapters[server_id] = fake
    from app.db.models import Plan, Subscription, User, VPNClient

    async with sessions.begin() as db:
        user = User(telegram_id=1)
        plan = Plan(name="p", slug="p", price_minor=1, traffic_limit_bytes=0, device_limit=1)
        db.add_all([user, plan])
        await db.flush()
        sub = Subscription(
            user_id=user.id,
            plan_id=plan.id,
            expires_at=EXPIRES,
            traffic_limit_bytes=0,
            device_limit=1,
        )
        db.add(sub)
        await db.flush()
        db.add(
            VPNClient(
                subscription_id=sub.id,
                server_id=server_id,
                inbound_id=1,
                xui_client_id="x",
                email="e@x",
                traffic_limit_bytes=0,
                expires_at=EXPIRES,
            )
        )
    s = spec(server_id=server_id)
    await routed.ensure_client(s)
    assert s.uuid in fake.clients
    assert await routed.get_client_traffic("e@x") == Traffic()
    assert await routed.get_server_status() is True


async def _noop():
    return None
