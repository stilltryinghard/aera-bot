"""HWID admission and separate per-device credentials on the four owned nodes."""
import asyncio
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, parse_qsl, urlencode, quote, urlsplit, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy import select, update, func
from app.core.security import TokenVault
from app.core.trial_policy import DURATION_MS, TRIAL_IP_LIMIT
from app.db.models import AppSetting, DeviceBinding, PaidLink, Plan, TrialLink, User
from app.integrations.xui.v328 import XUI328Client
from app.services.commerce import utc
from app.services.renewals import read_client
from app.services.source_links import primary_link as build_primary_link

_provision_lock = asyncio.Lock()


def canonical_device_link(raw, flow):
    """The verified server flow is authoritative for the emitted credential."""
    parsed = urlsplit(raw)
    if parsed.scheme != "vless":
        raise ValueError("Invalid device source")
    fields = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "flow"]
    if flow:
        fields.append(("flow", flow))
    return urlunsplit(parsed._replace(query=urlencode(fields)))
logger = logging.getLogger(__name__)


def normalize_hwid(value):
    if not isinstance(value, str) or not 16 <= len(value.strip()) <= 128:
        raise ValueError("Включите отправку HWID в настройках приложения и обновите подписку.")
    value = value.strip()
    # UUID and hex representations normalize together; other real client formats remain opaque.
    try:
        return UUID(value).hex
    except ValueError:
        if any(ord(c) < 33 or ord(c) > 126 for c in value):
            raise ValueError("Invalid HWID") from None
        return value


def device_hash(parent_uuid, hwid, secret):
    return hmac.new(secret.encode(), ("aera-hwid-v1:" + parent_uuid + ":" + normalize_hwid(hwid)).encode(), hashlib.sha256).hexdigest()


async def parent_access(db, identity, settings):
    parent = await db.scalar(select(PaidLink).where(PaidLink.client_uuid == identity).execution_options(populate_existing=True))
    kind = "paid"
    if parent:
        plan = await db.get(Plan, parent.plan_id)
        required, limit = bool(plan and plan.slug.startswith("devices-")), plan.device_limit if plan else 0
        duration = parent.duration_ms or (plan.duration_days * 86400000 if plan else 0)
    else:
        kind = "trial"
        parent = await db.scalar(select(TrialLink).where(TrialLink.client_uuid == identity).execution_options(populate_existing=True))
        marker = await db.get(AppSetting, "device_hwid_start_at")
        required = bool(parent and parent.issued_at and marker and utc(parent.issued_at).timestamp() >= float(marker.value))
        from app.core.trial_policy import duration_for_assignment, device_limit_for_assignment
        limit = await device_limit_for_assignment(db, parent)
        duration = await duration_for_assignment(db, parent) if parent else DURATION_MS
    if not required:
        return None
    user = await db.get(User, parent.user_id, populate_existing=True) if parent and parent.user_id else None
    now = datetime.now(UTC)
    if (not user or not user.is_active or user.is_blocked or not parent.issued_at
            or parent.status not in {"ACTIVE", "WAITING", "ACTIVATING"}
            or not parent.checked_at or utc(parent.checked_at) <= now - timedelta(minutes=2)
            or (parent.expires_at and utc(parent.expires_at) <= now)
            or (kind == "trial" and not parent.started_at and utc(parent.issued_at) <= now - timedelta(hours=1))
            or (kind == "trial" and parent.pending_uuid)):
        raise ValueError("Подписка недоступна.")
    return kind, parent, user, limit, duration


async def admit(db, identity, hwid, platform, model, settings):
    policy = await parent_access(db, identity, settings)
    if not policy:
        return None
    kind, parent, user, limit, duration = policy
    from app.services.trial_upgrade import HASH_PARENT
    hash_parent = await db.get(AppSetting, HASH_PARENT + identity)
    digest = device_hash(hash_parent.value if hash_parent else identity, hwid, settings.app_secret)
    await db.execute(update(User).where(User.id == user.id).values(is_active=User.is_active))
    # Serialize all subscription admissions for this account before count+insert.
    policy = await parent_access(db, identity, settings)
    if not policy:
        raise ValueError("Subscription policy changed")
    kind, parent, user, limit, duration = policy
    if limit < 1 or duration < 1:
        raise ValueError("Invalid subscription device policy")
    row = await db.scalar(select(DeviceBinding).where(DeviceBinding.parent_uuid == identity,
                                                       DeviceBinding.device_hash == digest))
    if row:
        if row.user_id != user.id or row.status in {"REVOKED", "REVOKING"}:
            raise ValueError("Привязка устройства отключена. Обратитесь в поддержку.")
        return row
    count = await db.scalar(select(func.count()).select_from(DeviceBinding).where(
        DeviceBinding.parent_uuid == identity, DeviceBinding.status != "REVOKED"))
    if count >= limit:
        raise ValueError(f"Лимит устройств: {limit}. Обратитесь в поддержку для замены устройства.")
    child = str(uuid4())
    vault = TokenVault(settings.app_secret)
    raw = urlsplit(vault.reveal(parent.link_encrypted))
    if raw.scheme != "vless" or raw.username != identity:
        raise ValueError("Source access changed")
    child_raw = urlunsplit(raw._replace(netloc=child + "@" + raw.netloc.split("@", 1)[1]))
    row = DeviceBinding(user_id=user.id, paid_link_id=parent.id if kind == "paid" else None,
                        trial_link_id=parent.id if kind == "trial" else None,
                        parent_uuid=identity, device_hash=digest, client_uuid=child,
                        label="AERA device " + child[:8], platform=(platform or "")[:32], device_model=(model or "")[:64],
                        link_encrypted=vault.cipher.encrypt(child_raw.encode()).decode(), duration_ms=duration)
    db.add(row)
    await db.flush()
    return row


async def child_primary(writer, binding, parent_client, expiry, enabled):
    """Only this registered child UUID/email may be created or updated."""
    inbound = await writer.get_inbound(writer.inbound_id)
    if inbound.get("protocol") != "vless" or inbound.get("nodeId") not in {None, 0}:
        raise ValueError("Unsupported device inbound")
    clients = json.loads(inbound["settings"]) if isinstance(inbound["settings"], str) else inbound["settings"]
    matches = [c for c in clients["clients"] if c.get("id") == binding.client_uuid or c.get("email") == "aera-" + binding.client_uuid]
    desired = dict(id=binding.client_uuid, email="aera-" + binding.client_uuid,
                   flow=parent_client.get("flow", ""), limitIp=1, totalGB=0, reset=0,
                   expiryTime=expiry, enable=enabled, subId=binding.client_uuid.replace("-", ""),
                   comment=binding.label)
    if not matches:
        if enabled:
            await writer.request("POST", "panel/api/clients/add", {"client": desired, "inboundIds": [writer.inbound_id]})
            await verify_child(writer, binding, desired)
        return
    if len(matches) != 1 or matches[0]["id"] != binding.client_uuid or matches[0]["email"] != desired["email"]:
        raise ValueError("Device identity conflict")
    actual = matches[0]
    if ((actual.get("flow") or "") != desired["flow"]
            or any(int(actual.get(k) or 0) != desired[k] for k in ["limitIp", "totalGB", "reset"])):
        raise ValueError("Device limits changed independently")
    if actual.get("enable") == enabled and int(actual.get("expiryTime", 0)) == expiry:
        await verify_child(writer, binding, desired)
        return
    global_record = await writer.request("GET", "panel/api/clients/get/" + quote(desired["email"], safe=""))
    if global_record.get("inboundIds") != [writer.inbound_id] or global_record["client"].get("uuid") != binding.client_uuid:
        raise ValueError("Shared device client cannot be modified")
    payload = {k: v for k, v in global_record["client"].items() if k not in {"id", "uuid", "createdAt", "updatedAt"}}
    payload.update(id=binding.client_uuid, expiryTime=expiry, enable=enabled)
    if global_record["client"].get("createdAt"):
        payload["created_at"] = global_record["client"]["createdAt"]
    await writer.request("POST", "panel/api/clients/update/" + quote(desired["email"], safe="") + f"?inboundIds={writer.inbound_id}", payload)
    await verify_child(writer, binding, desired)


async def verify_child(writer, binding, desired):
    actual = await read_client(writer, binding.client_uuid)
    if (actual.get("email") != desired["email"]
            or bool(actual.get("enable")) != desired["enable"]
            or int(actual.get("expiryTime") or 0) != desired["expiryTime"]
            or (actual.get("flow") or "") != desired["flow"]
            or any(int(actual.get(k) or 0) != desired[k] for k in ["limitIp", "totalGB", "reset"])):
        raise ValueError("Primary device write was not verified")


async def provision(sessions, settings, binding_id=None):
    # Release the shared mutation lock between devices so a new import is not
    # queued behind the entire periodic reconciliation batch. Upgrades still
    # hold the same lock while changing parent and child credentials together.
    if binding_id is None:
        async with sessions() as db:
            ids = list(await db.scalars(select(DeviceBinding.id).where(DeviceBinding.status != "REVOKED")))
        for row_id in ids:
            try:
                await provision(sessions, settings, row_id)
            except Exception:
                logger.exception("Device synchronization failed for binding %s", row_id)
            await asyncio.sleep(0)
        return
    from scripts.panel_sync import bridge, mirror_uuid, NODES
    from app.subscription.multinode import panel_state
    vault = TokenVault(settings.app_secret)
    async with _provision_lock:
        async with sessions() as db:
            query = select(DeviceBinding.id)
            if binding_id:
                query = query.where(DeviceBinding.id == binding_id)
            else:
                query = query.where(DeviceBinding.status != "REVOKED")
            ids = list(await db.scalars(query))
            if not ids:
                return
            config = json.loads(vault.reveal((await db.get(AppSetting, "trial_observer")).value))
        writer = XUI328Client(config["base_url"], config["api_token"], config["inbound_id"], allow_writes=True)
        try:
            for row_id in ids:
                try:
                    async with sessions() as db:
                        binding = await db.get(DeviceBinding, row_id)
                        from app.services.trial_upgrade import pending
                        if binding.paid_link_id and await pending(db, binding.paid_link_id):
                            continue
                        try:
                            policy = await parent_access(db, binding.parent_uuid, settings)
                        except ValueError:
                            policy = None
                        parent = (await db.get(PaidLink, binding.paid_link_id) if binding.paid_link_id
                                  else await db.get(TrialLink, binding.trial_link_id))
                        if not parent:
                            raise ValueError("Device parent missing")
                        enabled = bool(policy and binding.status not in {"REVOKED", "REVOKING"})
                        source_identity = urlsplit(vault.reveal(parent.link_encrypted)).username
                        try:
                            source = await read_client(writer, source_identity)
                        except ValueError:
                            if enabled:
                                raise
                            # A rotated/removed trial parent must not prevent disabling
                            # its old registered child on every owned node.
                            child_query = parse_qs(urlsplit(vault.reveal(binding.link_encrypted)).query)
                            source = {"flow": child_query.get("flow", [""])[0]}
                        child = None
                        try:
                            child = await read_client(writer, binding.client_uuid)
                        except ValueError:
                            pass
                        states = await panel_state(db, vault)
                        child_entry = (states or {}).get("entries", {}).get(binding.client_uuid, {})
                    inspect_trial_use = bool(binding.trial_link_id and parent.client_uuid == binding.parent_uuid
                                             and parent.user_id == binding.user_id and not parent.pending_uuid
                                             and parent.status == "WAITING"
                                             and binding.status not in {"REVOKED", "REVOKING"})
                    if not parent.started_at and child and (enabled or inspect_trial_use):
                        inbound = await writer.get_inbound(writer.inbound_id)
                        nl_stat = next((s for s in inbound.get("clientStats", []) if s.get("email") == child["email"]), {})
                        remotes = list(child_entry.get("remote_states", []))
                        if inspect_trial_use:
                            # Fresh reads prevent a stale snapshot from treating a
                            # just-started child as an unused timed-out assignment.
                            snapshots = await asyncio.gather(*(asyncio.wait_for(bridge({"version": 1, "clients": [],
                                                                       "inspect_only": True}, node), 8) for node in NODES), return_exceptions=True)
                            remotes = [reply.get("states", {}).get(mirror_uuid(node, binding.client_uuid), {})
                                       for node, reply in zip(NODES, snapshots) if isinstance(reply, dict)]
                        remotes.append(dict(enable=child.get("enable"), expiry_ms=nl_stat.get("expiryTime") or child.get("expiryTime", 0),
                                            up=nl_stat.get("up", 0), down=nl_stat.get("down", 0), last_online=nl_stat.get("lastOnline", 0)))
                        if inspect_trial_use:
                            # A child disabled by the hour cutoff may still carry
                            # evidence of a valid connection made before that cutoff.
                            remotes = [dict(state, enable=True) for state in remotes if state]
                        now = datetime.now(UTC)
                        if binding.paid_link_id:
                            from app.services.paid_first_use import align, first_deadline
                            candidates = [(first_deadline(parent, source, remote, now), remote) for remote in remotes]
                            candidates = [c for c in candidates if c[0] is not None]
                            if candidates:
                                await align(sessions, config, parent.id, source, min(candidates, key=lambda c: c[0])[1], now)
                        else:
                            from app.services.trial_node_first_use import align_trial
                            await align_trial(sessions, config, parent.id, source, remotes, now)
                        async with sessions() as db:
                            parent = await db.get(PaidLink if binding.paid_link_id else TrialLink, parent.id)
                            try:
                                enabled = bool(await parent_access(db, binding.parent_uuid, settings)) and binding.status not in {"REVOKED", "REVOKING"}
                            except ValueError:
                                enabled = False
                    expiry = int(utc(parent.expires_at).timestamp() * 1000) if parent.expires_at else -binding.duration_ms
                    await child_primary(writer, binding, source, expiry, enabled)
                    replies = await asyncio.gather(*(asyncio.wait_for(bridge({"version": 1, "partial": True, "clients": [{
                        "uuid": mirror_uuid(node, binding.client_uuid), "limit_ip": 1, "expiry_ms": expiry,
                        "enable": enabled, "flow": source.get("flow", ""), "preserve_first_use": enabled and expiry < 0,
                    }]}, node), 3) for node in NODES), return_exceptions=True)
                    primary_link = build_primary_link(await writer.get_inbound(writer.inbound_id), binding.client_uuid, source.get("flow", ""), binding.label)
                    links = [primary_link]
                    verified_nodes = 0
                    for node, reply in zip(NODES, replies):
                        if isinstance(reply, BaseException):
                            logger.warning('Device secondary unavailable node=%s', node)
                            continue
                        identity = mirror_uuid(node, binding.client_uuid)
                        state = reply.get("states", {}).get(identity, {})
                        if (not state or bool(state.get("enable")) != enabled
                                or (enabled and (int(state.get("expiry_ms", 0)) == 0
                                     or (expiry > 0 and int(state.get("expiry_ms", 0)) != expiry)))):
                            logger.warning('Device secondary unverified node=%s', node)
                            continue
                        verified_nodes += 1
                        if enabled:
                            links.append(reply["links"][identity])
                    if not enabled and verified_nodes != len(NODES):
                        raise ValueError('Full revocation must be verified on every node')
                    async with sessions.begin() as db:
                        current = await db.get(DeviceBinding, binding.id)
                        # An operator revocation wins over an in-flight provisioning request.
                        if current.status in {"REVOKED", "REVOKING"}:
                            if not enabled:
                                current.status = "REVOKED"
                                current.bundle_encrypted = None
                                current.checked_at = datetime.now(UTC)
                            continue
                        current.status = "ACTIVE" if enabled and parent.started_at else "WAITING" if enabled else "EXPIRED"
                        current.started_at, current.expires_at = parent.started_at, parent.expires_at
                        current.link_encrypted = vault.cipher.encrypt(primary_link.encode()).decode()
                        current.checked_at = datetime.now(UTC)
                        current.bundle_encrypted = vault.cipher.encrypt(json.dumps(links).encode()).decode() if enabled else None
                except Exception:
                    if binding_id:
                        raise
                    # One unavailable parent/device cannot starve expiry or
                    # revocation propagation for the remaining subscribers.
                    logger.exception("Device synchronization failed for binding %s", row_id)
        finally:
            await writer.close()


async def get_configuration(sessions, settings, identity, hwid, platform="", model=""):
    async with sessions.begin() as db:
        row = await admit(db, identity, hwid, platform, model, settings)
        if row is None:
            return {"required": False}
        row_id = row.id
        from app.services.trial_upgrade import pending
        if row.paid_link_id and await pending(db, row.paid_link_id):
            raise ValueError("Оплаченная подписка обновляется. Повторите обновление через несколько секунд.")
        # A prepared device must not wait behind provisioning of unrelated accounts.
        policy = await parent_access(db, identity, settings)
        parent = policy[1] if policy else None
        if (parent and row.status in {"WAITING", "ACTIVE"} and row.bundle_encrypted
                and row.checked_at and utc(row.checked_at) > datetime.now(UTC)-timedelta(minutes=2)
                and row.duration_ms == policy[4] and row.expires_at == parent.expires_at):
            links = json.loads(TokenVault(settings.app_secret).reveal(row.bundle_encrypted))
            if 1 <= len(links) <= 4 and all(urlsplit(link).scheme == "vless" for link in links):
                return {"required": True, "links": links}
    # Return verified available nodes; periodic reconciliation fills missing nodes.
    # Never publish an unverified child or fall back to a shared parent credential.
    await provision(sessions, settings, row_id)
    async with sessions() as db:
        row = await db.get(DeviceBinding, row_id)
        if not await parent_access(db, identity, settings):
            raise ValueError("Subscription policy changed")
        if row.status not in {"WAITING", "ACTIVE"} or not row.bundle_encrypted:
            raise ValueError("Устройство подготавливается. Обновите подписку чуть позже.")
        return {"required": True, "links": json.loads(TokenVault(settings.app_secret).reveal(row.bundle_encrypted))}
