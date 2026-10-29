"""Replace one known trial identity using the versioned 3.2.8 client API."""

import json
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import UUID

from app.core.trial_policy import DURATION_MS, SUPPORTED_DURATION_MS, TRIAL_IP_LIMIT
from app.integrations.xui.exceptions import XUIError
from app.integrations.xui.v328 import XUI328Client


def replacement_email(identity):
    # Xray reports statistics by email. A UUID-only replacement inherits the
    # old email's zero-byte reports, which can start the first-use timer.
    return f"aera-trial-{identity}"


class TrialStarted(XUIError):
    pass


class TrialKeyClient(XUI328Client):
    def __init__(self, base_url, token, inbound_id, transport=None):
        super().__init__(base_url, token, inbound_id, transport, allow_writes=True)
        self.rotation_route = None

    async def request(self, method, route, payload=None):
        if method != "GET" and (method != "POST" or route != self.rotation_route):
            raise XUIError("Only the selected trial key may be changed")
        return await super().request(method, route, payload)

    async def inspect_trial(self, old_uuid, new_uuid, email, new_email=None):
        emails = {email, new_email} if new_email else {email}
        inbound = await self.get_inbound(self.inbound_id)
        if inbound.get("protocol") != "vless" or inbound.get("nodeId") not in {None, 0}:
            raise XUIError("Only a local VLESS trial is supported")
        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)
        matches = [
            c
            for c in settings.get("clients", [])
            if c.get("email") in emails or c.get("id") in {old_uuid, new_uuid}
        ]
        if len(matches) != 1 or matches[0].get("email") not in emails:
            raise XUIError("Trial identity is ambiguous")
        current = matches[0]
        current_email = current["email"]
        try:
            record = await self.request(
                "GET", "panel/api/clients/get/" + quote(current_email, safe="")
            )
        except XUIError:
            # 3.2.8 renames the global row before updating the inbound. Recover
            # only our persisted replacement identity if that request stopped.
            if not new_email or current_email == new_email:
                raise
            record = await self.request("GET", "panel/api/clients/get/" + quote(new_email, safe=""))
        if not isinstance(record, dict) or record.get("inboundIds") != [self.inbound_id]:
            raise XUIError("Shared trial client cannot be changed")
        global_client = record.get("client", {})
        identity = global_client.get("uuid")
        if (
            identity not in {old_uuid, new_uuid}
            or current.get("id") != identity
            or global_client.get("email") not in emails
        ):
            raise XUIError("Trial UUID does not match")
        for c in (current, global_client):
            if (
                int(c.get("totalGB", -1)) != 0
                or int(c.get("limitIp", -1)) != TRIAL_IP_LIMIT
                or int(c.get("reset", 0)) != 0
            ):
                raise XUIError("Trial limits or renewal policy changed")
        traffic = await self.request(
            "GET", "panel/api/clients/traffic/" + quote(current_email, safe="")
        )
        if not isinstance(traffic, dict) or traffic.get("email") != current_email:
            raise XUIError("Trial traffic unavailable")
        return current, global_client, traffic

    async def disable_converted_trial(self, old_uuid, new_uuid, email):
        new_email = replacement_email(new_uuid)
        current, record, traffic = await self.inspect_trial(old_uuid, new_uuid, email, new_email)
        if record["uuid"] == new_uuid:
            return  # Accepted-but-lost rotation: verify it, never reset a new key twice.
        if current.get("enable") or record.get("enable") or traffic.get("enable", True):
            payload = {k: v for k, v in record.items() if k not in {"id", "uuid", "createdAt", "updatedAt"}}
            payload.update(id=old_uuid, enable=False)
            if record.get("createdAt"):
                payload["created_at"] = record["createdAt"]
            self.rotation_route = "panel/api/clients/update/" + quote(record["email"], safe="") + f"?inboundIds={self.inbound_id}"
            try:
                await self.request("POST", self.rotation_route, payload)
            finally:
                self.rotation_route = None
            current, record, traffic = await self.inspect_trial(old_uuid, new_uuid, email, new_email)
        if any(c.get("enable", True) for c in (current, record, traffic)):
            raise XUIError("Converted trial disable unverified")

    async def replace_trial_key(self, old_uuid, new_uuid, email, reason, issued_at, baseline_bytes):
        if old_uuid == new_uuid or any(str(UUID(i)) != i for i in (old_uuid, new_uuid)):
            raise XUIError("Invalid replacement identity")
        new_email = replacement_email(new_uuid)
        current, record, traffic = await self.inspect_trial(old_uuid, new_uuid, email, new_email)
        total = int(traffic.get("up", -1)) + int(traffic.get("down", -1))
        if total < 0:
            raise XUIError("Missing trial traffic counters")
        if record["uuid"] == old_uuid:
            expiry = int(traffic.get("expiryTime") or record.get("expiryTime", 0))
            if reason in {"UNUSED", "IDLE"}:
                if (
                    expiry <= 0 and -expiry not in SUPPORTED_DURATION_MS
                ) or total != baseline_bytes:
                    raise TrialStarted("Trial was used before rotation")
                if reason == "IDLE" and expiry <= 0:
                    raise XUIError("Unused stock has no premature deadline")
                if int(traffic.get("lastOnline", 0)) > int(issued_at.timestamp() * 1000):
                    raise TrialStarted("Trial was used before rotation")
            elif reason == "PURCHASED":
                if current.get("enable") or record.get("enable") or traffic.get("enable", True):
                    raise XUIError("Purchased trial was not disabled before rotation")
            elif reason == "EXPIRED":
                if expiry <= 0 or expiry > int(datetime.now(UTC).timestamp() * 1000):
                    raise TrialStarted("Trial expiry changed before rotation")
            else:
                raise XUIError("Unsupported trial rotation")
        elif record.get("subId") != new_uuid.replace("-", ""):
            raise XUIError("Pending replacement does not match")
        legacy_pending = (
            record["uuid"] == new_uuid
            and -int(record.get("expiryTime", 0)) in SUPPORTED_DURATION_MS
            and int(record.get("expiryTime", 0)) != -DURATION_MS
        )
        if legacy_pending and (
            int(current.get("expiryTime", 0)) != int(record.get("expiryTime", 0))
            or int(traffic.get("expiryTime", 0)) != int(record.get("expiryTime", 0))
            or total != baseline_bytes
            or int(traffic.get("lastOnline", 0)) > int(issued_at.timestamp() * 1000)
        ):
            raise TrialStarted("Pending trial was used before upgrade")
        if (
            record["uuid"] == old_uuid
            or record.get("email") != new_email
            or current.get("email") != new_email
            or legacy_pending
        ):
            # The global record contains a numeric database id and a separate UUID.
            # The update API instead accepts a Client with its UUID in id.
            payload = {
                k: v for k, v in record.items() if k not in {"id", "uuid", "createdAt", "updatedAt"}
            }
            payload.update(
                id=new_uuid,
                email=new_email,
                subId=new_uuid.replace("-", ""),
                expiryTime=-DURATION_MS,
                enable=True,
            )
            if record.get("createdAt"):
                payload["created_at"] = record["createdAt"]
            self.rotation_route = (
                "panel/api/clients/update/"
                + quote(record["email"], safe="")
                + f"?inboundIds={self.inbound_id}"
            )
            try:
                await self.request("POST", self.rotation_route, payload)
            finally:
                self.rotation_route = None
            current, record, traffic = await self.inspect_trial(
                old_uuid, new_uuid, email, new_email
            )
        expiry = int(traffic.get("expiryTime", 0))
        if (
            record.get("uuid") != new_uuid
            or record.get("email") != new_email
            or current.get("email") != new_email
            or record.get("subId") != new_uuid.replace("-", "")
            or current.get("subId") != new_uuid.replace("-", "")
            or not current.get("enable")
            or not record.get("enable")
            or not traffic.get("enable", True)
            or int(current.get("expiryTime", 0)) != expiry
            or int(record.get("expiryTime", 0)) != expiry
            or expiry != -DURATION_MS
            or int(traffic.get("up", -1)) + int(traffic.get("down", -1)) != total
        ):
            raise XUIError("New trial key not confirmed")
        return int(traffic["up"]) + int(traffic["down"])

    async def confirm_trial_deadline(self, identity, email, deadline, issued_at, baseline_bytes):
        current, record, traffic = await self.inspect_trial(identity, identity, email)
        if int(traffic.get("up", 0)) + int(traffic.get("down", 0)) <= baseline_bytes and int(
            traffic.get("lastOnline", 0)
        ) <= int(issued_at.timestamp() * 1000):
            raise XUIError("First trial use is not confirmed")
        if int(record.get("expiryTime", 0)) != deadline:
            payload = {
                k: v for k, v in record.items() if k not in {"id", "uuid", "createdAt", "updatedAt"}
            }
            payload.update(id=identity, expiryTime=deadline, enable=True)
            if record.get("createdAt"):
                payload["created_at"] = record["createdAt"]
            self.rotation_route = (
                "panel/api/clients/update/"
                + quote(email, safe="")
                + f"?inboundIds={self.inbound_id}"
            )
            try:
                await self.request("POST", self.rotation_route, payload)
            finally:
                self.rotation_route = None
            current, record, traffic = await self.inspect_trial(identity, identity, email)
        if not all(
            int(c.get("expiryTime", 0)) == deadline and c.get("enable", True)
            for c in (current, record, traffic)
        ):
            raise XUIError("Actual-use deadline not confirmed")
