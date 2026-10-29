"""Read-only aggregated export for the owner's private Google Sheet."""
import os
os.environ['DATABASE_URL']='sqlite+aiosqlite:////opt/aera/local.db'
import asyncio,json,socket
from datetime import datetime,UTC
from app.db.session import sessions
from app.config import get_settings
from app.services.daily_metrics import daily_rows,TZ
from app.services.funnel_metrics import outcomes
async def main():
 assert socket.gethostname().split('.')[0]=='vps-21329'
 async with sessions() as db:
  now=datetime.now(UTC)
  since,rows=await daily_rows(db,now,days=50000)
  totals=await outcomes(db,get_settings().app_secret)
  print(json.dumps(dict(updated=now.astimezone(TZ).isoformat(),since=since.astimezone(TZ).isoformat(),rows=rows,totals=totals),ensure_ascii=False))
asyncio.run(main())
