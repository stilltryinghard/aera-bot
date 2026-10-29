import hmac
from uuid import UUID
from fastapi import APIRouter, HTTPException, Request
from app.config import get_settings
from app.db.session import sessions
from app.services import platega

router = APIRouter()

@router.post('/payments/platega/{channel}/callback')
async def callback(channel: str, request: Request):
    if channel not in {'bot', 'site'}:
        raise HTTPException(404)
    credentials = platega.credentials(channel)
    if not (hmac.compare_digest(request.headers.get('x-merchantid', ''), credentials['merchant_id'])
            and hmac.compare_digest(request.headers.get('x-secret', ''), credentials['secret'])):
        raise HTTPException(401)
    if len(await request.body()) > 16384:
        raise HTTPException(413)
    try:
        body = await request.json()
        transaction = str(UUID(body['id']))
        await platega.reconcile(sessions, get_settings(), channel, transaction, body)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, 'Invalid payment notification') from None
    return {'ok': True}

@router.post('/internal/platega/checkout')
async def checkout(request: Request):
    if request.client.host not in {'127.0.0.1', '::1'}:
        raise HTTPException(403)
    if not hmac.compare_digest(request.headers.get('x-aera-bridge', ''), platega.configuration()['bridge_secret']):
        raise HTTPException(401)
    try:
        body = await request.json()
        return await platega.create_payment(sessions, get_settings(), body['user_id'], body['method'],
                                            'site', plan_id=body['plan_id'])
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(409, str(error)) from None
