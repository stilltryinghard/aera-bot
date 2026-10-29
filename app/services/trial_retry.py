"""Fail closed before resetting an unused HWID trial's eligibility."""
import asyncio
from datetime import UTC, datetime, timedelta
from sqlalchemy import select
from app.db.models import TrialLink, DeviceBinding, TrialUsage, User
from app.services.commerce import utc


async def prepare_unused_retry(sessions, settings, config, identity):
    async with sessions() as db:
        row = await db.get(TrialLink, identity)
        if not row or row.pending_uuid or row.status not in {"WAITING", "BLOCKED"}:
            return True  # Existing recovery/expired-stock paths keep their semantics.
        if row.started_at or row.activation_deadline_ms or not row.user_id or not row.issued_at:
            return True
        if utc(row.issued_at) > datetime.now(UTC) - timedelta(hours=1):
            return True
        owner, parent, issued = row.user_id, row.client_uuid, row.issued_at
        user = await db.get(User, owner)
        from app.services.trial_pool import fingerprint
        usage = await db.get(TrialUsage, fingerprint(user.telegram_id, settings.app_secret))
        if usage and usage.activated_at:
            return False
        bindings = list(await db.scalars(select(DeviceBinding).where(
            DeviceBinding.trial_link_id == identity, DeviceBinding.parent_uuid == parent)))
    if not bindings:
        return True  # The primary rotation itself verifies original traffic.
    from app.services.device_access import provision
    for binding in bindings:
        if binding.status != "REVOKED":
            await provision(sessions, settings, binding.id)
    async with sessions() as db:
        row = await db.get(TrialLink, identity)
        if row.client_uuid != parent or row.user_id != owner or row.started_at or row.activation_deadline_ms:
            return False
    from app.integrations.xui.v328 import XUI328Client
    from scripts.panel_sync import bridge, NODES, mirror_uuid
    reader = XUI328Client(config["base_url"], config["api_token"], config["inbound_id"], allow_writes=False)
    try:
        inbound = await reader.get_inbound(reader.inbound_id)
        snapshots = await asyncio.gather(*(bridge({"version": 1, "clients": [], "inspect_only": True}, n) for n in NODES))
    finally:
        await reader.close()
    import json
    clients = inbound.get("settings", {})
    clients = json.loads(clients) if isinstance(clients, str) else clients
    clients = {c["id"]: c for c in clients.get("clients", [])}
    stats = {r["email"]: r for r in inbound.get("clientStats", [])}
    for binding in bindings:
        client = clients.get(binding.client_uuid)
        primary = stats.get(client["email"], {}) if client else {}
        states = [reply.get("states", {}).get(mirror_uuid(n, binding.client_uuid), {})
                  for n, reply in zip(NODES, snapshots)]
        # Any usage or unavailable counters require reconciliation/support;
        # they can never produce a fresh free period.
        if client and (client.get("enable") or not primary):
            return False
        if primary and (not {"up", "down", "lastOnline"}.issubset(primary)
                        or int(primary.get("up", 0)) + int(primary.get("down", 0)) > 0
                        or int(primary.get("lastOnline", 0)) > int(utc(issued).timestamp()*1000)):
            return False
        for reply, state in zip(snapshots, states):
            if "states" not in reply or (state and (not {"up", "down", "last_online"}.issubset(state)
                    or state.get("enable")
                    or int(state.get("up", 0)) + int(state.get("down", 0)) > 0
                    or int(state.get("last_online", 0)) > int(utc(issued).timestamp()*1000))):
                return False
    async with sessions.begin() as db:
        row = await db.get(TrialLink, identity)
        if row.client_uuid != parent or row.user_id != owner or row.started_at or row.activation_deadline_ms:
            return False
        for binding in bindings:
            current = await db.get(DeviceBinding, binding.id)
            if current.status not in {"EXPIRED", "REVOKED"} or current.bundle_encrypted:
                return False
        for binding in bindings:
            current = await db.get(DeviceBinding, binding.id)
            current.status = "REVOKED"
    return True
