import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select, text

from app.api.admin import router as admin_router
from app.api.checkout_api import router as checkout_api_router
from app.api.crypto import router as crypto_router
from app.api.external_payments import router as external_payments_router
from app.api.freekassa import router as freekassa_router
from app.config import get_settings, redis_options
from app.core.exceptions import AERAError
from app.core.logging import log_error
from app.core.security import TokenVault, token_hash
from app.db.models import Payment, ProvisioningJob, Server, Subscription, User, VPNClient
from app.db.session import engine, sessions
from app.integrations.payments.mock import MockPaymentProvider
from app.integrations.xui.factory import create_adapter
from app.services.commerce import CommerceService, utc
from app.services.provisioning import ProvisioningService

settings = get_settings()
vault = TokenVault(settings.app_secret)
redis = Redis.from_url(settings.redis_url, **redis_options(settings))
adapter = create_adapter(sessions, settings)
provisioner = ProvisioningService(sessions, adapter, vault)
logger = logging.getLogger("aera")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if hasattr(app.state, "bot"):
        await app.state.bot.session.close()
        await app.state.dispatcher.storage.close()
    if hasattr(adapter, "close"):
        await adapter.close()
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title="AERA VPN", version="0.1.0", lifespan=lifespan)
app.include_router(admin_router)
app.include_router(crypto_router)
app.include_router(external_payments_router)
# Card checkout is not configured; expose only informational return pages.
# FreeKassa webhook enabled; signature and source-IP validation required.
app.include_router(freekassa_router)
app.include_router(checkout_api_router)


@app.exception_handler(AERAError)
async def business_error(request: Request, error: AERAError):
    from fastapi.responses import JSONResponse

    request_id = log_error("business_error", error, operation=request.method)
    return JSONResponse(
        status_code=400,
        content={"detail": "Не удалось выполнить действие.", "request_id": request_id},
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, error: Exception):
    from fastapi.responses import JSONResponse

    request_id = log_error("unexpected_error", error, operation=request.method)
    return JSONResponse(
        status_code=500, content={"detail": "Попробуйте ещё раз позже.", "request_id": request_id}
    )


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    # Paths can contain subscription secrets: never log raw paths or request bodies.
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        json.dumps(
            {
                "event": "http_request",
                "request_id": request_id,
                "operation": request.method,
                "status": response.status_code,
                "duration": round(time.monotonic() - started, 4),
            }
        )
    )
    return response


@app.get("/health")
@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok", "mode": "mock" if settings.xui_mock_mode else "real"}


@app.get("/ready")
async def ready() -> dict:
    try:
        async with sessions() as db:
            await db.execute(text("SELECT 1"))
        await redis.ping()
    except Exception:
        raise HTTPException(503, "Dependencies unavailable") from None
    return {"database": "ok", "redis": "ok"}


@app.get("/sub/{token}")
async def subscription(token: str, request: Request) -> PlainTextResponse:
    if len(token) > 128:
        raise HTTPException(404, "Access unavailable")
    # Count by IP, not by secret. Redis errors fail closed on public endpoints.
    peer = request.client.host if request.client else "unknown"
    key = "sub-rate:" + token_hash(peer) + ":" + str(int(time.time()) // 60)
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 120)
        if count > 60:
            raise HTTPException(429, "Try again later")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Temporarily unavailable") from None
    async with sessions() as db:
        client = await db.scalar(
            select(VPNClient).where(VPNClient.subscription_token_hash == token_hash(token))
        )
        if not client or not client.enabled:
            raise HTTPException(404, "Access unavailable")
        sub = await db.get(Subscription, client.subscription_id)
        user = await db.get(User, sub.user_id)
        if (
            sub.status not in {"ACTIVE", "EXPIRING", "TRIAL"}
            or utc(sub.expires_at) <= datetime.now(UTC)
            or user.is_blocked
            or not user.is_active
            or (
                client.traffic_limit_bytes > 0
                and client.traffic_used_bytes >= client.traffic_limit_bytes
            )
        ):
            raise HTTPException(404, "Access unavailable")
        from app.subscription.renderer import render

        server = await db.get(Server, client.server_id)
        body = (
            "# AERA MOCK: test subscription; no real VPN configuration\n"
            if settings.xui_mock_mode
            else render(client.uuid, server.name, server.public_config)
        )
        return PlainTextResponse(
            body,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Subscription-Userinfo": f"upload=0; download={client.traffic_used_bytes}; "
                f"total={client.traffic_limit_bytes}; "
                f"expire={int(utc(sub.expires_at).timestamp())}",
            },
        )


class Confirmation(BaseModel):
    payment_id: str


@app.post("/api/v1/payments/mock/confirm")
async def confirm(payload: Confirmation, x_mock_secret: str = Header(default="")) -> dict:
    if settings.app_env == "production" or settings.payment_provider != "mock":
        raise HTTPException(404)
    provider = MockPaymentProvider(settings.mock_payment_secret)
    try:
        payment_id = await provider.process_webhook(payload.model_dump(), x_mock_secret)
    except Exception:
        raise HTTPException(403, "Forbidden") from None
    async with sessions.begin() as db:
        payment = await db.get(Payment, payment_id)
        if not payment or payment.provider not in {"mock", "stars_mock"}:
            raise HTTPException(404)
        sub = await CommerceService(db, vault).confirm(payment_id)
        await db.flush()
        job_id = await db.scalar(
            select(ProvisioningJob.id).where(ProvisioningJob.payment_id == payment_id)
        )
    success = await provisioner.run(job_id)
    return {"subscription_id": sub.id, "status": "ACTIVE" if success else "PENDING_PROVISIONING"}


@app.post("/api/v1/telegram/webhook")
async def telegram_webhook(
    request: Request, x_telegram_bot_api_secret_token: str = Header(default="")
) -> dict:
    if (
        not settings.bot_token
        or not settings.telegram_webhook_secret
        or not hmac.compare_digest(
            x_telegram_bot_api_secret_token, settings.telegram_webhook_secret
        )
    ):
        raise HTTPException(403)
    from aiogram.types import Update

    from app.bot.client import create_bot
    from app.bot.runtime import create_dispatcher

    if not hasattr(request.app.state, "dispatcher"):
        request.app.state.dispatcher = create_dispatcher()
        request.app.state.bot = create_bot(settings)
    update = Update.model_validate(await request.json())
    await request.app.state.dispatcher.feed_update(request.app.state.bot, update)
    return {"ok": True}
