"""Read-only access to encrypted, synchronized per-customer node bundles."""
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit, quote, parse_qs
from uuid import UUID
from app.db.models import AppSetting, PaidLink, ManualAccess, FamilyAccess, TrialLink

PREFIX = 'multinode.v1:'
PANEL_KEY = 'multinode.panel.v1'

def panel_token(identity, secret):
    value=UUID(identity).hex
    return value+'.'+hmac.new(secret.encode(),('aera-panel-sub-v1:'+value).encode(),hashlib.sha256).hexdigest()

def panel_identity(token, secret):
    try:
        identity=str(UUID(hex=token.split('.')[0]))
        return identity if hmac.compare_digest(token,panel_token(identity,secret)) else None
    except (ValueError,AttributeError):return None

async def panel_state(db,vault,now=None):
    now=now or datetime.now(UTC)
    row=await db.get(AppSetting,PANEL_KEY)
    if not row:return None
    try:
        state=json.loads(vault.reveal(row.value))
        return state if 0<=now.timestamp()-state['checked_at']<180 else None
    except (ValueError,KeyError,TypeError):return None

def utc(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value

def named(link, label):
    u = urlsplit(link)
    return urlunsplit((u.scheme, u.netloc, u.path, u.query, quote(label)))

async def customer_nodes(db, user, vault, now=None):
    now = now or datetime.now(UTC)
    if not user.is_active or user.is_blocked:
        return []
    setting = await db.get(AppSetting, PREFIX + user.id)
    if not setting:
        return []
    try:
        state = json.loads(vault.reveal(setting.value))
        # Do not publish stale additional access after an observer/worker failure.
        if not 0 <= now.timestamp() - state['checked_at'] < 180:
            return []
        result = []
        primary = await panel_state(db, vault, now)
        for entry in state['entries']:
            model = {'paid': PaidLink, 'manual': ManualAccess, 'family': FamilyAccess, 'trial': TrialLink}.get(entry['kind'])
            if not model:
                continue
            row = await db.get(model, entry['source_id'])
            if not row or row.user_id != user.id or not row.link_encrypted:
                continue
            if entry['kind'] in {'paid','trial'} and (not row.issued_at or row.status not in {'ACTIVE', 'WAITING', 'ACTIVATING'} or not row.checked_at or utc(row.checked_at) <= now - timedelta(minutes=2)):
                continue
            if entry['kind']=='trial' and row.pending_uuid:
                continue
            if entry['kind'] == 'family' and not row.is_active:
                continue
            deadline = getattr(row, 'expires_at', None)
            if deadline and utc(deadline) <= now:
                continue
            if entry['expiry_ms'] > 0 and entry['expiry_ms'] <= now.timestamp() * 1000:
                continue
            original = vault.reveal(row.link_encrypted)
            if hashlib.sha256(original.encode()).hexdigest() != entry['source_hash']:
                continue
            actual = (primary or {}).get('entries', {}).get(urlsplit(original).username, {})
            nl_link = actual.get('nl_link', original)
            for node, link in [('nl', nl_link)]+([('fi', entry['fi_link'])] if entry.get('fi_link') else [])+[(n['node'],n['link']) for n in entry.get('nodes',[]) if n.get('enabled')]:
                u = urlsplit(link)
                if u.scheme != 'vless' or not u.username or not u.hostname or not u.port:
                    raise ValueError('Invalid managed link')
                result.append({'node': node, 'source_uuid': urlsplit(original).username,
                    'link': named(link, 'AERA · ' + {'nl':'Нидерланды','fi':'Финляндия','de':'Германия','uk':'Великобритания'}[node] + ' · ' + entry['label']),
                    'expiry_ms': entry['expiry_ms']})
        return result
    except (ValueError, TypeError, KeyError):
        return []


async def owned_panel_url(db, user, row, vault, settings, now=None):
    """Publish only the exact owned purchase, after both node states are verified."""
    from app.subscription.links import panel_url
    now = now or datetime.now(UTC)
    if not user.is_active or user.is_blocked or row.user_id != user.id or not row.link_encrypted:
        return None
    if isinstance(row, PaidLink):
        if not row.issued_at or row.status not in {'ACTIVE', 'WAITING', 'ACTIVATING'} or not row.checked_at or utc(row.checked_at) <= now - timedelta(minutes=2):
            return None
    if isinstance(row, FamilyAccess) and not row.is_active:
        return None
    deadline = getattr(row, 'expires_at', None)
    if deadline and utc(deadline) <= now:
        return None
    raw = vault.reveal(row.link_encrypted)
    identity = urlsplit(raw).username
    state = await panel_state(db, vault, now)
    entry = (state or {}).get('entries', {}).get(identity)
    if not entry or not entry.get('enabled') or not entry.get('secondary_enabled',entry.get('fi_enabled')) or entry.get('status') not in {'ACTIVE', 'WAITING'}:
        return None
    if entry.get('expiry_ms', 0) > 0 and entry['expiry_ms'] <= now.timestamp() * 1000:
        return None
    # Match the current owned credentials to the fresh verified primary snapshot.
    for field in ('scheme', 'username', 'port'):
        if getattr(urlsplit(raw), field) != getattr(urlsplit(entry['nl_link']), field):
            return None
    if urlsplit(entry['nl_link']).hostname not in {urlsplit(raw).hostname, '153.76.116.212'}:
        return None
    original_query, current_query = parse_qs(urlsplit(raw).query), parse_qs(urlsplit(entry['nl_link']).query)
    for name in ('security','pbk','sni','sid','flow'):
        if original_query.get(name,['']) != current_query.get(name,['']):
            return None
    if isinstance(row, PaidLink):
        from app.services.trial_upgrade import ALIAS
        from app.db.models import AppSetting
        alias = await db.get(AppSetting, ALIAS + row.id)
        if alias and alias.value == user.id:
            from app.subscription.links import customer_url
            return customer_url(user.id, settings)
        from app.db.models import Plan, DeviceSubscription
        plan = await db.get(Plan, row.plan_id)
        if plan and plan.slug.startswith("devices-"):
            public = await db.get(DeviceSubscription, identity)
            if not public:
                return None
            identity = public.public_uuid
    return panel_url(identity, settings)
