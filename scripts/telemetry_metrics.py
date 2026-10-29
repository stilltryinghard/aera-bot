"""Read-only node metrics for the standalone history collector.

Keep metrics independent of dashboard/billing/Telegram imports. The collection
and rate functions retain the existing Control response format.
"""
import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.db.models import AppSetting
from app.db.session import sessions

_previous = {}
ROOT = Path('/var/lib/aera-node-sync')
NODES = {'fi': '193.233.199.162', 'de': '147.45.178.100', 'uk': '87.120.219.28'}


async def _metrics_bridge(node, *, timeout=6):
    # This collector can request OS metrics only; it never sends client changes.
    request = {"version": 1, "clients": [], "metrics_only": True}
    proc = await asyncio.create_subprocess_exec(
        '/usr/bin/ssh', '-i', str(ROOT / 'provision_ed25519'),
        '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', 'UserKnownHostsFile=' + str(ROOT / 'known_hosts'),
        '-o', 'ConnectTimeout=5', 'root@' + NODES[node],
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(json.dumps(request).encode()), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError) as error:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        if isinstance(error, asyncio.CancelledError):
            raise
        raise RuntimeError('Node metrics timeout') from None
    if proc.returncode:
        raise RuntimeError('Node metrics unavailable')
    result = json.loads(out)
    if not isinstance(result, dict) or result.get('version') != 1:
        raise RuntimeError('Invalid node metrics response')
    return result


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
    try:
        reply = await _metrics_bridge(node, timeout=6)
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
