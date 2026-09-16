"""Authenticated internal RPC; services never access another service's tables."""
import base64
import hmac
import json
import os
from pathlib import Path
import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI,Request
from fastapi.responses import JSONResponse

class ServiceUnavailable(ValueError):pass

def service_key():
    configured=os.environ.get('KB_SERVICE_KEY')
    if configured:
        Fernet(configured.encode());return configured
    root=Path(os.environ.get('DATA_DIR','.data'));root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=root/'kb-service.key'
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
    except FileExistsError:pass
    key=path.read_text().strip();Fernet(key.encode());return key

class Client:
    def __init__(self,name,url=None,key=None):
        self.name=name;self.url=(url or os.environ.get('KB_'+name.upper()+'_URL','http://127.0.0.1:'+('8011' if name=='management' else '8012'))).rstrip('/')
        self.key=key
    def headers(self):return {'X-Relay-Service-Key':self.key or service_key()}
    def decode(self,response):
        if response.status_code==404:raise KeyError('Knowledge resource unavailable')
        if response.status_code in (400,409,422):raise ValueError(response.json().get('detail','Invalid knowledge operation'))
        if response.status_code>=400:raise ServiceUnavailable(f'Knowledge {self.name} service is unavailable. Check service status.')
        return response.json()
    async def call(self,action,tenant='local',payload=None):
        try:
            async with httpx.AsyncClient(timeout=180,trust_env=False) as http:
                response=await http.post(self.url+'/rpc',headers=self.headers(),json={'action':action,'tenant':tenant,'payload':payload or {}})
            return self.decode(response)
        except (httpx.HTTPError,json.JSONDecodeError):raise ServiceUnavailable(f'Knowledge {self.name} service is unavailable. Check service status.') from None
    def call_sync(self,action,tenant='local',payload=None):
        try:
            with httpx.Client(timeout=30,trust_env=False) as http:
                response=http.post(self.url+'/rpc',headers=self.headers(),json={'action':action,'tenant':tenant,'payload':payload or {}})
            return self.decode(response)
        except (httpx.HTTPError,json.JSONDecodeError):raise ServiceUnavailable(f'Knowledge {self.name} service is unavailable. Check service status.') from None

def app_for(domain,name,allowed,async_domain=False,key=None):
    app=FastAPI(title='Relay Knowledge '+name,docs_url=None,redoc_url=None,openapi_url=None)
    token=key or service_key()
    @app.get('/health')
    def health():return {'status':'ok','service':name}
    @app.post('/rpc')
    async def rpc(request:Request):
        if not hmac.compare_digest(request.headers.get('X-Relay-Service-Key',''),token):return JSONResponse({'detail':'Service authentication required'},status_code=401)
        content=bytearray()
        async for part in request.stream():
            content.extend(part)
            if len(content)>256*1024*1024:return JSONResponse({'detail':'Service request too large'},status_code=413)
        try:
            data=json.loads(content)
            if not isinstance(data,dict) or set(data)-{'action','tenant','payload'}:raise ValueError('Invalid RPC request')
            action=data.get('action');tenant=data.get('tenant');payload=data.get('payload',{})
            if action not in allowed or not isinstance(tenant,str) or not 1<=len(tenant)<=128 or not isinstance(payload,dict):raise ValueError('Invalid RPC operation')
            if async_domain:result=await domain.call(action,tenant,payload)
            else:
                from starlette.concurrency import run_in_threadpool
                result=await run_in_threadpool(domain.call,action,tenant,payload)
            return result
        except KeyError:return JSONResponse({'detail':'Knowledge resource unavailable'},status_code=404)
        except ValueError as exc:return JSONResponse({'detail':str(exc)[:1000]},status_code=400)
        except Exception:return JSONResponse({'detail':'Knowledge operation failed; service recovery may be required'},status_code=503)
    app.state.domain=domain
    return app
