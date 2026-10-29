"""Align a purchased key's first-use deadline when Finland is used first.

Only an issued, still waiting paid record is eligible. Persist the exact deadline
before the scoped primary update so timeouts cannot grant another period.
"""

import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import quote
from app.db.models import AppSetting, PaidLink, User
from app.integrations.xui.v328 import XUI328Client
from app.services.commerce import utc
from app.services.renewals import read_client, fingerprint

PREFIX = "paid-first-use.v1:"


def first_deadline(row, client, remote, now):
    if not row.issued_at or row.started_at or row.expires_at or row.status != "WAITING":
        return None
    duration = int(row.duration_ms or 0)
    if (
        duration <= 0
        or (int(client.get("expiryTime", 0)) != -duration and int(client.get("expiryTime", 0)) <= 0)
        or not client.get("enable")
    ):
        return None
    deadline = int(remote.get("expiry_ms", 0))
    used = int(remote.get("up", 0)) + int(remote.get("down", 0)) > 0
    used = used or int(remote.get("last_online", 0)) >= int(utc(row.issued_at).timestamp() * 1000)
    # Use the first observed actual traffic, not a possible zero-byte activation.
    observed = int(remote.get("last_online", 0))
    start = max(deadline - duration, observed)
    deadline = start + duration
    if not remote.get("enable") or not used or deadline <= int(now.timestamp() * 1000):
        return None
    if (
        start < int(utc(row.issued_at).timestamp() * 1000) - 1000
        or start > int(now.timestamp() * 1000) + 5000
    ):
        return None
    return deadline


class FirstUseClient(XUI328Client):
    route = None

    async def request(self, method, route, payload=None):
        if method != "GET" and (method != "POST" or route != self.route):
            raise ValueError("Only the selected paid first-use deadline may be changed")
        return await super().request(method, route, payload)


async def align(sessions, config, record_id, source_client, remote, now):
    async with sessions.begin() as db:
        row = await db.get(PaidLink, record_id)
        user = await db.get(User, row.user_id) if row and row.user_id else None
        if not user or not user.is_active or user.is_blocked or not row.issued_at:
            return False
        stored = await db.get(AppSetting, PREFIX + row.id)
        if stored:
            intent = json.loads(stored.value)
            if intent.get("done"):
                return False
        else:
            deadline = first_deadline(row, source_client, remote, now)
            if deadline is None:
                return False
            intent = {
                "deadline": deadline,
                "uuid": row.client_uuid,
                "email": source_client["email"],
                "issued": utc(row.issued_at).isoformat(),
                "owner": row.user_id,
                "primary_expiry": int(source_client.get("expiryTime", 0)),
                "fingerprint": hashlib.sha256(
                    json.dumps(fingerprint(source_client), sort_keys=True).encode()
                ).hexdigest(),
            }
            db.add(AppSetting(key=PREFIX + row.id, value=json.dumps(intent)))
        if (
            intent["uuid"] != row.client_uuid
            or intent["owner"] != row.user_id
            or intent["issued"] != utc(row.issued_at).isoformat()
        ):
            raise ValueError("First-use ownership changed")
    reader = FirstUseClient(
        config["base_url"], config["api_token"], config["inbound_id"], allow_writes=True
    )
    try:
        client = await read_client(reader, intent["uuid"])
        digest = hashlib.sha256(
            json.dumps(fingerprint(client), sort_keys=True).encode()
        ).hexdigest()
        if (
            digest != intent["fingerprint"]
            or client["email"] != intent["email"]
            or not client.get("enable")
        ):
            raise ValueError("First-use client conditions changed")
        deadline = intent["deadline"]
        actual = int(client.get("expiryTime", 0))
        if actual != deadline:
            # A simultaneously started NL term may differ only by one polling
            # interval. Never override an already renewed or otherwise edited term.
            if (
                actual > 0
                and actual != intent["primary_expiry"]
                and abs(actual - deadline) > 120000
            ):
                raise ValueError("Primary deadline changed independently")
            record = await reader.request(
                "GET", "panel/api/clients/get/" + quote(client["email"], safe="")
            )
            global_client = record["client"]
            payload = {
                k: v
                for k, v in global_client.items()
                if k not in {"id", "uuid", "createdAt", "updatedAt"}
            }
            payload.update(id=intent["uuid"], expiryTime=deadline)
            if global_client.get("createdAt"):
                payload["created_at"] = global_client["createdAt"]
            reader.route = (
                "panel/api/clients/update/"
                + quote(client["email"], safe="")
                + f"?inboundIds={reader.inbound_id}"
            )
            await reader.request("POST", reader.route, payload)
            reader.route = None
            verified = await read_client(reader, intent["uuid"])
            if int(verified["expiryTime"]) != deadline or fingerprint(verified) != fingerprint(
                client
            ):
                raise ValueError("Primary first-use update was not verified")
    finally:
        await reader.close()
    async with sessions.begin() as db:
        row = await db.get(PaidLink, record_id)
        if (
            row.user_id != intent["owner"]
            or row.client_uuid != intent["uuid"]
            or utc(row.issued_at).isoformat() != intent["issued"]
        ):
            raise ValueError("Paid assignment changed during activation")
        row.expires_at = datetime.fromtimestamp(intent["deadline"] / 1000, UTC)
        row.started_at = datetime.fromtimestamp((intent["deadline"] - row.duration_ms) / 1000, UTC)
        row.status = "ACTIVE"
        row.checked_at = datetime.now(UTC)
        stored = await db.get(AppSetting, PREFIX + row.id)
        stored.value = json.dumps({**intent, "done": True})
    return True
