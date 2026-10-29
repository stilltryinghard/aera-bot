"""Critical current-contract regressions. No provider calls or production database."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.security import TokenVault
from app.db.models import Payment, SupportTicket, User
from app.services.device_access import device_hash, normalize_hwid
from app.services.payment_timeout import elapsed
from app.services.platega import minor
from app.services.trial_upgrade import verified_order
from app.subscription.multinode import panel_identity, panel_state, panel_token

NOW = datetime(2026, 10, 26, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("seconds,expired", [(899, False), (900, True), (901, True)])
def test_checkout_boundary_uses_creation_time(seconds, expired):
    order = SimpleNamespace(paid_at=None, created_at=NOW - timedelta(days=1))
    payment = SimpleNamespace(
        paid_at=None, status="PENDING", created_at=NOW - timedelta(seconds=seconds), updated_at=NOW
    )
    assert elapsed(order, payment, NOW) is expired


@pytest.mark.parametrize("state", ["PAID", "CHARGEBACKED", "REFUNDED"])
def test_timeout_does_not_reopen_paid_or_reversed_receipt(state):
    order = SimpleNamespace(paid_at=None, created_at=NOW - timedelta(days=1))
    payment = SimpleNamespace(paid_at=None, status=state, created_at=order.created_at)
    assert not elapsed(order, payment, NOW)


@pytest.mark.parametrize(
    "amount", ["NaN", "Infinity", "-Infinity", "0.001", "100.001", None, {}, "bad"]
)
def test_provider_amount_rejects_nonfinite_or_fractional_minor_units(amount):
    with pytest.raises(ValueError):
        minor(amount)


@pytest.mark.parametrize("change", ["tamper", "wrong_secret", "different_identity"])
def test_public_panel_token_cannot_be_reused_for_another_identity(change):
    identity = str(uuid4())
    secret = "audit-only-secret"
    token = panel_token(identity, secret)
    assert panel_identity(token, secret) == identity
    if change == "tamper":
        token = token[:-1] + ("0" if token[-1] != "0" else "1")
    if change == "wrong_secret":
        secret = "different-audit-secret"
    if change == "different_identity":
        token = uuid4().hex + "." + token.split(".")[1]
    assert panel_identity(token, secret) is None


@pytest.mark.parametrize("age", [-1, 180, 181, 1000])
async def test_stale_or_future_panel_state_fails_closed(age):
    import json

    vault = TokenVault("audit-only-encryption-secret")
    row = SimpleNamespace(
        value=vault.cipher.encrypt(
            json.dumps({"checked_at": NOW.timestamp() - age, "entries": {}}).encode()
        ).decode()
    )
    db = SimpleNamespace(get=AsyncMock(return_value=row))
    assert await panel_state(db, vault, NOW) is None


def test_hwid_is_normalized_and_bound_to_parent():
    identity = str(uuid4())
    hwid = str(uuid4())
    assert normalize_hwid(hwid) == normalize_hwid(hwid.replace("-", "").upper())
    assert device_hash(identity, hwid, "test-secret") == device_hash(
        identity, hwid.replace("-", "").upper(), "test-secret"
    )
    assert device_hash(identity, hwid, "test-secret") != device_hash(
        str(uuid4()), hwid, "test-secret"
    )


@pytest.mark.parametrize(
    "violation", ["mock", "pending", "unpaid", "other_user", "other_plan", "blocked", "inactive"]
)
async def test_trial_to_paid_requires_verified_matching_receipt(violation):
    order = SimpleNamespace(payment_id="p", ticket_id="t", plan_id="plan", paid_at=NOW)
    payment = SimpleNamespace(
        status="PAID", paid_at=NOW, provider="platega", user_id="u", plan_id="plan"
    )
    ticket = SimpleNamespace(user_id="u")
    user = SimpleNamespace(id="u", is_active=True, is_blocked=False)
    if violation == "mock":
        payment.provider = "mock"
    if violation == "pending":
        payment.status = "PENDING"
    if violation == "unpaid":
        payment.paid_at = None
    if violation == "other_user":
        payment.user_id = "other"
    if violation == "other_plan":
        payment.plan_id = "other"
    if violation == "blocked":
        user.is_blocked = True
    if violation == "inactive":
        user.is_active = False
    rows = {Payment: payment, SupportTicket: ticket, User: user}
    db = SimpleNamespace(get=AsyncMock(side_effect=lambda model, key: rows[model]))
    assert await verified_order(db, order) is None
