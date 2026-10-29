"""Build public primary-node credentials from the current selected inbound only."""
import base64
import json
from urllib.parse import urlencode,quote
from uuid import UUID
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat


def primary_link(inbound,identity,flow,label):
    identity=str(UUID(identity))
    stream=inbound.get('streamSettings',{})
    if isinstance(stream,str):stream=json.loads(stream)
    if (inbound.get('protocol')!='vless' or inbound.get('nodeId') not in (None,0)
            or stream.get('network') not in ('tcp','raw') or stream.get('security')!='reality'):
        raise ValueError('Unsupported primary inbound')
    port=inbound.get('port')
    if type(port) is not int or not 1<=port<=65535:
        raise ValueError('Invalid primary port')
    if flow not in ('','xtls-rprx-vision'):
        raise ValueError('Unsupported primary flow')
    reality=stream.get('realitySettings',{})
    if isinstance(reality,str):reality=json.loads(reality)
    names=reality.get('serverNames');short_ids=reality.get('shortIds')
    if not isinstance(names,list) or not names or not isinstance(names[0],str) or not names[0]:
        raise ValueError('Primary REALITY server name unavailable')
    if not isinstance(short_ids,list) or not short_ids or not isinstance(short_ids[0],str):
        raise ValueError('Primary REALITY short ID unavailable')
    # Private bytes stay in process memory and never enter the returned URL/logs.
    try:
        private=reality['privateKey']
        decoded=base64.urlsafe_b64decode(private+'='*((-len(private))%4))
        public=base64.urlsafe_b64encode(X25519PrivateKey.from_private_bytes(decoded).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode().rstrip('=')
    except (KeyError,ValueError,TypeError):
        raise ValueError('Primary REALITY public key unavailable') from None
    query=urlencode({'type':'tcp','encryption':'none','security':'reality','pbk':public,'fp':'chrome','sni':names[0],'sid':short_ids[0],'spx':'/','flow':flow})
    return f'vless://{identity}@153.76.116.212:{port}?{query}#{quote(label)}'
