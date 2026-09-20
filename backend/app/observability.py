"""Content-free structured journals and request/run context propagation."""
from contextvars import ContextVar
from datetime import datetime,timezone
import json
import logging
import re
import time
import uuid

request_id=ContextVar('request_id',default=None)
run_id=ContextVar('run_id',default=None)
tenant_id=ContextVar('tenant_id',default=None)
logging.basicConfig(format='%(message)s')
log=logging.getLogger('relay.journal');log.setLevel(logging.INFO)
# Raw access logs include query strings; the structured middleware replaces them.
logging.getLogger('uvicorn.access').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
FIELDS={'event','request_id','run_id','tenant','method','path','status','duration_ms','seconds','node_id','transient','cached','nodes','resumed','provider','model','attempt','delay_seconds','error_type','worker_id','service','action','scope','reason_code','prompt_tokens','completion_tokens','calls'}

def valid_request_id(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',value) else uuid.uuid4().hex

def journal(**fields):
    data={'timestamp':datetime.now(timezone.utc).isoformat(),'request_id':request_id.get(),'run_id':run_id.get(),'tenant':tenant_id.get()}
    data.update({key:value for key,value in fields.items() if key in FIELDS})
    if isinstance(fields.get('usage'),dict):
        data['usage']={key:value for key,value in fields['usage'].items() if key in {'prompt_tokens','completion_tokens','calls'} and type(value) is int and value>=0}
    log.info(json.dumps(data,ensure_ascii=True,separators=(',',':'),default=str))

class RequestJournalMiddleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        identifier=valid_request_id(headers.get(b'x-request-id',b'').decode('ascii',errors='replace'))
        tokens=[(request_id,request_id.set(identifier)),(run_id,run_id.set(None)),(tenant_id,tenant_id.set(None))]
        scope.setdefault('state',{})['request_id']=identifier
        started=time.perf_counter();status=500;sent=False
        async def wrapped(message):
            nonlocal status,sent
            if message['type']=='http.response.start':
                status=message['status'];sent=True
                message={**message,'headers':[(k,v) for k,v in message.get('headers',[]) if k.lower()!=b'x-request-id']+[(b'x-request-id',identifier.encode())]}
            await send(message)
        try:
            await self.app(scope,receive,wrapped)
        except Exception as exc:
            journal(event='http.failure',error_type=type(exc).__name__)
            if not sent:
                from starlette.responses import JSONResponse
                await JSONResponse({'detail':'Internal server error','request_id':identifier},status_code=500)(scope,receive,wrapped)
            else:raise
        finally:
            route=scope.get('route')
            journal(event='http.request',method=scope['method'],path=getattr(route,'path','<unmatched>'),status=status,
                    duration_ms=round((time.perf_counter()-started)*1000,3),tenant=scope['state'].get('tenant_id',tenant_id.get()),
                    run_id=scope.get('path_params',{}).get('id') if getattr(route,'path','').startswith('/api/runs/') else run_id.get())
            for var,token in reversed(tokens):var.reset(token)


def log_failures(service):
    """Journal domain failures without serializing the request or exception message."""
    import functools
    import inspect
    def decorate(method):
        def failed(action,tenant,exc):
            journal(event='kb.'+service+'.failure',service=service,action=action if action in {'search','preview','verify','stats','build','publish','rebuild','upload','create','update','get','list','fail','cleanup','remove_document','delete','retry','claim','renew'} else 'other',tenant=tenant,error_type=type(exc).__name__)
        if inspect.iscoroutinefunction(method):
            @functools.wraps(method)
            async def wrapped(self,action,tenant,payload):
                try:return await method(self,action,tenant,payload)
                except Exception as exc:failed(action,tenant,exc);raise
        else:
            @functools.wraps(method)
            def wrapped(self,action,tenant,payload):
                try:return method(self,action,tenant,payload)
                except Exception as exc:failed(action,tenant,exc);raise
        return wrapped
    return decorate
