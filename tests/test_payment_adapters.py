import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core.exceptions import PaymentError
from app.integrations.payments.crypto import MockCryptoProvider
from app.integrations.payments.crypto_pay import CryptoPayProvider
from app.integrations.payments.freekassa import FreeKassaProvider, digest
from app.integrations.payments.mock import MockPaymentProvider
from app.integrations.payments.telegram_stars import TelegramStarsProvider
from app.integrations.payments.wata import WATAProvider
from app.integrations.payments.yookassa import YooKassaProvider, trusted_event_ip


def transport(handler):
    return httpx.MockTransport(handler)


# ---------- YooKassa ----------


@pytest.mark.parametrize(
    "peer, trusted",
    [
        ("185.71.76.5", True),
        ("77.75.156.11", True),
        ("2a02:5180::1", True),
        ("8.8.8.8", False),
        ("not-an-ip", False),
    ],
)
def test_yookassa_event_ip_allowlist(peer, trusted):
    assert trusted_event_ip(peer) is trusted


def test_yookassa_requires_credentials():
    with pytest.raises(PaymentError):
        YooKassaProvider("", "secret")


async def test_yookassa_create_payment_sends_idempotent_request():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["Idempotence-Key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "yk-1"})

    adapter = YooKassaProvider("shop", "secret", transport(handler))
    body = await adapter.create_payment("pay-1", 19900, "RUB", "https://back", sbp=True)
    await adapter.close()

    assert body == {"id": "yk-1"}
    assert seen["key"] == "pay-1"
    assert seen["body"]["amount"] == {"value": "199", "currency": "RUB"}
    assert seen["body"]["metadata"] == {"payment_id": "pay-1"}
    assert seen["body"]["payment_method_data"] == {"type": "sbp"}


async def test_yookassa_rejects_non_rub():
    adapter = YooKassaProvider("shop", "secret", transport(lambda r: httpx.Response(200)))
    with pytest.raises(PaymentError):
        await adapter.create_payment("p", 100, "USD", "https://back")


async def test_yookassa_hides_provider_errors():
    adapter = YooKassaProvider("shop", "secret", transport(lambda r: httpx.Response(500)))
    with pytest.raises(PaymentError, match="unavailable"):
        await adapter.verify_payment("yk-1")


async def test_yookassa_verify_and_refund():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.headers.get("Idempotence-Key")))
        return httpx.Response(200, json={"ok": True})

    adapter = YooKassaProvider("shop", "secret", transport(handler))
    await adapter.verify_payment("yk-1")
    await adapter.refund("yk-1", 500, "RUB", "refund-1")
    assert calls == [
        ("GET", "/v3/payments/yk-1", None),
        ("POST", "/v3/refunds", "refund-1"),
    ]


# ---------- Crypto Pay ----------


def crypto_signature(token, raw):
    return hmac.new(hashlib.sha256(token.encode()).digest(), raw, hashlib.sha256).hexdigest()


def crypto_event(**overrides):
    event = {
        "update_type": "invoice_paid",
        "request_date": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "payload": {"invoice_id": 42},
    }
    event.update(overrides)
    return json.dumps(event).encode()


def test_crypto_pay_requires_token():
    with pytest.raises(PaymentError):
        CryptoPayProvider("")


def test_crypto_pay_uses_mainnet_when_not_testnet():
    adapter = CryptoPayProvider("tok", testnet=False)
    assert str(adapter.http.base_url) == "https://pay.crypt.bot/api/"


def test_crypto_pay_accepts_signed_fresh_event():
    adapter = CryptoPayProvider("tok")
    raw = crypto_event()
    assert adapter.verify_webhook(raw, crypto_signature("tok", raw)) == "42"


@pytest.mark.parametrize(
    "raw_factory",
    [
        lambda: crypto_event(update_type="invoice_created"),
        lambda: crypto_event(request_date=(datetime.now(UTC) - timedelta(minutes=10)).isoformat()),
        lambda: crypto_event(request_date="2026-01-01T00:00:00"),
        lambda: json.dumps({"update_type": "invoice_paid"}).encode(),
        lambda: b"not json",
    ],
)
def test_crypto_pay_rejects_bad_events(raw_factory):
    adapter = CryptoPayProvider("tok")
    raw = raw_factory()
    with pytest.raises(PaymentError):
        adapter.verify_webhook(raw, crypto_signature("tok", raw))


def test_crypto_pay_rejects_bad_signature_and_oversized_body():
    adapter = CryptoPayProvider("tok")
    raw = crypto_event()
    with pytest.raises(PaymentError, match="signature"):
        adapter.verify_webhook(raw, crypto_signature("other", raw))
    big = b"x" * 20000
    with pytest.raises(PaymentError, match="signature"):
        adapter.verify_webhook(big, crypto_signature("tok", big))


async def test_crypto_pay_create_invoice_reuses_existing_payload():
    methods = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        items = [{"invoice_id": 1, "payload": "pay-1"}]
        return httpx.Response(200, json={"ok": True, "result": {"items": items}})

    adapter = CryptoPayProvider("tok", transport=transport(handler))
    assert (await adapter.create_invoice("pay-1", "USDT", "5"))["invoice_id"] == 1
    assert methods == ["getInvoices"]


async def test_crypto_pay_create_invoice_creates_when_missing():
    seen = []

    def handler(request):
        method = request.url.path.rsplit("/", 1)[-1]
        seen.append((method, json.loads(request.content)))
        if method == "getInvoices":
            return httpx.Response(200, json={"ok": True, "result": {"items": []}})
        return httpx.Response(200, json={"ok": True, "result": {"invoice_id": 7}})

    adapter = CryptoPayProvider("tok", transport=transport(handler))
    assert await adapter.create_invoice("pay-1", "TON", "1.5") == {"invoice_id": 7}
    assert seen[1][0] == "createInvoice"
    assert seen[1][1]["payload"] == "pay-1" and seen[1][1]["amount"] == "1.5"


@pytest.mark.parametrize("asset, amount", [("DOGE", "1"), ("USDT", "0"), ("USDT", "-1")])
async def test_crypto_pay_create_invoice_validates_input(asset, amount):
    adapter = CryptoPayProvider("tok", transport=transport(lambda r: httpx.Response(500)))
    with pytest.raises(PaymentError):
        await adapter.create_invoice("pay-1", asset, amount)


async def test_crypto_pay_rejected_and_unavailable_responses():
    rejected = CryptoPayProvider(
        "tok", transport=transport(lambda r: httpx.Response(200, json={"ok": False}))
    )
    with pytest.raises(PaymentError, match="rejected"):
        await rejected.verify_payment("1")
    down = CryptoPayProvider("tok", transport=transport(lambda r: httpx.Response(502)))
    with pytest.raises(PaymentError, match="unavailable"):
        await down.verify_payment("1")
    await down.close()


async def test_crypto_pay_verify_payment_requires_single_match():
    def handler(request):
        items = [{"invoice_id": 5}, {"invoice_id": 6}]
        return httpx.Response(200, json={"ok": True, "result": {"items": items}})

    adapter = CryptoPayProvider("tok", transport=transport(handler))
    assert (await adapter.verify_payment("5"))["invoice_id"] == 5
    with pytest.raises(PaymentError, match="not found"):
        await adapter.verify_payment("9")


# ---------- FreeKassa ----------


def fk():
    return FreeKassaProvider("123", "s1", "s2")


def fk_event(amount="199.00", order="order-1", **overrides):
    fields = {
        "MERCHANT_ID": "123",
        "AMOUNT": amount,
        "MERCHANT_ORDER_ID": order,
        "intid": "555",
        "CUR_ID": "42",
        "SIGN": digest(f"123:{amount}:s2:{order}"),
    }
    fields.update(overrides)
    return fields


@pytest.mark.parametrize(
    "args", [("abc", "s1", "s2"), ("123", "", "s2"), ("123", "s1", ""), ("123", "s", "s")]
)
def test_freekassa_rejects_bad_settings(args):
    with pytest.raises(PaymentError):
        FreeKassaProvider(*args)


def test_freekassa_checkout_url_is_signed():
    url = fk().checkout_url("order-1", 19900, "RUB")
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith("https://pay.fk.money/?")
    assert query["oa"] == "199.00" and query["o"] == "order-1" and query["i"] == "42"
    assert query["s"] == digest("123:199.00:s1:RUB:order-1")


@pytest.mark.parametrize(
    "amount, currency, method", [(0, "RUB", "42"), (100, "USD", "42"), (100, "RUB", "36")]
)
def test_freekassa_checkout_url_validation(amount, currency, method):
    with pytest.raises(PaymentError):
        fk().checkout_url("o", amount, currency, method)


def test_freekassa_verifies_signed_event():
    assert fk().verify_event(fk_event()) == ("order-1", "555", 19900)
    upper = fk_event()
    upper["SIGN"] = upper["SIGN"].upper()
    assert fk().verify_event(upper)[2] == 19900


@pytest.mark.parametrize(
    "fields",
    [
        {k: v for k, v in fk_event().items() if k != "SIGN"},
        fk_event(MERCHANT_ID="999"),
        fk_event(amount="1e3"),
        fk_event(order="bad order!"),
        fk_event(intid="abc"),
        fk_event(CUR_ID="1"),
        fk_event(SIGN="0" * 32),
        fk_event(amount="0.00"),
    ],
)
def test_freekassa_rejects_bad_events(fields):
    with pytest.raises(PaymentError):
        fk().verify_event(fields)


# ---------- WATA ----------


def wata_row(**overrides):
    row = {
        "id": "tx-1",
        "kind": "Payment",
        "status": "Paid",
        "orderId": "pay-1",
        "amount": 199.0,
        "currency": "RUB",
        "terminalPublicId": "term",
        "paymentLinkId": "link-1",
    }
    row.update(overrides)
    return row


def test_wata_requires_settings():
    with pytest.raises(PaymentError):
        WATAProvider("", "term")


async def test_wata_create_payment_payload():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "link-1", "url": "https://pay"})

    adapter = WATAProvider("tok", "term", transport(handler))
    assert (await adapter.create_payment("pay-1", 19900, "https://back"))["id"] == "link-1"
    assert seen["orderId"] == "pay-1" and seen["amount"] == 199.0
    assert seen["isArbitraryAmountAllowed"] is False


def test_wata_matches_checks_every_identity_field():
    adapter = WATAProvider("tok", "term")
    assert adapter.matches(wata_row(), "pay-1", 19900, "link-1")
    assert adapter.matches(wata_row(), "pay-1", 19900)
    assert not adapter.matches(wata_row(amount=1), "pay-1", 19900)
    assert not adapter.matches(wata_row(terminalPublicId="x"), "pay-1", 19900)
    assert not adapter.matches(wata_row(paymentLinkId="x"), "pay-1", 19900, "link-1")
    assert not adapter.matches(wata_row(amount="abc"), "pay-1", 19900)


async def test_wata_paid_transaction_confirms_by_detail_lookup():
    def handler(request):
        if request.url.path.endswith("/v2/transactions/"):
            items = [wata_row(kind="Refund"), wata_row(amount=5), wata_row()]
            return httpx.Response(200, json={"items": items, "hasNextPage": False})
        return httpx.Response(200, json=wata_row())

    adapter = WATAProvider("tok", "term", transport(handler))
    assert (await adapter.paid_transaction("pay-1", 19900, "link-1"))["id"] == "tx-1"


async def test_wata_paid_transaction_pages_and_gives_up():
    pages = []

    def handler(request):
        if request.url.path.endswith("/v2/transactions/"):
            pages.append(request.url.params.get("cursorId"))
            if len(pages) == 1:
                return httpx.Response(
                    200,
                    json={
                        "items": [wata_row()],
                        "hasNextPage": True,
                        "nextCursorId": "c2",
                        "nextCursorDate": "d2",
                    },
                )
            return httpx.Response(200, json={"items": [], "hasNextPage": False})
        # Detail lookup disagrees with the list, so it must not count as paid.
        return httpx.Response(200, json=wata_row(status="Refunded"))

    adapter = WATAProvider("tok", "term", transport(handler))
    assert await adapter.paid_transaction("pay-1", 19900, "link-1") is None
    assert pages == [None, "c2"]


async def test_wata_paid_transaction_stops_after_ten_pages():
    def handler(request):
        return httpx.Response(
            200, json={"items": [], "hasNextPage": True, "nextCursorId": "c", "nextCursorDate": "d"}
        )

    adapter = WATAProvider("tok", "term", transport(handler))
    assert await adapter.paid_transaction("pay-1", 19900, "link-1") is None
    await adapter.close()


async def test_wata_hides_provider_errors():
    adapter = WATAProvider("tok", "term", transport(lambda r: httpx.Response(500)))
    with pytest.raises(PaymentError):
        await adapter.create_payment("pay-1", 100, "https://back")


# ---------- Telegram Stars ----------


class FakeBot:
    def __init__(self):
        self.calls = []

    async def create_invoice_link(self, **kwargs):
        self.calls.append(("invoice", kwargs))
        return "https://t.me/invoice/x"

    async def refund_star_payment(self, **kwargs):
        self.calls.append(("refund", kwargs))


async def test_stars_invoice_uses_payment_id_payload_and_language():
    bot = FakeBot()
    url = await TelegramStarsProvider(bot).create_payment("pay-1", 50, "XTR", lang="en")
    assert url == "https://t.me/invoice/x"
    kwargs = bot.calls[0][1]
    assert kwargs["payload"] == "pay-1" and kwargs["currency"] == "XTR"
    assert kwargs["prices"][0].amount == 50 and kwargs["prices"][0].label == "AERA subscription"


@pytest.mark.parametrize("amount, currency", [(0, "XTR"), (10, "RUB")])
async def test_stars_invoice_validation(amount, currency):
    with pytest.raises(PaymentError):
        await TelegramStarsProvider(FakeBot()).create_payment("p", amount, currency)


async def test_stars_never_trusts_client_or_webhook_verification():
    provider = TelegramStarsProvider(FakeBot())
    with pytest.raises(PaymentError):
        await provider.verify_payment("x")
    with pytest.raises(PaymentError):
        await provider.process_webhook({}, "s")


async def test_stars_refund():
    bot = FakeBot()
    await TelegramStarsProvider(bot).refund("charge-1", 77)
    assert bot.calls == [("refund", {"user_id": 77, "telegram_payment_charge_id": "charge-1"})]


# ---------- Local mocks ----------


async def test_mock_payment_provider():
    provider = MockPaymentProvider("secret")
    assert await provider.create_payment("p", 1, "RUB") == "p"
    assert await provider.verify_payment("p") is True
    assert await provider.process_webhook({"payment_id": 5}, "secret") == "5"
    with pytest.raises(PaymentError):
        await provider.process_webhook({"payment_id": 5}, "wrong")
    assert await provider.refund("p") is None


def test_mock_crypto_webhook_verification():
    provider = MockCryptoProvider(None, "secret")
    fresh = json.dumps({"invoice_id": "inv", "request_date": datetime.now(UTC).isoformat()})
    raw = fresh.encode()
    assert provider.verify_webhook(raw, provider.sign(raw)) == "inv"
    with pytest.raises(PaymentError, match="signature"):
        provider.verify_webhook(raw, "bad")
    stale = json.dumps(
        {"invoice_id": "inv", "request_date": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    ).encode()
    with pytest.raises(PaymentError, match="Invalid crypto event"):
        provider.verify_webhook(stale, provider.sign(stale))


async def test_mock_crypto_get_invoice(sessions):
    from app.db.models import CryptoMockReceipt

    async with sessions.begin() as db:
        db.add(
            CryptoMockReceipt(
                provider_invoice_id="inv",
                asset="TEST_USDT",
                network="TESTNET",
                status="PAID",
                received_amount="1.5",
            )
        )
    provider = MockCryptoProvider(sessions, "secret")
    receipt = await provider.get_invoice("inv")
    assert receipt.status == "PAID" and receipt.received_amount == Decimal("1.5")
    with pytest.raises(PaymentError):
        await provider.get_invoice("missing")
