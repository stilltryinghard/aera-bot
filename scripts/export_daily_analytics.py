"""Read-only aggregate export: no customer names, IDs, keys or payment changes."""
import os
os.environ['DATABASE_URL']='sqlite+aiosqlite:////opt/aera/local.db'
import asyncio, json, socket
from datetime import UTC, datetime
from app.db.session import sessions
from app.config import get_settings
from app.services.analytics_report import daily_details
from app.services.bot_metrics import section_counts
from app.services.funnel_metrics import outcomes
from app.services.start_counter import start_count
from app.services.daily_metrics import TZ

async def main():
    assert socket.gethostname().split('.')[0]=='vps-21329'
    async with sessions() as db:
        now=datetime.now(UTC)
        data=await daily_details(db,get_settings().app_secret,now)
        data['updated']=now.astimezone(TZ).isoformat()
        data['since']=datetime.fromisoformat(data['since']).astimezone(TZ).isoformat()
        data['totals']=await outcomes(db,get_settings().app_secret)
        data['totals']['starts']=int(await start_count(db) or 0)
        data['totals']['sections']=await section_counts(db)
        print(json.dumps(data,ensure_ascii=False))

asyncio.run(main())
