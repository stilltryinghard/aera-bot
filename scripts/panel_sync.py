"""Mirror all primary-panel clients without changing their original access."""
import os
os.environ['DATABASE_URL']='sqlite+aiosqlite:////opt/aera/local.db'
import asyncio,json,hashlib,time,logging,base64
from datetime import UTC,datetime
from pathlib import Path
from uuid import uuid5,NAMESPACE_URL
from urllib.parse import urlsplit,urlencode
from sqlalchemy import select
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from app.db.models import AppSetting,User,Plan,PaidLink,ManualAccess,FamilyAccess,TrialLink,DeviceBinding
from app.db.session import sessions
from app.config import get_settings
from app.core.security import TokenVault
from app.integrations.xui.v328 import XUI328Client
from app.subscription.multinode import PREFIX,PANEL_KEY,utc

log=logging.getLogger('aera-node-sync');ROOT=Path('/var/lib/aera-node-sync')
def decode(v):return json.loads(v) if isinstance(v,str) else v

NODES={'fi':'193.233.199.162','de':'147.45.178.100','uk':'87.120.219.28'}

def mirror_uuid(node, identity):
    return str(uuid5(NAMESPACE_URL,'aera-'+node+'-panel-v1:'+identity))

async def bridge(request, node='fi', *, timeout=150):
    proc=await asyncio.create_subprocess_exec('/usr/bin/ssh','-i',str(ROOT/'provision_ed25519'),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+str(ROOT/'known_hosts'),'-o','ConnectTimeout=5','root@'+NODES[node],stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:out,_=await asyncio.wait_for(proc.communicate(json.dumps(request).encode()),timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as error:
        if proc.returncode is None:proc.kill()
        await proc.wait()
        if isinstance(error, asyncio.CancelledError):raise
        raise RuntimeError('Node sync timeout') from None
    if proc.returncode:raise RuntimeError('Node reconciliation unavailable')
    result=json.loads(out);assert result['version']==1
    return result


async def collect_nodes(requests, *, timeout):
    """One failed secondary must not discard another node's verified result."""
    async def one(node, request):
        try:
            result = await asyncio.wait_for(bridge(request, node), timeout)
            required = ('states',) if request.get('inspect_only') else ('states', 'links', 'stats')
            if not isinstance(result, dict) or result.get('version') != 1 or any(not isinstance(result.get(k), dict) for k in required):
                raise ValueError('Invalid node result')
            if not request.get('inspect_only') and any(c['uuid'] not in result['links'] for c in request['clients']):
                raise ValueError('Incomplete node links')
            return result
        except Exception as error:
            log.warning('Node unavailable node=%s type=%s', node, type(error).__name__)
            return None
    nodes = list(requests)
    replies = await asyncio.gather(*(one(node, requests[node]) for node in nodes))
    return {node: reply for node, reply in zip(nodes, replies) if reply is not None}


async def _reconcile():
    snapshots = await collect_nodes({node: {'version': 1, 'clients': [], 'inspect_only': True} for node in NODES}, timeout=12)
    now=datetime.now(UTC);vault=TokenVault(get_settings().app_secret)
    async with sessions() as db:
        config=json.loads(vault.reveal((await db.get(AppSetting,'trial_observer')).value))
        observer=XUI328Client(config['base_url'],config['api_token'],config['inbound_id'],allow_writes=False)
        try:inbounds=await observer.get_inbounds()
        finally:await observer.close()
        users={u.id:u for u in await db.scalars(select(User))}
        plans={p.id:p for p in await db.scalars(select(Plan))}
        owned={}
        for kind,model in [('paid',PaidLink),('manual',ManualAccess),('family',FamilyAccess),('trial',TrialLink),('device',DeviceBinding)]:
            for row in await db.scalars(select(model)):
                if not row.user_id or not row.link_encrypted or (kind in {'paid','trial'} and not row.issued_at):continue
                link=vault.reveal(row.link_encrypted)
                owned.setdefault(urlsplit(link).username,[]).append((kind,row,link))
        # Only FI-first, issued purchases can change a primary deadline.
        # Existing active/manual/family clients and unsold stock are untouched.
        from app.services.paid_first_use import align
        activated=False
        for inbound in inbounds:
            for c in decode(inbound['settings']).get('clients',[]):
                for kind,row,link in owned.get(c['id'],[]):
                    if kind not in {'paid','trial'}: continue
                    remotes=[snapshots[node].get('states',{}).get(mirror_uuid(node,c['id']),{}) for node in snapshots]
                    if kind=='paid':
                        from app.services.paid_first_use import first_deadline
                        candidates=[(first_deadline(row,c,remote,now),remote) for remote in remotes]
                        candidates=[x for x in candidates if x[0] is not None]
                        remote=min(candidates,key=lambda x:x[0])[1] if candidates else {}
                        changed=await align(sessions,config,row.id,c,remote,now)
                    else:
                        from app.services.trial_node_first_use import align_trial
                        changed=await align_trial(sessions,config,row.id,c,remotes,now)
                    if changed:
                        activated=True
                        await db.refresh(row)
        if activated:
            observer=XUI328Client(config['base_url'],config['api_token'],config['inbound_id'],allow_writes=False)
            try:inbounds=await observer.get_inbounds()
            finally:await observer.close()
        wanted={node:[] for node in NODES};panel_entries={};bot_bundles={}
        for inbound in inbounds:
            stream=decode(inbound['streamSettings'])
            if inbound['protocol']!='vless' or stream.get('security')!='reality' or stream.get('network') not in {'tcp','raw'}:
                raise RuntimeError('Unsupported source inbound')
            r=stream['realitySettings'];secret=base64.urlsafe_b64decode(r['privateKey']+'='*((-len(r['privateKey']))%4))
            pub=base64.urlsafe_b64encode(X25519PrivateKey.from_private_bytes(secret).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode().rstrip('=')
            server=r['serverNames'][0];sid=r['shortIds'][0]
            stats={x['email']:x for x in inbound.get('clientStats',[])}
            for c in decode(inbound['settings']).get('clients',[]):
                identity=c['id']
                if identity in panel_entries:raise RuntimeError('Ambiguous source client')
                if int(c.get('totalGB',0)):raise RuntimeError('Finite quotas require aggregate enforcement')
                expiry=int(c.get('expiryTime',0));waiting=expiry<0;expired=expiry>0 and expiry<=now.timestamp()*1000
                enabled=bool(inbound.get('enable') and c.get('enable'));fi_enabled=enabled and not waiting and not expired
                owners=owned.get(identity,[])
                paid_waiting=any(kind in {'paid','device'} and row.issued_at and (row.status=='WAITING' or (kind=='device' and row.status=='PENDING')) and not row.started_at and row.duration_ms and (expiry == -row.duration_ms or expiry > 0) for kind,row,link in owners)
                trial_owners=[row for kind,row,link in owners if kind=='trial']
                trial_waiting=any(row.issued_at and row.status in {'WAITING','ACTIVATING'} and not row.pending_uuid and (row.started_at or now.timestamp()-utc(row.issued_at).timestamp()<=3600) for row in trial_owners)
                fi_enabled=enabled and not expired and (not waiting or paid_waiting or trial_waiting)
                # Stock trials must never activate independently before assignment.
                source_trial=await db.scalar(select(TrialLink).where(TrialLink.client_uuid==identity))
                if source_trial and not trial_owners and not any(kind=="paid" for kind,_,_ in owners):fi_enabled=False
                for kind,row,link in owners:
                    user=users.get(row.user_id)
                    if not user or not user.is_active or user.is_blocked or (kind=='family' and not row.is_active) or (getattr(row,'expires_at',None) and utc(row.expires_at)<=now) or (kind=='trial' and (row.pending_uuid or row.status not in {'ACTIVE','WAITING','ACTIVATING'})) or (kind=='device' and row.status not in {'WAITING','ACTIVE','PENDING'}):fi_enabled=False
                fi=str(uuid5(NAMESPACE_URL,'aera-fi-panel-v1:'+identity))
                for node in NODES:
                    wanted[node].append({'uuid':mirror_uuid(node,identity),'limit_ip':int(c.get('limitIp',0)),'expiry_ms':expiry,'enable':fi_enabled,'flow':c.get('flow',''),'preserve_first_use':(paid_waiting or trial_waiting) and expiry < 0})
                legacy='vless://'+identity+'@153.76.116.212:'+str(inbound['port'])+'?'+urlencode({'type':'tcp','encryption':'none','security':'reality','pbk':pub,'fp':'chrome','sni':server,'sid':sid,'spx':'/','flow':c.get('flow','')})+'#AERA-Netherlands'
                stat=stats.get(c.get('email'),{})
                panel_entries[identity]={'uuid':identity,'fi_uuid':fi,'label':c.get('comment') or c.get('email') or identity[:8],'expiry_ms':expiry,'limit_ip':int(c.get('limitIp',0)),'enabled':enabled,'fi_enabled':fi_enabled,'secondary_enabled':fi_enabled,'status':'DISABLED' if not enabled else 'WAITING' if waiting else 'EXPIRED' if expired else 'ACTIVE','nl_link':legacy,'nl_stats':{'up':int(stat.get('up',0)),'down':int(stat.get('down',0)),'last_online':int(stat.get('lastOnline',0))},'telegram_id':users[owners[0][1].user_id].telegram_id if owners and owners[0][1].user_id in users else None}
                if fi_enabled:
                    for kind,row,link in owners:
                        if kind=='device':continue
                        user=users.get(row.user_id);deadline=getattr(row,'expires_at',None)
                        if not user or (deadline and utc(deadline)<=now) or (kind=='paid' and row.status not in {'ACTIVE','WAITING'}):continue
                        plan=plans.get(getattr(row,'plan_id',None))
                        bot_bundles.setdefault(row.user_id,[]).append({'kind':kind,'source_id':row.id,'source_hash':hashlib.sha256(link.encode()).hexdigest(),'fi_uuid':fi,'expiry_ms':expiry,'label':plan.name if plan else 'Пробный доступ' if kind=='trial' else 'Семья'})
        results = await collect_nodes({node: {'version': 1, 'clients': wanted[node]} for node in snapshots}, timeout=60)
        result = results.get('fi')
        for entry in panel_entries.values():
            fi=entry.pop('fi_uuid')
            entry['fi_link'] = result['links'][fi] if result else None
            entry['fi_stats'] = result['stats'].get(fi,{}) if result else {}
            entry['fi_enabled'] = entry['secondary_enabled'] and result is not None
            entry['nodes'] = [dict(node=node, enabled=entry['secondary_enabled'] and node in results,
                **({'link': results[node]['links'][mirror_uuid(node,entry['uuid'])], 'stats': results[node]['stats'].get(mirror_uuid(node,entry['uuid']),{})} if node in results else {'stats': {}})) for node in ('de','uk')]
            entry['remote_states'] = [dict(results[node].get('states',{}).get(mirror_uuid(node,entry['uuid']),{}), node=node) for node in results]
        states={PANEL_KEY:{'checked_at':time.time(),'entries':panel_entries,'nodes_available':{node: node in results for node in NODES}}}
        for user_id,entries in bot_bundles.items():
            for entry in entries:
                fi=entry.pop('fi_uuid');entry['fi_link']=result['links'][fi] if result else None
                primary=next(p for p in panel_entries.values() if mirror_uuid('fi',p['uuid'])==fi)
                entry['nodes']=primary['nodes']
            states[PREFIX+user_id]={'checked_at':time.time(),'entries':entries}
        for setting in await db.scalars(select(AppSetting).where(AppSetting.key.startswith(PREFIX))):
            if setting.key not in states:states[setting.key]={'checked_at':time.time(),'entries':[]}
        for key,state in states.items():
            value=vault.cipher.encrypt(json.dumps(state,ensure_ascii=False).encode()).decode();setting=await db.get(AppSetting,key)
            if setting:setting.value=value
            else:db.add(AppSetting(key=key,value=value))
        await db.commit()
        log.info('Panel sync: %d clients, %d active, %d waiting, %d bot customers, %d changes',len(panel_entries),sum(e['status']=='ACTIVE' for e in panel_entries.values()),sum(e['status']=='WAITING' for e in panel_entries.values()),len(bot_bundles),sum(x['changed'] for x in results.values()))

async def reconcile():
    # Serialize full node snapshots with the scoped in-place tariff upgrade.
    import fcntl
    from app.services.trial_upgrade import SYNC_LOCK
    with open(SYNC_LOCK, 'a') as upgrade_lock:
        try:
            fcntl.flock(upgrade_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        return await _reconcile()


async def main():
    import fcntl
    lock=open(ROOT/'worker.lock','w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    while True:
        try:await reconcile()
        except Exception as exc:log.error('Panel sync failed: %s',type(exc).__name__)
        await asyncio.sleep(10)

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(reconcile() if '--once' in __import__('sys').argv else main())
