import asyncio
import json
import pytest
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.kb.rpc import app_for

def test_rpc_authentication_and_allowlist():
    class Domain:
        def call(self,action,tenant,payload):return {'tenant':tenant,'result':payload['value']}
    key=Fernet.generate_key().decode();client=TestClient(app_for(Domain(),'test',{'echo'},key=key))
    body={'action':'echo','tenant':'alice','payload':{'value':12}}
    assert client.post('/rpc',json=body).status_code==401
    assert client.post('/rpc',json=body,headers={'X-Relay-Service-Key':key}).json()=={'tenant':'alice','result':12}
    body['action']='__dict__'
    assert client.post('/rpc',json=body,headers={'X-Relay-Service-Key':key}).status_code==400

@pytest.mark.asyncio
async def test_ingestion_failure_is_recorded_and_schedules_cleanup(tmp_path):
    from backend.app.kb.ingestion import Ingestion
    class Manager:
        def __init__(self):self.actions=[]
        async def call(self,action,tenant='local',payload=None):
            self.actions.append((action,payload))
            if action=='artifact':return {'filename':'bad.txt','content_b64':'bm90IHVzZWQ='}
            if action=='cleanup_list':return [{'kb_id':'kb','tenant_id':'t','segment_id':'attempt'}]
            return {'ok':True}
    class Search:
        def __init__(self):self.actions=[]
        async def call(self,action,tenant='local',payload=None):self.actions.append(action);return {'ok':True}
    manager=Manager();search=Search();worker=Ingestion(manager,search)
    async def fail(*args):raise ValueError('Chunking failed')
    worker.extract=fail
    await worker.execute({'id':'j','tenant_id':'t','kb_id':'kb','attempt_id':'attempt','version':1,'config':{},'documents':[{'id':'d','filename':'bad.txt'}]})
    assert any(a=='fail' and 'Chunking failed' in p['error'] for a,p in manager.actions)
    assert 'build' not in search.actions
    await worker.cleanup()
    assert 'cleanup' in search.actions
    assert any(a=='cleanup_result' and p['ok'] for a,p in manager.actions)

@pytest.mark.asyncio
async def test_embedding_fingerprint_drift_prevents_call(monkeypatch):
    from backend.app.kb.embedding import Embeddings
    e=Embeddings()
    async def digest(*args):return 'new'
    monkeypatch.setattr(e,'fingerprint',digest)
    with pytest.raises(ValueError,match='changed'):await e.embed('model',['text'],'old')

@pytest.mark.asyncio
async def test_chat_models_cannot_be_configured_as_embedding_models(monkeypatch):
    from backend.app import providers
    from backend.app.kb.embedding import Embeddings
    def handler(request):
        if request.url.path=='/api/tags':return httpx.Response(200,json={'models':[{'name':'chatty','digest':'abc'},{'name':'embedder','digest':'def'},{'name':'old','digest':'ghi'}]})
        name=json.loads(request.content)['model']
        return httpx.Response(200,json={'capabilities':['completion','tools'] if name=='chatty' else ['embedding']} if name!='old' else {})
    real=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:real(transport=httpx.MockTransport(handler)))
    e=Embeddings()
    with pytest.raises(ValueError,match='chat model'):await e.fingerprint('chatty',require_embedding=True)
    assert await e.fingerprint('chatty')=='abc'  # An existing index keeps answering with the model it was built with.
    assert await e.fingerprint('embedder',require_embedding=True)=='def'
    assert await e.fingerprint('old',require_embedding=True)=='ghi'  # Servers without capability data are not blocked.
    with pytest.raises(ValueError,match='installed'):await e.fingerprint('absent')

@pytest.mark.asyncio
async def test_run_deadline_includes_service_preflight(tmp_path,monkeypatch):
    from backend.app import worker as module
    from backend.app.storage import Store
    from backend.tests.test_platform_graph import platform_flow
    store=Store(f'sqlite:///{tmp_path}/run.db',Fernet.generate_key())
    worker=module.Worker(store)
    async def stalled(*args):await asyncio.sleep(3);return []
    monkeypatch.setattr(module,'kb_errors',stalled)
    monkeypatch.setattr(module,'RUN_TIMEOUT_SECONDS',.01)
    created=store.create_run(platform_flow().model_dump(),'Hello')
    await worker.execute(store.claim_next(worker.owner))
    result=store.run(created['id'])
    assert result['status']=='failed' and 'timed out' in result['error']
    store.engine.dispose()
