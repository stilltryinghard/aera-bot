"""Verified trial-to-paid promotion with stable customer/device identities."""
import asyncio
import fcntl
import json
import logging
from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy import select, update
from app.core.security import TokenVault
from app.db.models import (AppSetting, DeviceBinding, PaidLink, Payment, Plan,
                           SaleOrder, SupportTicket, TrialLink, User)
from app.services.commerce import utc

PREFIX = 'trial-upgrade.v1:'
ALIAS = 'trial-paid-user-url.v1:'
HASH_PARENT = 'device-hash-parent.v1:'
SYNC_LOCK = '/opt/aera/.trial-upgrade-sync.lock'
log = logging.getLogger('aera')


async def pending(db, paid_id):
    row = await db.get(AppSetting, PREFIX + paid_id, populate_existing=True)
    return bool(row and json.loads(row.value)['status'] != 'DONE')


async def eligible_trial(db, user_id):
    trial = await db.scalar(select(TrialLink).where(
        TrialLink.user_id == user_id, TrialLink.issued_at.is_not(None),
        TrialLink.pending_uuid.is_(None),
        TrialLink.status.in_(['ACTIVE', 'WAITING', 'ACTIVATING', 'EXPIRED', 'BLOCKED'])))
    if trial and not await db.scalar(select(PaidLink.id).where(PaidLink.client_uuid == trial.client_uuid)):
        return trial
    return None


async def verified_order(db, order):
    payment = await db.get(Payment, order.payment_id) if order.payment_id else None
    ticket = await db.get(SupportTicket, order.ticket_id)
    if (not order.paid_at or not payment or payment.status != 'PAID' or not payment.paid_at
            or payment.provider not in {'platega', 'telegram_stars'}
            or payment.user_id != ticket.user_id or payment.plan_id != order.plan_id):
        return None
    user = await db.get(User, ticket.user_id)
    return user if user and user.is_active and not user.is_blocked else None


async def setting(db, key, value):
    row = await db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


async def start_job(db, paid, order, user, children, settings, *, trial=None):
    if await db.get(AppSetting, PREFIX + paid.id):
        return True
    plan = await db.get(Plan, order.plan_id)
    if not plan or not 1 <= plan.device_limit <= 10 or not 0 < plan.duration_days <= 366:
        raise ValueError('Unsupported upgrade tariff')
    now = datetime.now(UTC)
    issued_at = utc(paid.issued_at or now).isoformat()
    paid.issued_at = None
    running = bool(trial.started_at if trial else any(c.started_at for c in children))
    previous_end = trial.expires_at if trial else None
    duration = plan.duration_days * 86400000
    # Keep remaining trial time; commit one target before any panel write.
    deadline = int((max(now, utc(previous_end)) if previous_end else now).timestamp()*1000) + duration if running else -duration
    paid.duration_ms = duration
    paid.label = plan.name
    paid.started_at = now if running else None
    paid.expires_at = datetime.fromtimestamp(deadline/1000, UTC) if deadline > 0 else None
    paid.status, paid.checked_at = 'UPGRADING', None
    original_parent = children[0].parent_uuid if children else paid.client_uuid
    selected = sorted(children, key=lambda c: (c.status == 'REVOKED', utc(c.issued_at), c.id))[:plan.device_limit]
    selected_ids = {c.id for c in selected}
    for child in children:
        if child.id not in selected_ids:
            child.status = 'REVOKING'
            child.bundle_encrypted = None
            continue
        if child.user_id != user.id or child.parent_uuid != original_parent:
            raise ValueError('Device ownership changed')
        child.paid_link_id, child.trial_link_id = paid.id, None
        child.parent_uuid = paid.client_uuid
        child.duration_ms = duration
        child.started_at, child.expires_at = paid.started_at, paid.expires_at
        child.status, child.checked_at = 'PENDING', None
        child.bundle_encrypted = None
    await setting(db, HASH_PARENT + paid.client_uuid, original_parent)
    await setting(db, ALIAS + paid.id, user.id)
    job = dict(status='PREPARED', paid_id=paid.id, order_id=order.id, owner=user.id,
               identity=paid.client_uuid, deadline=deadline, duration=duration,
               limit=plan.device_limit, label=plan.name, children=[c.id for c in selected],
               trial_id=trial.id if trial else None, issued_at=issued_at, created=now.isoformat())
    await setting(db, PREFIX + paid.id, json.dumps(job))
    if trial:
        # Retain historical usage, but permanently remove this identity from trial stock.
        trial.user_id, trial.status = None, 'CONVERTED'
        trial.pending_uuid, trial.recycle_reason = None, 'PAID_UPGRADE'
        from app.services.trial_pool import fingerprint
        from app.db.models import TrialUsage
        digest = fingerprint(user.telegram_id, settings.app_secret)
        if not await db.get(TrialUsage, digest):
            db.add(TrialUsage(fingerprint=digest, activated_at=trial.started_at))
        user.trial_used = True
    await db.flush()
    return True


async def prepare(db, order, settings):
    user = await verified_order(db, order)
    if not user:
        return False
    await db.execute(update(User).where(User.id == user.id).values(is_active=User.is_active))
    existing = await db.scalar(select(PaidLink).where(PaidLink.order_id == order.id))
    if existing and await db.get(AppSetting, PREFIX + existing.id):
        return True
    if existing and existing.issued_at:
        return False
    trial = await eligible_trial(db, user.id)
    if not trial:
        return False
    if existing:
        if existing.started_at or existing.expires_at:
            raise ValueError('Reserved stock has unexpected usage')
        existing.order_id, existing.user_id, existing.status = None, None, 'UNVERIFIED'
        await db.flush()
    paid = PaidLink(client_uuid=trial.client_uuid, link_encrypted=trial.link_encrypted,
                    label=trial.label, panel_email=trial.panel_email, user_id=user.id,
                    plan_id=order.plan_id, order_id=order.id, issued_at=datetime.now(UTC))
    db.add(paid)
    await db.flush()
    children = list(await db.scalars(select(DeviceBinding).where(
        DeviceBinding.user_id == user.id, DeviceBinding.trial_link_id == trial.id,
        DeviceBinding.parent_uuid == trial.client_uuid)))
    return await start_job(db, paid, order, user, children, settings, trial=trial)


async def restore_previous_device(db, paid, settings):
    """Recover a first purchase whose old trial master was already safely recycled."""
    if await db.get(AppSetting, PREFIX + paid.id):
        return True
    order = await db.get(SaleOrder, paid.order_id) if paid.order_id else None
    user = await verified_order(db, order) if order else None
    if not user or user.id != paid.user_id or paid.started_at or paid.status != 'WAITING':
        return False
    # Never reinterpret an established second subscription as a first trial purchase.
    other = await db.scalar(select(PaidLink.id).where(PaidLink.user_id == user.id,
        PaidLink.id != paid.id, PaidLink.issued_at.is_not(None)))
    if other:
        return False
    rows = list(await db.scalars(select(DeviceBinding).where(DeviceBinding.user_id == user.id,
        DeviceBinding.trial_link_id.is_not(None), DeviceBinding.issued_at <= paid.issued_at)
        .order_by(DeviceBinding.issued_at.desc())))
    if not rows:
        # A trial may have been recycled before any app imported it. Its stable
        # user URL still belongs to this customer even though no device exists.
        from app.services.trial_attempts import issued_count
        if await issued_count(db, user, settings.app_secret) > 0:
            await setting(db, ALIAS + paid.id, user.id)
        return False
    original_parent = rows[0].parent_uuid
    children = [c for c in rows if c.parent_uuid == original_parent]
    # An old registered child may be revived only for its same owner and paid order.
    for child in await db.scalars(select(DeviceBinding).where(DeviceBinding.paid_link_id == paid.id)):
        if child.status == 'ACTIVE' or child.started_at:
            return False
    for child in await db.scalars(select(DeviceBinding).where(DeviceBinding.paid_link_id == paid.id)):
        child.status, child.bundle_encrypted = 'REVOKING', None
    return await start_job(db, paid, order, user, children, settings)


async def write_parent(writer, identity, deadline, limit, label):
    from app.services.renewals import read_client
    client = await read_client(writer, identity)
    if int(client.get('totalGB') or 0) != 0 or int(client.get('reset') or 0) != 0:
        raise ValueError('Unexpected parent quota')
    route = 'panel/api/clients/update/' + quote(client['email'], safe='') + f'?inboundIds={writer.inbound_id}'
    global_record = await writer.request('GET', 'panel/api/clients/get/' + quote(client['email'], safe=''))
    if global_record.get('inboundIds') != [writer.inbound_id] or global_record['client'].get('uuid') != identity:
        raise ValueError('Parent identity changed')
    payload = {k:v for k,v in global_record['client'].items() if k not in {'id','uuid','createdAt','updatedAt'}}
    payload.update(id=identity, expiryTime=deadline, limitIp=limit, enable=True, comment=label)
    if global_record['client'].get('createdAt'):
        payload['created_at'] = global_record['client']['createdAt']
    if (client.get('enable') is not True or int(client.get('expiryTime') or 0) != deadline
            or int(client.get('limitIp') or 0) != limit or client.get('comment') != label):
        await writer.request('POST', route, payload)
    checked = await read_client(writer, identity)
    if (checked.get('enable') is not True or int(checked.get('expiryTime') or 0) != deadline
            or int(checked.get('limitIp') or 0) != limit):
        raise ValueError('Parent upgrade not verified')
    # Transport/credentials must be unchanged by tariff edits.
    for key in set(client) | set(checked):
        if key not in {'enable','expiryTime','limitIp','comment','updatedAt','updated_at'} and client.get(key) != checked.get(key):
            raise ValueError('Parent configuration changed')
    return checked


async def apply_job(sessions, settings, paid_id):
    from app.services.device_access import _provision_lock, child_primary
    from app.services.renewals import reader_for
    from app.services.source_links import primary_link
    from scripts.panel_sync import bridge, mirror_uuid, NODES
    with open(SYNC_LOCK, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        async with _provision_lock:
            async with sessions() as db:
                marker = await db.get(AppSetting, PREFIX + paid_id)
                job = json.loads(marker.value)
                if job['status'] == 'DONE':
                    return True
                paid = await db.get(PaidLink, paid_id)
                order = await db.get(SaleOrder, job['order_id'])
                user = await verified_order(db, order)
                if (not user or user.id != job['owner'] or paid.user_id != user.id
                        or paid.client_uuid != job['identity'] or paid.order_id != order.id):
                    raise ValueError('Upgrade ownership changed')
                children = [await db.get(DeviceBinding, i) for i in job['children']]
                if any(not c or c.user_id != user.id or c.paid_link_id != paid.id for c in children):
                    raise ValueError('Upgrade device changed')
                writer = await reader_for(db, settings)
            vault = TokenVault(settings.app_secret)
            try:
                if job['deadline'] < 0:
                    # Distinguish new use during a retry from the old trial timer.
                    # An old positive five-day expiry must not survive a new 30/90/etc term.
                    snapshots = await asyncio.gather(*(asyncio.wait_for(bridge(
                        {'version':1,'clients':[],'inspect_only':True},node),12) for node in NODES))
                    primary = await writer.get_inbound(writer.inbound_id)
                    stats = {x.get('email'):x for x in primary.get('clientStats',[])}
                    primary_clients = primary.get('settings',{})
                    if isinstance(primary_clients,str): primary_clients=json.loads(primary_clients)
                    primary_clients = {x['id']:x for x in primary_clients.get('clients',[])}
                    created_ms=int(datetime.fromisoformat(job['created']).timestamp()*1000)
                    now_ms=int(datetime.now(UTC).timestamp()*1000)
                    starts=[]
                    for identity in [paid.client_uuid]+[c.client_uuid for c in children]:
                        values=[reply.get('states',{}).get(mirror_uuid(node,identity),{})
                                for node,reply in zip(NODES,snapshots)]
                        local=stats.get(primary_clients.get(identity,{}).get('email'),{})
                        values.append({'last_online':local.get('lastOnline',0)})
                        for value in values:
                            observed=int(value.get('last_online',0))
                            if created_ms <= observed <= now_ms:
                                starts.append(observed)
                    if starts:
                        start=min(starts)
                        job['deadline']=start+job['duration']
                        async with sessions.begin() as db:
                            marker=await db.get(AppSetting,PREFIX+paid_id)
                            marker.value=json.dumps(job)
                            record=await db.get(PaidLink,paid_id)
                            record.started_at=datetime.fromtimestamp(start/1000,UTC)
                            record.expires_at=datetime.fromtimestamp(job['deadline']/1000,UTC)
                            paid.started_at=record.started_at
                source = await write_parent(writer, paid.client_uuid, job['deadline'], job['limit'], job['label'])
                for child in children:
                    await child_primary(writer, child, source, job['deadline'], True)
                wanted = [(paid.client_uuid, job['limit'])] + [(c.client_uuid,1) for c in children]
                async def update_node(node):
                    # The restricted node bridge accepts exactly ONE client per
                    # partial request. Keep the durable job incomplete until all
                    # parent/child writes and read-backs have been verified.
                    merged = {'states': {}, 'links': {}}
                    for identity, limit in wanted:
                        reply = await asyncio.wait_for(bridge({'version': 1, 'partial': True,
                            'clients': [dict(uuid=mirror_uuid(node, identity), limit_ip=limit,
                                expiry_ms=job['deadline'], enable=True, flow=source.get('flow', ''),
                                preserve_first_use=False)]}, node), 15)
                        for key in merged:
                            merged[key].update(reply.get(key, {}))
                    return merged
                replies = await asyncio.gather(*(update_node(node) for node in NODES))
                for node, reply in zip(NODES,replies):
                    for identity,limit in wanted:
                        state = reply.get('states',{}).get(mirror_uuid(node,identity),{})
                        if not state.get('enable') or int(state.get('expiry_ms',0)) != job['deadline']:
                            raise ValueError('Secondary upgrade not verified')
                        if 'limit_ip' in state and int(state['limit_ip']) != limit:
                            raise ValueError('Secondary device limit mismatch')
                inbound = await writer.get_inbound(writer.inbound_id)
                bundles = {}
                for child in children:
                    primary = primary_link(inbound,child.client_uuid,source.get('flow',''),child.label)
                    links = [primary]+[reply['links'][mirror_uuid(node,child.client_uuid)] for node,reply in zip(NODES,replies)]
                    bundles[child.id] = (primary,links)
                async with sessions.begin() as db:
                    current = await db.get(PaidLink,paid_id)
                    if current.user_id != job['owner'] or current.client_uuid != job['identity']:
                        raise ValueError('Upgrade ownership changed during provisioning')
                    current.issued_at = datetime.fromisoformat(job['issued_at'])
                    current.started_at = paid.started_at
                    current.expires_at = datetime.fromtimestamp(job['deadline']/1000,UTC) if job['deadline']>0 else None
                    current.status = 'ACTIVE' if current.started_at else 'WAITING'
                    current.checked_at = datetime.now(UTC)
                    for child_id,(primary,links) in bundles.items():
                        child=await db.get(DeviceBinding,child_id)
                        if child.paid_link_id!=paid_id or child.user_id!=job['owner']:
                            raise ValueError('Device ownership changed during provisioning')
                        child.status,child.started_at,child.expires_at=current.status,current.started_at,current.expires_at
                        child.checked_at=datetime.now(UTC)
                        child.link_encrypted=vault.cipher.encrypt(primary.encode()).decode()
                        child.bundle_encrypted=vault.cipher.encrypt(json.dumps(links).encode()).decode()
                    ticket=await db.get(SupportTicket,order.ticket_id)
                    ticket.status,ticket.closed_at='CONNECTED',datetime.now(UTC)
                    marker=await db.get(AppSetting,PREFIX+paid_id)
                    marker.value=json.dumps({**job,'status':'DONE','completed':datetime.now(UTC).isoformat()})
                    from app.services.notifications import enqueue
                    await enqueue(db,'paid-link:'+order.id,job['owner'],'ACCESS_READY',paid_id)
                return True
            finally:
                await writer.close()


async def process_pending(sessions, settings):
    async with sessions() as db:
        ids=[row.key[len(PREFIX):] for row in await db.scalars(select(AppSetting).where(
            AppSetting.key.startswith(PREFIX,autoescape=True))) if json.loads(row.value)['status']!='DONE']
    for identity in ids:
        try:
            await apply_job(sessions,settings,identity)
        except Exception as error:
            log.error('trial_paid_upgrade_pending type=%s',type(error).__name__)
            async with sessions.begin() as db:
                from app.services.notifications import enqueue
                for telegram_id in settings.admin_ids:
                    admin = await db.scalar(select(User).where(User.telegram_id == telegram_id))
                    if admin:
                        await enqueue(db, f'trial-upgrade-review:{identity}:{telegram_id}', admin.id,
                                      'PORTAL', '⚙️ Оплаченный переход с пробной ожидает проверки узлов. Бот повторит попытку; проверьте журнал trial_paid_upgrade_pending.')
