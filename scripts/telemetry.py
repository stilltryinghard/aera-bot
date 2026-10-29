"""Store real OS samples independently of browser visits, retain one day."""
import os
os.environ['DATABASE_URL']='sqlite+aiosqlite:////opt/aera/local.db'
import asyncio,sqlite3,time,logging
from pathlib import Path
from scripts.telemetry_metrics import node_snapshots

async def main():
    path=Path('/var/lib/aera-control/history.db')
    with sqlite3.connect(path) as db:
        db.execute('pragma journal_mode=WAL')
        db.execute('create table if not exists samples(node text not null,at real not null,rx real,tx real,cpu real,primary key(node,at))')
    path.chmod(0o600)
    while True:
        start=time.monotonic()
        try:
            nodes=await node_snapshots();at=time.time()
            with sqlite3.connect(path,timeout=2) as db:
                for node in nodes:
                    m=node['metrics']
                    if m and m['rx_mbps'] is not None:
                        db.execute('insert into samples values(?,?,?,?,?)',(node['id'],at,m['rx_mbps'],m['tx_mbps'],m['cpu_percent']))
                db.execute('delete from samples where at<?',(at-86400,))
        except Exception as exc:logging.error('Telemetry sample failed: %s',type(exc).__name__)
        await asyncio.sleep(max(1,10-(time.monotonic()-start)))

if __name__=='__main__':asyncio.run(main())
