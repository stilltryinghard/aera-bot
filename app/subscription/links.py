"""Stable, authenticated subscription URLs; no client credentials in public code."""
import hashlib
import hmac
import os
import base64
import re
from uuid import UUID
from urllib.parse import urlsplit


def valid_short_token(token):
    return isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{21}[AQgw]", token) is not None


def short_token(kind, identity, secret):
    if kind not in {"user", "panel"}:
        raise ValueError("Invalid subscription kind")
    message = "aera-short-sub-v1:" + kind + ":" + UUID(identity).hex
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def subscription_token(user_id: str, secret: str) -> str:
    identity = UUID(user_id).hex
    signature = hmac.new(secret.encode(), ("aera-sub-v1:" + identity).encode(), hashlib.sha256).hexdigest()
    return identity + "." + signature


def subscription_user(token: str, secret: str) -> str | None:
    if len(token) != 97:
        return None
    identity, separator, signature = token.partition(".")
    try:
        user_id = str(UUID(hex=identity))
    except ValueError:
        return None
    expected = subscription_token(user_id, secret)
    return user_id if separator and hmac.compare_digest(token, expected) else None


def subscription_url(user_id: str, secret: str, origin: str) -> str:
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Subscription origin must be a bare HTTPS origin")
    return origin.rstrip("/") + "/s/" + short_token("user", user_id, secret)


def customer_url(user_id: str, settings) -> str:
    origin = getattr(settings, "aera_subscription_origin", "") or os.getenv("AERA_SUBSCRIPTION_ORIGIN", settings.public_base_url)
    return subscription_url(user_id, settings.app_secret, origin)


def connect_url(url: str) -> str:
    return url.replace("/s/", "/c/", 1) if "/s/" in urlsplit(url).path else url.replace("/sub/", "/connect/", 1)


def panel_url(identity: str, settings) -> str:
    from app.subscription.multinode import panel_token
    origin = getattr(settings, "aera_subscription_origin", "") or os.getenv("AERA_SUBSCRIPTION_ORIGIN", settings.public_base_url)
    # Reuse strict origin validation; only the token namespace differs.
    subscription_url(identity, settings.app_secret, origin)
    return origin.rstrip("/") + "/s/" + short_token("panel", identity, settings.app_secret)


def device_public_identity(identity, secret):
    # A public subscription URL must never reveal the master VPN UUID.
    digest = hmac.new(secret.encode(), ("aera-device-public-v1:" + str(UUID(identity))).encode(), hashlib.sha256).digest()
    return str(UUID(bytes=digest[:16]))
