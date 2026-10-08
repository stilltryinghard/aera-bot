import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest
from conftest import add_order, add_user, asgi_client
from sqlalchemy import select

import app.main as main
from app.api import checkout_api, external_payments
from app.core.security import token_hash
from app.db.models import (
    AppSetting,
    AuditLog,
    CryptoMockReceipt,
    PaidLink,
    Payment,
    Plan,
    PromoCode,
    SaleOrder,
    Server,
    Subscription,
    User,
    VPNClient,
)
from app.integrations.payments.freekassa import digest

ADMIN = {"x-admin-token": "admin-token", "x-admin-telegram-id": "999"}


@pytest.fixture
def redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(main, "redis", fake)
    return fake


@pytest.fixture
async def client(app_sessions, app_settings, redis):
    app_settings(admin_api_token="admin-token", admin_telegram_ids="999")
    async with asgi_client(main.app) as http:
        yield http


@pytest.fixture
async def plan_id(app_sessions):
    async with app_sessions.begin() as db:
        plan = Plan(
            name="Month", slug="month", price_minor=19900, traffic_limit_bytes=0, device_limit=3
        )
        db.add(plan)
        await db.flush()
        return plan.id


# ---------- health and middleware ----------


async def test_health_and_request_id(client):
    response = await client.get("/health")
    assert response.json() == {"status": "ok", "mode": "mock"}
    assert response.headers["X-Request-ID"]
    assert (await client.get("/api/v1/health")).status_code == 200


async def test_ready(client, redis, monkeypatch):
    assert (await client.get("/ready")).json() == {"database": "ok", "redis": "ok"}

    async def down():
        raise ConnectionError

    monkeypatch.setattr(redis, "ping", down)
    assert (await client.get("/ready")).status_code == 503


async def test_business_and_unexpected_errors_hide_details(client, app_sessions):
    async with app_sessions.begin() as db:
        user_id = (await add_user(db, 1)).id
    # AccessDeniedError (an AERAError) maps to 400 with a request id.
    response = await client.post(
        f"/api/v1/admin/users/{user_id}/access", headers=ADMIN, json={"action": "extend"}
    )
    assert response.status_code == 400 and response.json()["request_id"]
    async with asgi_client(main.app) as http:
        http._transport.raise_app_exceptions = False
        response = await http.post(
            "/api/v1/admin/users/missing/access",
            headers=ADMIN,
            json={"action": "extend", "confirmed": True},
        )
    assert response.status_code == 500 and "Unknown" not in response.text


# ---------- subscription endpoint ----------


async def subscribed(app_sessions, plan_id, vault, **sub_values):
    async with app_sessions.begin() as db:
        user = await add_user(db, 1)
        server = Server(
            name="AERA NL",
            code="nl",
            public_config={
                "protocol": "vless",
                "security": "reality",
                "host": "vpn.example",
                "port": 443,
                "public_key": "pbk",
                "server_name": "sni",
                "short_id": "sid",
            },
        )
        db.add(server)
        await db.flush()
        values = dict(status="ACTIVE", expires_at=datetime.now(UTC) + timedelta(days=5))
        values.update(sub_values)
        sub = Subscription(
            user_id=user.id, plan_id=plan_id, traffic_limit_bytes=0, device_limit=1, **values
        )
        db.add(sub)
        await db.flush()
        token, digest_, encrypted = vault.create()
        db.add(
            VPNClient(
                subscription_id=sub.id,
                server_id=server.id,
                inbound_id=1,
                xui_client_id="x",
                email="e",
                traffic_limit_bytes=0,
                expires_at=sub.expires_at,
                enabled=True,
                subscription_token_hash=digest_,
                token_encrypted=encrypted,
            )
        )
        return token


async def test_subscription_serves_config(client, app_sessions, plan_id, vault, app_settings):
    token = await subscribed(app_sessions, plan_id, vault)
    response = await client.get(f"/sub/{token}")
    assert response.status_code == 200 and response.text.startswith("# AERA MOCK")
    assert response.headers["Cache-Control"] == "no-store"
    assert "expire=" in response.headers["Subscription-Userinfo"]
    app_settings(xui_mock_mode=False)
    real = await client.get(f"/sub/{token}")
    import base64

    assert base64.b64decode(real.text).decode().startswith("vless://")


@pytest.mark.parametrize(
    "sub_values",
    [{"status": "SUSPENDED"}, {"expires_at": datetime.now(UTC) - timedelta(minutes=1)}],
)
async def test_subscription_denies_inactive(client, app_sessions, plan_id, vault, sub_values):
    token = await subscribed(app_sessions, plan_id, vault, **sub_values)
    assert (await client.get(f"/sub/{token}")).status_code == 404


async def test_subscription_unknown_and_long_tokens(client):
    assert (await client.get("/sub/unknown")).status_code == 404
    assert (await client.get("/sub/" + "x" * 129)).status_code == 404


async def test_subscription_rate_limit_and_redis_failure(client, redis, monkeypatch):
    key_prefix = "sub-rate:" + token_hash("127.0.0.1") + ":"
    import time

    await redis.set(key_prefix + str(int(time.time()) // 60), 60)
    assert (await client.get("/sub/anything")).status_code == 429

    async def down(*_args):
        raise ConnectionError

    monkeypatch.setattr(redis, "incr", down)
    assert (await client.get("/sub/anything")).status_code == 503


# ---------- mock payment confirmation ----------


async def test_mock_confirm(client, app_sessions, plan_id, vault, app_settings):
    async with app_sessions.begin() as db:
        user = await add_user(db, 1)
        payment = Payment(
            user_id=user.id,
            plan_id=plan_id,
            provider="mock",
            amount_minor=19900,
            currency="RUB",
            details={"duration_days": 30},
        )
        other = Payment(
            user_id=user.id, plan_id=plan_id, provider="yookassa", amount_minor=1, currency="RUB"
        )
        db.add_all([payment, other])
        await db.flush()
        db.add(Server(name="s", code="s"))
        ids = payment.id, other.id
    headers = {"x-mock-secret": "local-mock-only"}
    url = "/api/v1/payments/mock/confirm"
    assert (await client.post(url, json={"payment_id": ids[0]})).status_code == 403
    assert (await client.post(url, json={"payment_id": ids[1]}, headers=headers)).status_code == 404
    response = await client.post(url, json={"payment_id": ids[0]}, headers=headers)
    assert response.json()["status"] == "ACTIVE"
    app_settings(payment_provider="telegram_stars")
    assert (await client.post(url, json={"payment_id": ids[0]}, headers=headers)).status_code == 404


# ---------- Telegram webhook ----------


async def test_telegram_webhook(client, app_settings, monkeypatch):
    url = "/api/v1/telegram/webhook"
    assert (await client.post(url, json={})).status_code == 403
    app_settings(bot_token="1:x", telegram_webhook_secret="s" * 32)
    fed = []

    class Dispatcher:
        async def feed_update(self, bot, update):
            fed.append(update.update_id)

    import app.bot.client as bot_client
    import app.bot.runtime as runtime

    monkeypatch.setattr(runtime, "create_dispatcher", Dispatcher)
    monkeypatch.setattr(bot_client, "create_bot", lambda settings: object())
    headers = {"x-telegram-bot-api-secret-token": "s" * 32}
    assert (
        await client.post(
            url, json={"update_id": 1}, headers={"x-telegram-bot-api-secret-token": "wrong"}
        )
    ).status_code == 403
    response = await client.post(url, json={"update_id": 7}, headers=headers)
    assert response.json() == {"ok": True} and fed == [7]
    for attr in ("dispatcher", "bot"):
        delattr(main.app.state, attr)


# ---------- admin API ----------


async def test_admin_requires_token_and_admin_id(client):
    assert (await client.get("/api/v1/admin/analytics")).status_code == 403
    bad = {**ADMIN, "x-admin-telegram-id": "1"}
    assert (await client.get("/api/v1/admin/analytics", headers=bad)).status_code == 403


async def test_admin_analytics_and_listing(client, app_sessions, plan_id):
    async with app_sessions.begin() as db:
        await add_user(db, 1)
    stats = (await client.get("/api/v1/admin/analytics", headers=ADMIN)).json()
    assert stats["users"] == 1
    users = (await client.get("/api/v1/admin/users?limit=500", headers=ADMIN)).json()
    assert users[0]["telegram_id"] == 1
    assert (await client.get("/api/v1/admin/payments", headers=ADMIN)).status_code == 404


async def test_admin_plans(client, app_sessions):
    body = {
        "name": "Year",
        "slug": "year",
        "price_minor": 100,
        "duration_days": 365,
        "traffic_limit_bytes": 0,
        "device_limit": 2,
    }
    created = await client.post("/api/v1/admin/plans", headers=ADMIN, json=body)
    plan_id = created.json()["id"]
    updated = await client.put(
        f"/api/v1/admin/plans/{plan_id}", headers=ADMIN, json={**body, "price_minor": 200}
    )
    assert updated.json()["id"] == plan_id
    assert (
        await client.put("/api/v1/admin/plans/missing", headers=ADMIN, json=body)
    ).status_code == 404
    assert (
        await client.post("/api/v1/admin/plans", headers=ADMIN, json={**body, "slug": "Bad Slug"})
    ).status_code == 422
    async with app_sessions() as db:
        assert (await db.get(Plan, plan_id)).price_minor == 200
        assert len(list(await db.scalars(select(AuditLog)))) == 2


async def test_admin_access_promos_support_broadcast_settings(client, app_sessions, plan_id):
    async with app_sessions.begin() as db:
        user = await add_user(db, 1)
        db.add(
            Subscription(
                user_id=user.id,
                plan_id=plan_id,
                expires_at=datetime.now(UTC),
                traffic_limit_bytes=0,
                device_limit=1,
            )
        )
        _, ticket, _ = await add_order(db, plan_id, 2)
        user_id, ticket_id = user.id, ticket.id
    access = await client.post(
        f"/api/v1/admin/users/{user_id}/access",
        headers=ADMIN,
        json={"action": "extend", "confirmed": True, "days": 3},
    )
    assert access.json() == {"status": "queued"}

    promo = {"code": "SPRING", "discount_type": "PERCENT", "discount_value": 20, "max_uses": 5}
    assert (await client.post("/api/v1/admin/promos", headers=ADMIN, json=promo)).status_code == 200
    too_much = {**promo, "code": "FREE", "discount_value": 100}
    assert (
        await client.post("/api/v1/admin/promos", headers=ADMIN, json=too_much)
    ).status_code == 422

    reply = await client.post(
        f"/api/v1/admin/support/{ticket_id}/reply", headers=ADMIN, json={"text": "Hello"}
    )
    assert reply.json() == {"status": "queued"}

    sent = await client.post(
        "/api/v1/admin/broadcasts", headers=ADMIN, json={"text": "News", "confirmed": True}
    )
    assert sent.json()["total"] == 2

    assert (
        await client.put("/api/v1/admin/settings/trial_days", headers=ADMIN, json={"value": "2"})
    ).json() == {"ok": True}
    assert (
        await client.put("/api/v1/admin/settings/app_secret", headers=ADMIN, json={"value": "x"})
    ).status_code == 422
    async with app_sessions() as db:
        assert (await db.get(AppSetting, "trial_days")).value == "2"
        assert await db.scalar(select(PromoCode))


# ---------- YooKassa and Crypto Pay events ----------


async def test_yookassa_webhook(app_sessions, app_settings, redis, plan_id, monkeypatch):
    reconciled = []

    class Checkout:
        def __init__(self, *args):
            pass

        async def reconcile(self, payment_id):
            reconciled.append(payment_id)
            return "PAID"

    monkeypatch.setattr(external_payments, "CheckoutService", Checkout)
    async with app_sessions.begin() as db:
        user = await add_user(db, 1)
        payment = Payment(
            user_id=user.id,
            plan_id=plan_id,
            provider="yookassa",
            amount_minor=1,
            currency="RUB",
            provider_payment_id="yk-1",
        )
        db.add(payment)
        await db.flush()
        payment_id = payment.id
    event = {"type": "notification", "event": "payment.succeeded", "object": {"id": "yk-1"}}
    url = "/api/v1/payments/yookassa/webhook"
    async with asgi_client(main.app, peer="8.8.8.8") as untrusted:
        assert (await untrusted.post(url, json=event)).status_code == 403
    async with asgi_client(main.app, peer="185.71.76.1") as yookassa:
        assert (await yookassa.post(url, json={**event, "event": "refund"})).status_code == 400
        missing = {**event, "object": {"id": "unknown"}}
        assert (await yookassa.post(url, json=missing)).status_code == 404
        assert (await yookassa.post(url, json=event)).json() == {"status": "PAID"}
    assert reconciled == [payment_id]


async def test_crypto_pay_webhook(app_sessions, app_settings, redis, plan_id, monkeypatch):
    app_settings(crypto_pay_token="tok")

    class Checkout:
        def __init__(self, *args):
            pass

        async def reconcile(self, payment_id):
            return "PAID"

    monkeypatch.setattr(external_payments, "CheckoutService", Checkout)
    async with app_sessions.begin() as db:
        user = await add_user(db, 1)
        db.add(
            Payment(
                user_id=user.id,
                plan_id=plan_id,
                provider="crypto_pay",
                amount_minor=1,
                currency="RUB",
                provider_payment_id="42",
            )
        )
    raw = json.dumps(
        {
            "update_type": "invoice_paid",
            "request_date": datetime.now(UTC).isoformat(),
            "payload": {"invoice_id": 42},
        }
    ).encode()
    signature = hmac.new(hashlib.sha256(b"tok").digest(), raw, hashlib.sha256).hexdigest()
    async with asgi_client(main.app) as http:
        response = await http.post(
            "/api/v1/payments/crypto-pay/webhook",
            content=raw,
            headers={"crypto-pay-api-signature": signature},
        )
    assert response.json() == {"status": "PAID"}


# ---------- FreeKassa ----------

FK = dict(
    freekassa_enabled=True,
    card_provider="freekassa",
    freekassa_merchant_id="123",
    freekassa_secret1="s1",
    freekassa_secret2="s2",
)
FORM = {"content-type": "application/x-www-form-urlencoded"}


def fk_body(payment_id, amount="199.00", sign=None):
    sign = sign or digest(f"123:{amount}:s2:{payment_id}")
    return (
        f"MERCHANT_ID=123&AMOUNT={amount}&MERCHANT_ORDER_ID={payment_id}&intid=555"
        f"&CUR_ID=42&SIGN={sign}"
    )


async def test_freekassa_webhook(app_sessions, app_settings, redis, plan_id):
    url = "/api/v1/payments/freekassa/webhook"
    async with asgi_client(main.app, peer="168.119.157.136") as fk:
        assert (await fk.post(url, content="x", headers=FORM)).status_code == 404  # disabled
        app_settings(**FK, manual_sales=False)
        async with app_sessions.begin() as db:
            user = await add_user(db, 1)
            payment = Payment(
                user_id=user.id,
                plan_id=plan_id,
                provider="freekassa",
                amount_minor=19900,
                currency="RUB",
                details={"duration_days": 30},
            )
            db.add(payment)
            await db.flush()
            payment.provider_payment_id = "fk-order:" + payment.id
            payment_id = payment.id
        assert (
            await fk.post(url, content="x", headers={"content-type": "text/plain"})
        ).status_code == 415
        assert (await fk.post(url, content="a=" + "x" * 9000, headers=FORM)).status_code == 413
        assert (await fk.post(url, content="a=1&a=2", headers=FORM)).status_code == 400
        assert (await fk.post(url, content="%zz", headers=FORM)).status_code == 400
        bad = fk_body(payment_id, sign="0" * 32)
        assert (await fk.post(url, content=bad, headers=FORM)).status_code == 400
        response = await fk.post(url, content=fk_body(payment_id), headers=FORM)
        assert response.text == "YES"
    async with asgi_client(main.app, peer="8.8.8.8") as stranger:
        assert (
            await stranger.post(url, content=fk_body(payment_id), headers=FORM)
        ).status_code == 403
    async with app_sessions() as db:
        assert (await db.get(Payment, payment_id)).status == "PAID"


async def test_payment_pages(client, app_settings):
    for path, title in (
        ("/payments/success", "Проверяем оплату"),
        ("/payments/failure", "Оплата не завершена"),
        ("/", "AERA VPN"),
    ):
        response = await client.get(path)
        assert title in response.text and response.headers["Cache-Control"] == "no-store"
    app_settings(bot_username="@Bad Name!")
    assert "t.me/AERAVPN_BOT" in (await client.get("/")).text


# ---------- crypto simulation ----------


async def test_crypto_simulation(client, app_sessions, app_settings, plan_id):
    url = "/api/v1/payments/crypto/mock"
    async with app_sessions.begin() as db:
        user_id = (await add_user(db, 1)).id
    body = {"user_id": user_id, "plan_id": plan_id}
    assert (await client.post(url + "/invoices", json=body)).status_code == 404
    app_settings(crypto_mock_enabled=True)
    assert (await client.post(url + "/invoices", json=body)).status_code == 403
    secret = {"x-crypto-mock-secret": "local-crypto-test-only"}
    invoice = (await client.post(url + "/invoices", json=body, headers=secret)).json()
    invoice_id = invoice["invoice_id"]
    assert invoice["amount"] == "1.99"
    ledger = {
        "status": "PAID",
        "received_amount": "1.99",
        "asset": "TEST_USDT",
        "network": "TESTNET",
    }
    assert (
        await client.put(f"{url}/provider-ledger/missing", json=ledger, headers=secret)
    ).status_code == 404
    assert (
        await client.put(f"{url}/provider-ledger/{invoice_id}", json=ledger, headers=secret)
    ).json() == {"ok": True}
    raw = json.dumps(
        {"invoice_id": invoice_id, "request_date": datetime.now(UTC).isoformat()}
    ).encode()
    from app.integrations.payments.crypto import MockCryptoProvider

    signature = MockCryptoProvider(None, "local-crypto-test-only").sign(raw)
    response = await client.post(
        url + "/webhook", content=raw, headers={"x-crypto-signature": signature}
    )
    assert response.json() == {"status": "PAID"}
    async with app_sessions() as db:
        assert (await db.scalar(select(CryptoMockReceipt))).status == "PAID"


# ---------- site checkout bridge ----------


def signed(payload, stamp=None):
    import time

    raw = json.dumps(payload).encode()
    stamp = str(stamp if stamp is not None else int(time.time()))
    sign = hmac.new(b"test-bridge-key", stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, {"x-aera-time": stamp, "x-aera-sign": sign}


@pytest.fixture
def freekassa_api(monkeypatch, app_settings):
    app_settings(**FK)
    calls = []
    state = {
        "methods": [{"id": 36, "is_enabled": True, "fee": {"user": 0}}],
        "orders": [],
        "location": "https://pay.fk.money/abc",
    }

    async def api_call(endpoint, **fields):
        calls.append((endpoint, fields))
        if endpoint == "currencies":
            return {"currencies": state["methods"]}
        if endpoint == "orders":
            return {"orders": state["orders"]}
        return {"location": state["location"], "orderId": 9}

    monkeypatch.setattr(checkout_api, "api_call", api_call)
    state["calls"] = calls
    return state


async def stocked_order(app_sessions, plan_id, vault, **order_values):
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="paid_pool_enabled", value="true"))
        db.add(
            PaidLink(
                label="k",
                client_uuid="c",
                link_encrypted="x",
                plan_id=plan_id,
                status="FREE",
                checked_at=datetime.now(UTC),
            )
        )
        user, _, order = await add_order(db, plan_id, 1, **order_values)
        return user.id, order.id


def checkout_body(intent, **values):
    body = {"intent": intent, "method": "36", "email": "buyer@example.com", "accepted": True}
    body.update(values)
    return body


async def test_checkout_intent_round_trip(monkeypatch):
    token = checkout_api.intent("order-1", "user-1")
    assert checkout_api.verify_intent(token)["order_id"] == "order-1"
    from fastapi import HTTPException

    for bad in (token + "x", "garbage", token.split(".")[0] + ".0"):
        with pytest.raises(HTTPException):
            checkout_api.verify_intent(bad)
    import time

    monkeypatch.setattr(time, "time", lambda: 10**12)
    with pytest.raises(HTTPException):
        checkout_api.verify_intent(token)


async def test_checkout_rejects_unsigned_and_remote(client):
    raw, headers = signed({"action": "summary"})
    async with asgi_client(main.app, peer="8.8.8.8") as remote:
        assert (
            await remote.post("/internal/web-checkout", content=raw, headers=headers)
        ).status_code == 403
    assert (
        await client.post(
            "/internal/web-checkout", content=raw, headers={**headers, "x-aera-sign": "0"}
        )
    ).status_code == 403
    stale_raw, stale = signed({"action": "summary"}, stamp=1)
    assert (
        await client.post("/internal/web-checkout", content=stale_raw, headers=stale)
    ).status_code == 403
    big_raw, big = signed({"pad": "x" * 5000})
    assert (
        await client.post("/internal/web-checkout", content=big_raw, headers=big)
    ).status_code == 400


async def test_checkout_summary(client, app_sessions, plan_id, vault):
    user_id, order_id = await stocked_order(app_sessions, plan_id, vault)
    raw, headers = signed({"action": "summary", "intent": checkout_api.intent(order_id, user_id)})
    summary = (await client.post("/internal/web-checkout", content=raw, headers=headers)).json()
    assert summary["price_minor"] == 19900 and summary["name"] == "Month"
    raw, headers = signed({"action": "summary", "intent": checkout_api.intent(order_id, "x")})
    assert (
        await client.post("/internal/web-checkout", content=raw, headers=headers)
    ).status_code == 403


async def test_checkout_creates_freekassa_order_once(
    client, app_sessions, plan_id, vault, freekassa_api
):
    user_id, order_id = await stocked_order(app_sessions, plan_id, vault)
    raw, headers = signed(checkout_body(checkout_api.intent(order_id, user_id), peer_ip="1.2.3.4"))
    first = await client.post("/internal/web-checkout", content=raw, headers=headers)
    assert first.json() == {"url": "https://pay.fk.money/abc"}
    raw, headers = signed(checkout_body(checkout_api.intent(order_id, user_id)))
    again = await client.post("/internal/web-checkout", content=raw, headers=headers)
    assert again.json() == first.json()
    created = [fields for endpoint, fields in freekassa_api["calls"] if endpoint == "orders/create"]
    assert len(created) == 1 and created[0]["amount"] == "199.00" and created[0]["i"] == 36
    async with app_sessions() as db:
        order = await db.get(SaleOrder, order_id)
        payment = await db.get(Payment, order.payment_id)
        user = await db.get(User, user_id)
    assert payment.provider == "freekassa" and payment.details["api_pending"] is False
    assert user.terms_version == "portal-2"


async def test_checkout_by_plan_without_intent(
    client, app_sessions, plan_id, freekassa_api, app_settings
):
    async with app_sessions.begin() as db:
        db.add(AppSetting(key="paid_pool_enabled", value="true"))
        db.add(
            PaidLink(
                label="k",
                client_uuid="c",
                link_encrypted="x",
                plan_id=plan_id,
                status="FREE",
                checked_at=datetime.now(UTC),
            )
        )
        user_id = (await add_user(db, 1)).id
    raw, headers = signed(checkout_body(None, user_id=user_id, plan_id=plan_id))
    response = await client.post("/internal/web-checkout", content=raw, headers=headers)
    assert response.json()["url"].startswith("https://pay.fk.money/")


@pytest.mark.parametrize(
    "values, status",
    [
        ({"method": "1"}, 400),
        ({"email": "not-an-email"}, 400),
        ({"accepted": False}, 400),
    ],
)
async def test_checkout_input_validation(
    client, app_sessions, plan_id, vault, freekassa_api, values, status
):
    user_id, order_id = await stocked_order(app_sessions, plan_id, vault)
    raw, headers = signed(checkout_body(checkout_api.intent(order_id, user_id), **values))
    assert (
        await client.post("/internal/web-checkout", content=raw, headers=headers)
    ).status_code == status


@pytest.mark.parametrize(
    "state, status",
    [
        ({"methods": [{"id": 36, "is_enabled": False, "fee": {"user": 0}}]}, 409),
        ({"methods": [{"id": 36, "is_enabled": True, "fee": {"user": 1.5}}]}, 503),
        ({"orders": [{"merchant_order_id": "{pid}", "status": 1}]}, 409),
        ({"location": "https://evil.example/pay"}, 502),
    ],
)
async def test_checkout_provider_states(
    client, app_sessions, plan_id, vault, freekassa_api, state, status
):
    user_id, order_id = await stocked_order(app_sessions, plan_id, vault)
    if "orders" in state:
        async with app_sessions.begin() as db:
            user = await db.get(User, user_id)
            payment = Payment(
                user_id=user.id,
                plan_id=plan_id,
                provider="freekassa",
                amount_minor=19900,
                currency="RUB",
            )
            db.add(payment)
            await db.flush()
            (await db.get(SaleOrder, order_id)).payment_id = payment.id
            state = {"orders": [{"merchant_order_id": payment.id, "status": 1}]}
    freekassa_api.update(state)
    raw, headers = signed(checkout_body(checkout_api.intent(order_id, user_id)))
    assert (
        await client.post("/internal/web-checkout", content=raw, headers=headers)
    ).status_code == status


async def test_checkout_order_states(client, app_sessions, plan_id, vault, freekassa_api):
    user_id, order_id = await stocked_order(app_sessions, plan_id, vault)
    intent = checkout_api.intent(order_id, user_id)
    async with app_sessions.begin() as db:
        user = await db.get(User, user_id)
        stars = Payment(
            user_id=user.id,
            plan_id=plan_id,
            provider="telegram_stars",
            amount_minor=10,
            currency="XTR",
        )
        db.add(stars)
        await db.flush()
        (await db.get(SaleOrder, order_id)).payment_id = stars.id
    raw, headers = signed(checkout_body(intent))
    assert (
        await client.post("/internal/web-checkout", content=raw, headers=headers)
    ).status_code == 409
    async with app_sessions.begin() as db:
        order = await db.get(SaleOrder, order_id)
        order.payment_id, order.amount_rub_minor = None, 2000000
    raw, headers = signed(checkout_body(intent))
    assert (
        await client.post("/internal/web-checkout", content=raw, headers=headers)
    ).status_code == 400
    async with app_sessions.begin() as db:
        order = await db.get(SaleOrder, order_id)
        order.amount_rub_minor, order.paid_at = 19900, datetime.now(UTC)
    raw, headers = signed(checkout_body(intent))
    assert (
        await client.post("/internal/web-checkout", content=raw, headers=headers)
    ).status_code == 409


async def test_checkout_without_stock(client, app_sessions, plan_id, freekassa_api):
    async with app_sessions.begin() as db:
        user, _, order = await add_order(db, plan_id, 1)
        ids = user.id, order.id
    raw, headers = signed(checkout_body(checkout_api.intent(ids[1], ids[0])))
    response = await client.post("/internal/web-checkout", content=raw, headers=headers)
    assert response.status_code == 409 and "недоступен" in response.json()["detail"]


async def test_freekassa_api_call_signs_requests(monkeypatch, tmp_path, app_settings):
    import httpx

    app_settings(**FK)
    monkeypatch.setenv("FREEKASSA_API_KEY", "api-key")
    nonce_file = tmp_path / "nonce"
    monkeypatch.setattr(checkout_api, "Path", lambda _path: nonce_file)
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if request.url.path.endswith("/currencies"):
            return httpx.Response(200, json={"type": "success", "currencies": []})
        return httpx.Response(400, json={"type": "error"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        checkout_api.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert await checkout_api.api_call("currencies") == {"type": "success", "currencies": []}
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        await checkout_api.api_call("orders/create", amount="1.00")
    assert error.value.status_code == 502
    first, second = seen
    assert first["shopId"] == 123 and second["nonce"] > first["nonce"]
    payload = {k: v for k, v in first.items() if k != "signature"}
    expected = hmac.new(
        b"api-key", "|".join(str(payload[k]) for k in sorted(payload)).encode(), hashlib.sha256
    ).hexdigest()
    assert first["signature"] == expected
    assert int(nonce_file.read_text()) == second["nonce"]


async def test_lifespan_closes_resources(monkeypatch, redis):
    closed = []

    class Engine:
        async def dispose(self):
            closed.append("engine")

    class State:
        pass

    class Session:
        async def close(self):
            closed.append("bot")

    class Storage:
        async def close(self):
            closed.append("storage")

    monkeypatch.setattr(main, "engine", Engine())
    monkeypatch.setattr(main.adapter, "close", lambda: _closed(closed), raising=False)
    fake_app = State()
    fake_app.state = State()
    fake_app.state.bot = State()
    fake_app.state.bot.session = Session()
    fake_app.state.dispatcher = State()
    fake_app.state.dispatcher.storage = Storage()
    async with main.lifespan(fake_app):
        pass
    assert closed == ["bot", "storage", "adapter", "engine"]


async def _closed(closed):
    closed.append("adapter")
