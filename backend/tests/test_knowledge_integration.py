import json
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.worker import Worker
from backend.app.registry import REGISTRY
from backend.tests.test_knowledge_runtime import pdf_flow
from backend.tests.test_knowledge import text_pdf

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f'sqlite:///{tmp_path}/integrated.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False)) as c:yield c

async def uploaded(c):
    base=c.post('/api/knowledge',json={'name':'My PDFs'}).json()
    doc=c.post(f'/api/knowledge/{base["id"]}/documents?filename=facts.pdf',content=text_pdf(),headers={'content-type':'application/pdf'}).json()
    assert await c.app.state.knowledge.process_once()
    graph=pdf_flow();graph.nodes[1].config['knowledge_base_id']=base['id']
    return base,doc,graph

@pytest.mark.asyncio
async def test_actual_upload_retrieval_worker_and_removed_source_resume(client,monkeypatch):
    base,doc,graph=await uploaded(client)
    async def fail(*args):raise ValueError('Retry me')
    original=REGISTRY['response'].handler
    monkeypatch.setattr(REGISTRY['response'],'handler',fail)
    started=client.post('/api/runs',json={'workflow':graph.model_dump(),'message':'What contains Caloris?'} )
    assert started.status_code==201,started.text
    store=client.app.state.store;worker=Worker(store)
    await worker.execute(store.claim_next(worker.owner))
    run=store.run(started.json()['id'])
    assert run['status']=='failed' and run['error']=='Retry me'
    sources=json.loads(run['checkpoints']['answer']['sources'])
    assert sources[0]['document_id']==doc['id'] and sources[0]['page']==1
    monkeypatch.setattr(REGISTRY['response'],'handler',original)
    client.post(f'/api/documents/{doc["id"]}/remove')
    assert client.post(f'/api/runs/{run["id"]}/resume').status_code==422
    # A direct queued worker path must apply the same cached-source guard.
    store.resume_run(run['id'])
    await worker.execute(store.claim_next(worker.owner))
    assert 'saved source' in store.run(run['id'])['error']
    assert client.get(f'/api/documents/{doc["id"]}/file').status_code==404

@pytest.mark.asyncio
async def test_grounded_answer_cannot_bypass_model_catalog(client):
    _,_,graph=await uploaded(client)
    graph.nodes[2].config={'provider':'ollama','model':'unapproved'}
    result=client.post('/api/runs',json={'workflow':graph.model_dump()})
    assert result.status_code==422 and 'not enabled' in result.text


def test_knowledge_resources_follow_first_account_ownership_and_scope(tmp_path):
    from backend.app.storage import Store
    from backend.app.knowledge import Knowledge
    url=f'sqlite:///{tmp_path}/owners.db';key=Fernet.generate_key()
    store=Store(url,key);knowledge=Knowledge(store)
    base=knowledge.create('Legacy');doc=knowledge.upload(base['id'],'legacy.pdf',text_pdf())
    claim=knowledge.claim();knowledge.publish(*claim,['Caloris belongs to Mercury.'])
    store.engine.dispose()
    with TestClient(create_app(url,key,embedded_worker=False)) as c:
        assert c.get('/api/knowledge').status_code==401
        c.post('/api/auth/register',json={'email':'first@example.com','password':'first-password-123'})
        assert c.get('/api/knowledge').json()==[base]
        assert c.get(f'/api/documents/{doc["id"]}/file').status_code==200
        c.post('/api/auth/logout')
        c.post('/api/auth/register',json={'email':'second@example.com','password':'second-password-123'})
        assert c.get('/api/knowledge').json()==[]
        for path in (f'/api/knowledge/{base["id"]}/documents', f'/api/documents/{doc["id"]}/file'):
            assert c.get(path).status_code==404
        graph=pdf_flow();graph.nodes[1].config['knowledge_base_id']=base['id']
        assert c.post('/api/runs',json={'workflow':graph.model_dump()}).status_code==422
        assert c.post(f'/api/documents/{doc["id"]}/remove').status_code==404

@pytest.mark.asyncio
async def test_removal_after_resume_starts_still_blocks_cached_sources(client,monkeypatch):
    _,doc,graph=await uploaded(client)
    async def fail(*args):raise ValueError('Retry me')
    monkeypatch.setattr(REGISTRY['response'],'handler',fail)
    started=client.post('/api/runs',json={'workflow':graph.model_dump(),'message':'Caloris'}).json()
    store=client.app.state.store;worker=Worker(store)
    await worker.execute(store.claim_next(worker.owner))
    store.resume_run(started['id'])
    original_event=store.worker_event
    def remove_during_cached_input(id,owner,event):
        saved=original_event(id,owner,event)
        if event.get('cached') and event.get('node_id')=='input':client.app.state.knowledge.remove(doc['id'])
        return saved
    monkeypatch.setattr(store,'worker_event',remove_during_cached_input)
    await worker.execute(store.claim_next(worker.owner))
    assert 'saved source' in store.run(started['id'])['error']
