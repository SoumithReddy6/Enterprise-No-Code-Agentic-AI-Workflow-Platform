"""Authenticated API. Execution jobs are persisted and claimed by Relay workers."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import Field
from .models import StrictModel, Workflow
from .registry import REGISTRY
from .compiler import validate_workflow
from .storage import Store, local_key, WorkflowConflict
from .auth import install_auth
from .worker import Worker
from .providers import ollama_models,supports
from .tool_service import ToolService
from .tool_api import install_tool_routes
from .platform_validation import platform_errors,kb_errors
from .kb_gateway import install_kb_routes

class RunRequest(StrictModel):
    workflow:Workflow
    message:str=Field(default='',max_length=20000)
class WorkflowUpdate(Workflow):
    updated_at:str|None=Field(default=None,max_length=64)
class ModelRequest(StrictModel):
    provider:Literal['openai','claude','ollama']
    model:str=Field(min_length=1,max_length=100,pattern=r'^\S+$')
    credential_id:str=Field(default='',max_length=128)
class ModelUpdate(StrictModel):
    enabled:bool
class CredentialRequest(StrictModel):
    provider:Literal['openai','claude']='openai'
    name:str=Field(min_length=1,max_length=120)
    secret:str=Field(min_length=1,max_length=4000)


def create_app(database_url=None,encryption_key=None,auth_enabled=True,embedded_worker=None,knowledge_services=None):
    directory=Path(os.environ.get('DATA_DIR','.data'));directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    store=Store(database_url or os.environ.get('DATABASE_URL',f'sqlite:///{directory}/workflows.db'),encryption_key or local_key(directory))
    if embedded_worker is None:embedded_worker=os.environ.get('RELAY_EMBEDDED_WORKER','true').lower()=='true'
    @asynccontextmanager
    async def lifespan(app):
        worker=asyncio.create_task(Worker(store).serve()) if embedded_worker else None
        yield
        if worker:worker.cancel();await asyncio.gather(worker,return_exceptions=True)
        store.engine.dispose()
    app=FastAPI(title='Relay Workflow API',version='0.2.0',lifespan=lifespan)
    app.state.store=store
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','testserver','backend'])
    app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:3000','http://localhost:3000'],allow_credentials=True,allow_methods=['GET','POST','PUT'],allow_headers=['Content-Type'])
    auth=install_auth(app,store,enabled=auth_enabled)
    tenant=auth.tenant
    app.state.tools=ToolService(store)
    install_tool_routes(app,app.state.tools,tenant)
    install_kb_routes(app,store,tenant,knowledge_services)
    async def submission_errors(workflow,tenant_id,checkpoints=None,dependencies=None):
        return validate_workflow(workflow)+store.model_errors(workflow,tenant_id)+platform_errors(store,workflow,tenant_id)+await kb_errors(app.state.knowledge_services,workflow,tenant_id,checkpoints,dependencies)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request,exc):
        return JSONResponse(status_code=422,content={'detail':[{'loc':list(e['loc']),'msg':e['msg'],'type':e['type']} for e in exc.errors()]})

    def safe_draft(workflow):
        if any(edge.kind=='store' for edge in workflow.edges) or any(node.type.startswith('vector_') for node in workflow.nodes):
            raise HTTPException(422,'This workflow used the retired VectorDB nodes. Create a named knowledge base and reconnect a Retrieve node before importing or replaying it.')
        for node in workflow.nodes:
            definition=REGISTRY.get(node.type)
            if not definition or set(node.config)-set(definition.config_model.model_fields):
                raise HTTPException(422,'Unknown node configuration. Credentials must be stored separately and referenced by ID.')
        return workflow.model_dump(mode='json')

    def fetch_run(id,tenant_id):
        try:return store.run(id,tenant_id)
        except KeyError:raise HTTPException(404,'Run not found') from None

    @app.get('/api/health')
    async def health():return {'status':'ok','mode':'authenticated-workspaces' if auth_enabled else 'test-local','durable_execution':True}
    @app.get('/api/nodes')
    async def nodes(tenant_id:str=Depends(tenant)):return [n.public() for n in REGISTRY.values()]
    @app.get('/api/providers/ollama/models')
    async def models(tenant_id:str=Depends(tenant)):
        # The chat-model picker must not offer embedding-only models.
        discovery=await ollama_models()
        return {**discovery,'models':[m for m in discovery['models'] if supports(m,'completion')]}
    @app.post('/api/validate')
    async def validate(workflow:Workflow,tenant_id:str=Depends(tenant)):
        errors=await submission_errors(workflow,tenant_id)
        return {'valid':not errors,'errors':errors,'workflow':workflow.model_dump(mode='json')}
    @app.get('/api/workflows')
    async def workflows(tenant_id:str=Depends(tenant)):return store.workflows(tenant_id)
    @app.post('/api/workflows',status_code=201)
    async def save(workflow:Workflow,tenant_id:str=Depends(tenant)):return store.save_workflow(safe_draft(workflow),tenant_id=tenant_id)
    @app.get('/api/workflows/{id}')
    async def workflow(id:str,tenant_id:str=Depends(tenant)):
        try:return store.workflow(id,tenant_id)
        except KeyError:raise HTTPException(404,'Workflow not found') from None
    @app.put('/api/workflows/{id}')
    async def update(id:str,workflow:WorkflowUpdate,tenant_id:str=Depends(tenant)):
        document=Workflow.model_validate(workflow.model_dump(exclude={'updated_at'}))
        try:return store.save_workflow(safe_draft(document),id,tenant_id,workflow.updated_at)
        except KeyError:raise HTTPException(404,'Workflow not found') from None
        except WorkflowConflict as exc:
            return JSONResponse(status_code=409,content={'detail':str(exc),'current':exc.current})
    @app.get('/api/models')
    async def allowed_models(tenant_id:str=Depends(tenant)):return store.models(tenant_id)
    @app.post('/api/models',status_code=201)
    async def allow_model(body:ModelRequest,tenant_id:str=Depends(tenant)):
        try:return store.allow_model(**body.model_dump(),tenant_id=tenant_id)
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
    @app.put('/api/models/{id}')
    async def update_model(id:str,body:ModelUpdate,tenant_id:str=Depends(tenant)):
        try:return store.set_model_enabled(id,body.enabled,tenant_id)
        except KeyError:raise HTTPException(404,'Model not found') from None
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
    @app.get('/api/credentials')
    async def credentials(tenant_id:str=Depends(tenant)):return store.credentials(tenant_id)
    @app.post('/api/credentials',status_code=201)
    async def credential(body:CredentialRequest,tenant_id:str=Depends(tenant)):return store.credential(body.name,body.secret,tenant_id,body.provider)
    @app.get('/api/runs')
    async def runs(tenant_id:str=Depends(tenant)):return store.runs(tenant_id)
    @app.post('/api/runs',status_code=201)
    async def start(body:RunRequest,tenant_id:str=Depends(tenant)):
        errors=await submission_errors(body.workflow,tenant_id)
        if errors:raise HTTPException(422,detail=errors)
        run=store.create_run(body.workflow.model_dump(mode='json'),body.message,tenant_id)
        return {'id':run['id'],'status':'queued'}
    @app.get('/api/runs/{id}')
    async def run(id:str,tenant_id:str=Depends(tenant)):return fetch_run(id,tenant_id)
    @app.post('/api/runs/{id}/cancel')
    async def cancel(id:str,tenant_id:str=Depends(tenant)):
        fetch_run(id,tenant_id)
        store.request_cancel(id,tenant_id)
        # Give a live worker a brief opportunity to acknowledge cancellation.
        for _ in range(10):
            status=store.run(id,tenant_id)['status']
            if status not in ('queued','running'):break
            await asyncio.sleep(.05)
        return {'status':store.run(id,tenant_id)['status']}
    @app.post('/api/runs/{id}/resume',status_code=202)
    async def resume(id:str,tenant_id:str=Depends(tenant)):
        previous=fetch_run(id,tenant_id)
        workflow=Workflow.model_validate(previous['workflow'])
        errors=store.model_errors(workflow,tenant_id)+platform_errors(store,workflow,tenant_id)+await kb_errors(app.state.knowledge_services,workflow,tenant_id,previous.get('checkpoints'),previous.get('vector_dependencies'))
        if errors:raise HTTPException(422,detail=errors)
        try:run=store.resume_run(id,tenant_id);return {'id':run['id'],'status':run['status']}
        except KeyError:raise HTTPException(404,'Run not found') from None
        except ValueError as exc:raise HTTPException(409,str(exc)) from None
    @app.get('/api/runs/{id}/events')
    async def events(id:str,request:Request,tenant_id:str=Depends(tenant)):
        fetch_run(id,tenant_id)
        async def stream():
            cursor=0
            while True:
                # A revoked/expired session must not keep an existing stream authorized.
                try:auth.tenant(request)
                except HTTPException:
                    yield 'event: done\ndata: {"status":"unauthorized"}\n\n';break
                run=fetch_run(id,tenant_id)
                for event in run['events'][cursor:]:
                    yield f'id: {event["seq"]}\nevent: node\ndata: {json.dumps(event)}\n\n';cursor+=1
                if run['status'] not in ('queued','running'):
                    yield f'event: done\ndata: {json.dumps({"status":run["status"]})}\n\n';break
                yield ': heartbeat\n\n';await asyncio.sleep(.15)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
    return app
