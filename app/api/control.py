"""Private read-only control-plane snapshot. No credentials or connection links."""
import asyncio
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, func
from app.config import get_settings
from app.db.models import User, Plan, Payment, PaidLink, ManualAccess, FamilyAccess, TrialLink, AccessRenewal, AppSetting
from app.db.session import sessions
from app.services.paid_pool import current_customer_condition
from app.services.commerce import utc

router=APIRouter()
_cached=None
_cached_at=0
_lock=asyncio.Lock()
_previous={}
_usage_cache={}
_usage_at=0

async def panel_usage(db):
    global _usage_cache,_usage_at
    if time.monotonic()-_usage_at<30:return _usage_cache
    from app.core.security import TokenVault
    from app.integrations.xui.v328 import XUI328Client
    stored=await db.get(AppSetting,'trial_observer')
    if not stored:return {}
    reader=None
    try:
        cfg=json.loads(TokenVault(get_settings().app_secret).reveal(stored.value))
        reader=XUI328Client(cfg['base_url'],cfg['api_token'],cfg['inbound_id'],allow_writes=False)
        inbound=await asyncio.wait_for(reader.get_inbound(cfg['inbound_id']),3)
        raw=inbound.get('settings',{})
        if isinstance(raw,str):raw=json.loads(raw)
        stats={s.get('email'):s for s in inbound.get('clientStats',[])}
        checked=datetime.now(UTC).isoformat()
        result={}
        for c in raw.get('clients',[]):
            s=stats.get(c.get('email'))
            result[c.get('id')]={'traffic_used_bytes':int(s.get('up',0))+int(s.get('down',0)) if s else None,
                'panel_devices':int(c.get('limitIp',0)),'traffic_limit_bytes':int(c.get('totalGB',0)),'panel_checked_at':checked}
        _usage_cache=result;_usage_at=time.monotonic()
        return result
    except Exception:
        # Do not present an old traffic reading as a current reading after errors.
        _usage_cache={};_usage_at=time.monotonic();return {}
    finally:
        if reader:await reader.close()

def iso(value):
    return utc(value).isoformat() if value else None

def local_metrics():
    import os,shutil
    fields=[int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if len(line.split())>=2}
    rx=tx=0
    for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
        name,raw=line.split(':',1)
        if name.strip()=='lo' or name.strip().startswith(('veth','docker','br-')):continue
        values=raw.split();rx+=int(values[0]);tx+=int(values[8])
    disk=shutil.disk_usage('/')
    return dict(cpu_total=sum(fields),cpu_idle=fields[3]+fields[4],cores=os.cpu_count(),
        ram_total=mem['MemTotal'],ram_used=mem['MemTotal']-mem['MemAvailable'],
        disk_total=disk.total,disk_used=disk.used,rx=rx,tx=tx,
        uptime=float(Path('/proc/uptime').read_text().split()[0]),sample_time=time.time(),load=os.getloadavg()[0])

def rates(node,data):
    previous=_previous.get(node)
    now=time.monotonic()
    result={**data,'cpu_percent':data.get('cpu_percent'),'rx_mbps':None,'tx_mbps':None}
    if previous:
        old,then=previous
        elapsed=now-then;total=data['cpu_total']-old['cpu_total'];idle=data['cpu_idle']-old['cpu_idle']
        if total>0:result['cpu_percent']=round(max(0,min(100,100*(total-idle)/total)),1)
        if elapsed>0:
            for key in ['rx','tx']:
                delta=data[key]-old[key]
                if delta>=0:result[key+'_mbps']=round(delta*8/elapsed/1e6,3)
    _previous[node]=(data,now)
    return result

async def managed_metrics(node):
    from scripts.panel_sync import bridge
    try:
        reply = await bridge({"version": 1, "clients": [], "metrics_only": True}, node, timeout=6)
        data = reply.get("metrics", {})
        required = {"cpu_total", "cpu_idle", "ram_total", "ram_used", "disk_total", "disk_used",
                    "rx", "tx", "uptime", "sample_time", "cores", "load"}
        if not required.issubset(data) or not all(type(data[k]) in {int, float} for k in required):
            return None
        if not 0 <= time.time() - data["sample_time"] < 30:
            return None
        return data
    except Exception:
        return None


async def nl_metrics():
    from app.core.security import TokenVault
    from app.integrations.xui.v328 import XUI328Client
    reader=None
    try:
        async with sessions() as db:
            row=await db.get(AppSetting,'trial_observer')
            cfg=json.loads(TokenVault(get_settings().app_secret).reveal(row.value))
        reader=XUI328Client(cfg['base_url'],cfg['api_token'],cfg['inbound_id'],allow_writes=False)
        status=await asyncio.wait_for(reader.request('GET','panel/api/server/status'),5)
        return dict(cpu_total=0,cpu_idle=0,cpu_percent=float(status['cpu']),cores=status['cpuCores'],
            ram_total=status['mem']['total'],ram_used=status['mem']['current'],disk_total=status['disk']['total'],disk_used=status['disk']['current'],
            rx=status['netTraffic']['recv'],tx=status['netTraffic']['sent'],uptime=status['uptime'],sample_time=time.time(),load=None,
            vpn_running=status.get('xray',{}).get('state')=='running')
    except Exception:return None
    finally:
        if reader:await reader.close()

async def node_snapshots():
    local = local_metrics()
    nl, fi, de, uk = await asyncio.gather(nl_metrics(), *(managed_metrics(n) for n in ("fi", "de", "uk")))
    nodes = [dict(id="core", name="AERA CORE", location="Приложение и бот", role="Биллинг · API",
                  ip="153.76.116.2", status="online", metrics=rates("core", local))]
    for identity, name, location, ip, data in [
        ("nl", "AERA NL 01", "Нидерланды", "153.76.116.212", nl),
        ("fi", "AERA FIN 01", "Хельсинки, Финляндия", "193.233.199.162", fi),
        ("de", "AERA DE 01", "Германия", "147.45.178.100", de),
        ("uk", "AERA UK 01", "Великобритания", "87.120.219.28", uk),
    ]:
        nodes.append(dict(id=identity, name=name, location=location, role="VPN · VLESS Reality", ip=ip,
                          status="online" if data else "unavailable", metrics=rates(identity, data) if data else None))
    return nodes


def metric_history():
    import sqlite3
    path=Path('/var/lib/aera-control/history.db')
    if not path.exists():return {}
    try:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2) as db:
            rows=db.execute('select node,at,rx,tx,cpu from samples where at>? order by at',(time.time()-3600,)).fetchall()
        result={}
        for node,at,rx,tx,cpu in rows:
            result.setdefault(node,[]).append(dict(at=datetime.fromtimestamp(at,UTC).isoformat(),rx=rx,tx=tx,cpu=cpu))
        return result
    except sqlite3.Error:return {}

async def database_snapshot():
    now=datetime.now(UTC)
    async with sessions() as db:
        usage=await panel_usage(db)
        from app.subscription.multinode import panel_state
        from app.core.security import TokenVault
        state=await panel_state(db,TokenVault(get_settings().app_secret))
        for identity, entry in (state or {}).get('entries', {}).items():
            if identity in usage:
                usage[identity] = {**usage[identity], 'traffic_used_bytes': entry_traffic(entry)}
        total=await db.scalar(select(func.count()).select_from(User))
        active=await db.scalar(select(func.count()).select_from(User).where(User.is_active.is_(True),User.is_blocked.is_(False),current_customer_condition()))
        from app.db.models import DeviceBinding
        binding_children={}
        for binding in await db.scalars(select(DeviceBinding)):
            binding_children.setdefault((binding.user_id,binding.parent_uuid),[]).append(binding.client_uuid)
        plans={p.id:p for p in await db.scalars(select(Plan))}
        users=list(await db.scalars(select(User).order_by(User.created_at.desc()).limit(500)))
        by_user={u.id:[] for u in users}
        for kind,model in [('paid',PaidLink),('manual',ManualAccess),('family',FamilyAccess),('trial',TrialLink)]:
            query=select(model).where(model.user_id.in_(list(by_user)))
            if kind=='paid':query=query.where(PaidLink.issued_at.is_not(None))
            for r in await db.scalars(query):
                plan=plans.get(getattr(r,'plan_id',None));deadline=getattr(r,'expires_at',None)
                status='EXPIRED' if deadline and utc(deadline)<=now else getattr(r,'status','ACTIVE')
                checked=getattr(r,'checked_at',None)
                if kind in {'paid','trial'} and (not checked or utc(checked)<now-timedelta(minutes=2)):status='UNKNOWN'
                if kind=='family' and not r.is_active:status='DISABLED'
                extra={}
                if usage and getattr(r,'link_encrypted',None):
                    try:
                        from app.core.security import TokenVault
                        uuid=urlsplit(TokenVault(get_settings().app_secret).reveal(r.link_encrypted)).username
                        extra=usage.get(uuid,{})
                        children=binding_children.get((r.user_id,uuid),[])
                        if children and state:
                            identities=[uuid]+children
                            extra={**extra,'traffic_used_bytes':sum(
                                entry_traffic(state['entries'].get(identity,{})) for identity in identities),
                                'panel_checked_at':datetime.fromtimestamp(state['checked_at'],UTC).isoformat()}
                    except Exception:pass
                by_user[r.user_id].append(dict(kind=kind,plan=plan.name if plan else 'Семья' if kind=='family' else 'Пробный доступ',
                    status=status,expires_at=iso(deadline),checked_at=iso(checked),
                    devices=getattr(r,'device_limit',None) if kind=='family' else (None if plan and plan.unlimited_devices else plan.device_limit if plan else None),**extra))
        customers=[dict(telegram_id=u.telegram_id,name=u.first_name or u.username or 'Пользователь',username=u.username,
            blocked=u.is_blocked or not u.is_active,subscriptions=by_user[u.id],created_at=iso(u.created_at)) for u in users]
        latest=list(await db.scalars(select(Payment).order_by(Payment.created_at.desc()).limit(40)))
        identities={u.id:u for u in users}
        payments=[dict(id=p.id[:8],telegram_id=identities[p.user_id].telegram_id if p.user_id in identities else None,
            amount=p.amount_minor/100 if p.currency!='XTR' else p.amount_minor,currency=p.currency,status=p.status,
            provider=p.provider,method=(p.details or {}).get('method'),date=iso(p.paid_at or p.created_at)) for p in latest]
        money={currency:amount/100 if currency!='XTR' else amount for currency,amount in
            await db.execute(select(Payment.currency,func.sum(Payment.amount_minor)).where(Payment.status=='PAID').group_by(Payment.currency))}
        stock=[dict(status=status,count=count) for status,count in await db.execute(select(PaidLink.status,func.count()).where(PaidLink.issued_at.is_(None)).group_by(PaidLink.status))]
        renewals=[dict(status=status,count=count) for status,count in await db.execute(select(AccessRenewal.status,func.count()).group_by(AccessRenewal.status))]
        from app.subscription.multinode import panel_state
        from app.core.security import TokenVault
        state=await panel_state(db,TokenVault(get_settings().app_secret))
        listing=panel_listing(state)
        return dict(summary=dict(users=total,customers=active,revenue=money,stock=stock,renewals=renewals),clients=customers,payments=payments,clients_limit=500,
            panel_clients=listing['items'],panel_counts=listing['counts'],panel_page_meta={k:listing[k] for k in ['page','pages','total','per_page']},panel_checked_at=state['checked_at'] if state else None)

@router.get('/internal/control/snapshot')
async def snapshot(request:Request):
    from app.services.platega import configuration
    if request.client.host not in {'127.0.0.1','::1'} or not hmac.compare_digest(request.headers.get('x-aera-bridge',''),configuration()['bridge_secret']):
        raise HTTPException(403)
    identity=request.headers.get('x-aera-user','')
    async with sessions() as db:
        u=await db.get(User,identity)
        if not u or u.telegram_id!=6233935382 or not u.is_active or u.is_blocked:raise HTTPException(403)
    global _cached,_cached_at
    async with _lock:
        if _cached is not None and time.monotonic()-_cached_at<5:return _cached
        database,nodes=await asyncio.gather(database_snapshot(),node_snapshots())
        _cached={**database,'nodes':nodes,'history':metric_history(),'updated_at':datetime.now(UTC).isoformat(),'refresh_seconds':10}
        _cached_at=time.monotonic()
        return _cached


def entry_traffic(entry):
    return sum(int(stats.get(k, 0)) for stats in
               [entry.get("nl_stats", {}), entry.get("fi_stats", {})]
               + [n.get("stats", {}) for n in entry.get("nodes", [])] for k in ("up", "down"))


def entry_servers(entry):
    return (["nl"] if entry.get("enabled") else []) + (["fi"] if entry.get("fi_enabled") else []) + [
        n["node"] for n in entry.get("nodes", []) if n.get("enabled") and n.get("node") in {"de", "uk"}]


def panel_listing(state,page=1,search=''):
    entries=list((state or {}).get('entries',{}).values())
    counts={'total':len(entries),'active':sum(e['status']=='ACTIVE' for e in entries),'waiting':sum(e['status']=='WAITING' for e in entries)}
    query=search.strip().casefold()
    filtered=[e for e in entries if not query or query in (e['label']+' '+e['uuid']+' '+str(e.get('telegram_id') or '')).casefold()]
    filtered.sort(key=lambda e:({'ACTIVE':0,'WAITING':1,'EXPIRED':2,'DISABLED':3}.get(e['status'],4),e['label'].casefold(),e['uuid']))
    total=len(filtered);pages=max(1,(total+9)//10);page=min(max(1,page),pages)
    rows=[]
    for e in filtered[(page-1)*10:page*10]:
        nl=e.get('nl_stats',{});fi=e.get('fi_stats',{})
        rows.append(dict(id=e['uuid'][:8],record_id=e['uuid'],name=e['label'],telegram_id=e.get('telegram_id'),status=e['status'],devices=e['limit_ip'],expiry_ms=e['expiry_ms'],fi_enabled=e['fi_enabled'],
            nl_bytes=nl.get('up',0)+nl.get('down',0),fi_bytes=fi.get('up',0)+fi.get('down',0),traffic_used_bytes=entry_traffic(e),servers=entry_servers(e)))
    return dict(items=rows,total=total,page=page,pages=pages,per_page=10,counts=counts,checked_at=(state or {}).get('checked_at'))

async def require_dashboard_owner(request):
    from app.services.platega import configuration
    if request.client.host not in {'127.0.0.1','::1'} or not hmac.compare_digest(request.headers.get('x-aera-bridge',''),configuration()['bridge_secret']):raise HTTPException(403)
    async with sessions() as db:
        owner=await db.get(User,request.headers.get('x-aera-user',''))
        if not owner or owner.telegram_id!=6233935382 or not owner.is_active or owner.is_blocked:raise HTTPException(403)

from fastapi import Query
from fastapi.responses import JSONResponse
from uuid import UUID

@router.get('/internal/control/panel-clients')
async def panel_clients_page(request:Request,page:int=Query(1,ge=1,le=1000000),search:str=Query('',max_length=128)):
    await require_dashboard_owner(request)
    from app.subscription.multinode import panel_state
    from app.core.security import TokenVault
    async with sessions() as db:state=await panel_state(db,TokenVault(get_settings().app_secret))
    if not state:raise HTTPException(503,'No fresh panel data')
    return JSONResponse(panel_listing(state,page,search),headers={'Cache-Control':'no-store'})

@router.get('/internal/control/panel-clients/{client_id}/link')
async def panel_client_link(client_id:UUID,request:Request):
    await require_dashboard_owner(request)
    from app.subscription.multinode import panel_state,panel_token
    from app.core.security import TokenVault
    async with sessions() as db:
        state=await panel_state(db,TokenVault(get_settings().app_secret))
        if not state:raise HTTPException(503,'No fresh panel data')
        entry=state['entries'].get(str(client_id))
        if not entry:raise HTTPException(404)
        from app.db.models import DeviceSubscription, DeviceBinding
        binding=await db.scalar(select(DeviceBinding).where(DeviceBinding.client_uuid==str(client_id)))
        parent=binding.parent_uuid if binding else str(client_id)
        alias=await db.get(DeviceSubscription,parent)
        identity=alias.public_uuid if alias else parent
        from app.subscription.links import panel_url
        path=urlsplit(panel_url(identity,get_settings())).path
        trial=await db.scalar(select(TrialLink).where(TrialLink.client_uuid==parent))
        if trial and trial.user_id and trial.issued_at:
            from app.subscription.links import customer_url
            user=await db.get(User,trial.user_id)
            if not user or user.is_blocked or not user.is_active:raise HTTPException(403)
            path=urlsplit(customer_url(user.id,get_settings())).path
        if binding:
            entry=state['entries'].get(parent)
            if not entry:raise HTTPException(503,'No fresh parent data')
    nodes=entry_servers(entry)
    return JSONResponse({'path':path,'name':entry['label'],'status':entry['status'],'servers':len(nodes),'nodes':nodes},headers={'Cache-Control':'no-store'})


@router.get('/internal/control/customers/{telegram_id}/links')
async def customer_links(telegram_id:int,request:Request):
    await require_dashboard_owner(request)
    from app.services.cabinet import entries, personal_link
    from app.subscription.multinode import owned_panel_url
    from app.core.security import TokenVault
    settings=get_settings();vault=TokenVault(settings.app_secret)
    async with sessions() as db:
        user=await db.scalar(select(User).where(User.telegram_id==telegram_id))
        if not user:raise HTTPException(404)
        if user.is_blocked or not user.is_active:raise HTTPException(403)
        links=[]
        for kind,row in await entries(db,user):
            url=(await owned_panel_url(db,user,row,vault,settings)
                 if kind in {'paid','manual','family'} else personal_link((kind,row),user))
            if url:links.append({'kind':kind,'url':url})
    return JSONResponse({'telegram_id':telegram_id,'links':links},headers={'Cache-Control':'no-store'})
