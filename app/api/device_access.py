"""Loopback-only authenticated bridge from read-only subscription frontend."""
import hashlib
import hmac
import json
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.config import get_settings
from app.db.session import sessions
from app.services.device_access import get_configuration

router = APIRouter()


class DeviceRequest(BaseModel):
    identity: str = Field(min_length=36, max_length=36)
    hwid: str = Field(max_length=128)
    platform: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=64)
    timestamp: int


@router.post("/internal/device-configuration")
async def configuration(data: DeviceRequest, request: Request):
    settings = get_settings()
    raw = await request.body()
    expected = hmac.new(settings.app_secret.encode(), b"aera-device-bridge-v1:" + raw, hashlib.sha256).hexdigest()
    if (not request.client or request.client.host not in {"127.0.0.1", "::1"}
            or abs(time.time() - data.timestamp) > 60
            or not hmac.compare_digest(request.headers.get("x-aera-device-auth", ""), expected)):
        raise HTTPException(404, "Not found")
    try:
        return await get_configuration(sessions, settings, data.identity, data.hwid, data.platform, data.model)
    except ValueError as error:
        raise HTTPException(403, str(error), headers={"subscription-always-hwid-enable": "1"}) from None
    except Exception:
        raise HTTPException(503, "Устройство подготавливается. Повторите обновление подписки.",
                            headers={"Retry-After": "15"}) from None
