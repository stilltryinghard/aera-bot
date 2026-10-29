"""Persist one trial deadline after verified use on a managed secondary node."""
import json,hashlib
from datetime import UTC,datetime
from urllib.parse import quote
from app.db.models import AppSetting,TrialLink,User
from app.services.commerce import utc
from app.services.renewals import read_client,fingerprint
from app.services.paid_first_use import FirstUseClient
from app.services.trial_pool import DURATION_MS,confirm_activation

PREFIX='trial-node-first-use.v1:'

def remote_start(row,remote,now,duration_ms=DURATION_MS):
    if not row.issued_at or row.started_at or row.activation_deadline_ms or row.pending_uuid or row.status!='WAITING':
        return None
    issued=int(utc(row.issued_at).timestamp()*1000)
    observed=int(remote.get('last_online',0))
    expiry=int(remote.get('expiry_ms',0))
    if not remote.get('enable') or expiry<=0:
        return None
    if not (int(remote.get('up',0))+int(remote.get('down',0))>0 or observed>issued):
        return None
    start=max(expiry-duration_ms,observed)
    if start<issued-1000 or start>int(now.timestamp()*1000)+5000:
        return None
    # An unstarted assignment must not acquire new access after its claim hour.
    if start>issued+3600000:
        return None
    return start

async def align_trial(sessions,config,record_id,source,remotes,now):
    async with sessions.begin() as db:
        row=await db.get(TrialLink,record_id)
        user=await db.get(User,row.user_id) if row and row.user_id else None
        if not user or not user.is_active or user.is_blocked or row.pending_uuid:
            return False
        stored=await db.get(AppSetting,PREFIX+row.id)
        if stored:
            intent=json.loads(stored.value)
            if intent.get('done'):return False
        else:
            from app.core.trial_policy import duration_for_assignment
            duration_ms=await duration_for_assignment(db,row)
            candidates=[remote_start(row,x,now,duration_ms) for x in remotes]
            candidates=[x for x in candidates if x is not None]
            if not candidates or not source.get('enable'):return False
            start=min(candidates)
            intent={'uuid':row.client_uuid,'owner':row.user_id,'issued':utc(row.issued_at).isoformat(),
                    'email':source['email'],'baseline':row.baseline_bytes,'start':start,'deadline':start+duration_ms,
                    'primary_expiry':int(source.get('expiryTime',0)),
                    'fingerprint':hashlib.sha256(json.dumps(fingerprint(source),sort_keys=True).encode()).hexdigest()}
            db.add(AppSetting(key=PREFIX+row.id,value=json.dumps(intent)))
        if (row.client_uuid,row.user_id,utc(row.issued_at).isoformat(),row.baseline_bytes)!=(intent['uuid'],intent['owner'],intent['issued'],intent['baseline']):
            raise ValueError('Trial assignment changed')
        if row.activation_deadline_ms not in (None,intent['deadline']):
            raise ValueError('Trial deadline changed')
        row.started_at=datetime.fromtimestamp(intent['start']/1000,UTC)
        row.activation_deadline_ms=intent['deadline']
        row.status='ACTIVATING'
    writer=FirstUseClient(config['base_url'],config['api_token'],config['inbound_id'],allow_writes=True)
    try:
        client=await read_client(writer,intent['uuid'])
        digest=hashlib.sha256(json.dumps(fingerprint(client),sort_keys=True).encode()).hexdigest()
        if digest!=intent['fingerprint'] or client['email']!=intent['email'] or not client.get('enable'):
            raise ValueError('Trial client changed')
        actual=int(client.get('expiryTime',0))
        if actual!=intent['deadline']:
            if actual>0 and actual!=intent['primary_expiry'] and abs(actual-intent['deadline'])>120000:
                raise ValueError('Primary trial deadline changed independently')
            record=await writer.request('GET','panel/api/clients/get/'+quote(client['email'],safe=''))
            original=record['client']
            payload={k:v for k,v in original.items() if k not in {'id','uuid','createdAt','updatedAt'}}
            payload.update(id=intent['uuid'],expiryTime=intent['deadline'])
            if original.get('createdAt'):payload['created_at']=original['createdAt']
            writer.route='panel/api/clients/update/'+quote(client['email'],safe='')+f'?inboundIds={writer.inbound_id}'
            await writer.request('POST',writer.route,payload);writer.route=None
            checked=await read_client(writer,intent['uuid'])
            if int(checked['expiryTime'])!=intent['deadline'] or fingerprint(checked)!=fingerprint(client):
                raise ValueError('Trial deadline update unverified')
    finally:await writer.close()
    async with sessions.begin() as db:
        row=await db.get(TrialLink,record_id)
        if row.user_id!=intent['owner'] or row.client_uuid!=intent['uuid'] or row.pending_uuid or row.activation_deadline_ms!=intent['deadline']:
            raise ValueError('Trial assignment changed during activation')
        from app.config import get_settings
        await confirm_activation(db,row,get_settings())
        row.checked_at=datetime.now(UTC)
        stored=await db.get(AppSetting,PREFIX+row.id)
        stored.value=json.dumps({**intent,'done':True})
    return True
