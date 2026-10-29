"""Idempotent provisioning of NEW v2 stock only; never edits existing clients."""
import asyncio
import json
from uuid import uuid4

from sqlalchemy import select, func
from app.db.models import AppSetting, PaidLink, Plan, DeviceSubscription
from app.core.security import TokenVault
from app.integrations.xui.v328 import XUI328Client
from app.services.tariffs_v2 import TERMS, slug, price
from app.services.source_links import primary_link

_lock = asyncio.Lock()


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


def validate_plan(plan):
    if not plan.slug.startswith("devices-") or plan.duration_days not in TERMS or not 1 <= plan.device_limit <= 10:
        raise ValueError("Only new device tariffs can be provisioned")
    if plan.slug != slug(plan.duration_days, plan.device_limit) or plan.price_minor != price(plan.duration_days, plan.device_limit) * 100:
        raise ValueError("Tariff does not match approved catalog")


def stock_payload(row, plan):
    validate_plan(plan)
    return dict(id=row.client_uuid, email="aera-" + row.client_uuid, flow="xtls-rprx-vision",
                limitIp=plan.device_limit, totalGB=0, expiryTime=-plan.duration_days * 86400000,
                enable=True, reset=0, subId=row.client_uuid.replace("-", ""), comment=row.label)


async def replenish(sessions, settings, target=3):
    if not 1 <= target <= 5:
        raise ValueError("Invalid inventory target")
    async with _lock:
        vault = TokenVault(settings.app_secret)
        async with sessions() as db:
            enabled = await db.get(AppSetting, "v2_stock_enabled")
            if not enabled or enabled.value != "true":
                return 0
            observer = await db.get(AppSetting, "trial_observer")
            if not observer:
                raise ValueError("Verified primary observer unavailable")
            config = json.loads(vault.reveal(observer.value))
        writer = XUI328Client(config["base_url"], config["api_token"], config["inbound_id"], allow_writes=True)
        created = 0
        try:
            inbound = await writer.get_inbound(writer.inbound_id)
            if inbound.get("protocol") != "vless" or inbound.get("nodeId") not in {None, 0}:
                raise ValueError("Unsupported primary inbound")
            # Validate live REALITY settings before any inventory/client writes.
            primary_link(inbound, "00000000-0000-0000-0000-000000000001", "xtls-rprx-vision", "validation")
            clients = decode(inbound["settings"])["clients"]
            current = {c["id"]: c for c in clients}
            async with sessions.begin() as db:
                plans = list(await db.scalars(select(Plan).where(Plan.slug.startswith("devices-"), Plan.is_active.is_(True))))
                for plan in plans:
                    # Validate every selected catalog entry before persisting or writing clients.
                    validate_plan(plan)
                    count = await db.scalar(select(func.count()).select_from(PaidLink).where(
                        PaidLink.plan_id == plan.id, PaidLink.order_id.is_(None), PaidLink.issued_at.is_(None)))
                    for _ in range(max(0, target - count)):
                        identity = str(uuid4())
                        label = f"AERA V2 {plan.duration_days}d {plan.device_limit}dev {identity[:8]}"
                        raw = primary_link(inbound, identity, "xtls-rprx-vision", label)
                        from app.subscription.links import device_public_identity
                        db.add(DeviceSubscription(parent_uuid=identity, public_uuid=device_public_identity(identity, settings.app_secret)))
                        db.add(PaidLink(client_uuid=identity, label=label, plan_id=plan.id,
                                       duration_ms=plan.duration_days * 86400000, status="PROVISIONING",
                                       link_encrypted=vault.cipher.encrypt(raw.encode()).decode()))
                await db.flush()
                candidates = [(row.id, stock_payload(row, await db.get(Plan, row.plan_id)))
                              for row in await db.scalars(select(PaidLink).join(Plan).where(
                                  Plan.slug.startswith("devices-"), PaidLink.order_id.is_(None),
                                  PaidLink.issued_at.is_(None), PaidLink.status.in_(["PROVISIONING", "UNAVAILABLE", "UNVERIFIED"])))]
            for identity, desired in candidates:
                actual = current.get(desired["id"])
                if actual:
                    if any(actual.get(k) != desired[k] for k in ["email", "expiryTime", "limitIp", "totalGB", "enable"]):
                        raise ValueError("Existing stock differs; no mutation performed")
                    continue
                if any(c.get("email") == desired["email"] for c in current.values()):
                    raise ValueError("Stock email conflict")
                # Persisted UUID makes accepted-but-lost create responses recoverable.
                await writer.request("POST", "panel/api/clients/add", {"client": desired, "inboundIds": [writer.inbound_id]})
                current[desired["id"]] = desired
                created += 1
            return created
        finally:
            await writer.close()
