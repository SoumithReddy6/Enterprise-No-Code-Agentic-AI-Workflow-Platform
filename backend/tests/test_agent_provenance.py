"""Evidence an agent used stays authorized across API resume, lease recovery and cached replay."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from backend.app.main import create_app
from backend.app.models import Workflow, Node, Edge
from backend.app.registry import REGISTRY, QueryConfig
from backend.app.storage import JobRecord
from backend.app.worker import Worker
from backend.tests.test_kb_services_integration import services, populated  # noqa: F401 (fixture)


@pytest.fixture(params=['retrieve','query'])
def setup(tmp_path, monkeypatch, request, services):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    app = create_app(f'sqlite:///{tmp_path}/db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False, knowledge_services=services)
    store = app.state.store
    worker = Worker(store); worker.kbs = services
    def workflow(kb_id):
        return Workflow.model_validate({'name':'Evidence agent','nodes':[
            {'id':'input','type':'chat_input'},
            {'id':'agent','type':'agent','inputs':{'input':'input.message'}},
            {'id':'search','type':request.param,'config':{'knowledge_base_id':kb_id,'mode':'keyword'}},
            {'id':'out','type':'response','inputs':{'text':'agent.text'}}],
            'edges':[{'id':'a','source':'input','target':'agent'},{'id':'b','source':'agent','target':'out'},
                     {'id':'t','source':'search','target':'agent','kind':'tool'}]})
    responses = iter(['{"action":"call","target":"search","input":"codename"}', '{"action":"final","text":"Verified answer"}'])
    async def model(inputs,config,ctx):
        return {'text':'Evidence answer [S1]' if isinstance(config,QueryConfig) else next(responses),'provider':'demo'}
    monkeypatch.setattr('backend.app.registry.llm_node',model)
    async def fail(*args):raise ValueError('Temporary downstream failure')
    monkeypatch.setattr(REGISTRY['response'],'handler',fail)
    return app, worker, workflow


async def remove(services,kb,doc):
    await services.management.call('remove_document','local',{'kb_id':kb['id'],'document_id':doc['id']})


@pytest.mark.asyncio
@pytest.mark.parametrize('removed',[False,True])
async def test_agent_evidence_checked_on_api_resume(setup, services, removed, monkeypatch):
    app,worker,workflow = setup;store=app.state.store
    kb,doc,_=await populated(services);graph=workflow(kb['id'])
    run=store.create_run(graph.model_dump(), 'Question')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id'])
    assert saved['status']=='failed' and 'agent' in saved['checkpoints'] and 'search' not in saved['checkpoints']
    assert len(saved['vector_dependencies']['agent'])==1 and saved['vector_dependencies']['agent'][0]['knowledge_base_id']==kb['id']
    if removed:await remove(services,kb,doc)
    with TestClient(app) as client:
        response=client.post(f'/api/runs/{run["id"]}/resume')
    assert response.status_code == (422 if removed else 202), response.text
    if not removed:
        async def finish(inputs,config,ctx):return {'text':inputs['text'],'sources':'[]'}
        monkeypatch.setattr(REGISTRY['response'],'handler',finish)
        await worker.execute(store.claim_next(worker.owner))
        assert store.run(run['id'])['output']=='Verified answer'
        assert store.run(run['id'])['status']=='success'


@pytest.mark.asyncio
async def test_agent_evidence_checked_on_lease_recovery(setup, services):
    app,worker,workflow=setup;store=app.state.store
    kb,doc,_=await populated(services)
    run=store.create_run(workflow(kb['id']).model_dump(),'Question')
    await worker.execute(store.claim_next(worker.owner))
    store.resume_run(run['id'])
    store.claim_next('dead-worker')
    with Session(store.engine) as session:
        session.get(JobRecord,run['id']).lease_until=0;session.commit()
    await remove(services,kb,doc)
    await worker.execute(store.claim_next(worker.owner))
    result=store.run(run['id'])
    assert result['status']=='failed'
    assert 'sources are unavailable' in result['error'],result['error']
    assert not any(e.get('cached') and e.get('node_id')=='agent' for e in result['events'])


@pytest.mark.asyncio
async def test_agent_evidence_rechecked_immediately_before_cached_result(setup, services, monkeypatch):
    app,worker,workflow=setup;store=app.state.store
    kb,doc,_=await populated(services)
    run=store.create_run(workflow(kb['id']).model_dump(),'Question')
    await worker.execute(store.claim_next(worker.owner))
    store.resume_run(run['id'])
    original=store.worker_event
    def event(id,owner,value):
        # Evidence disappears after preflight passed but before the agent's cached result would be reused.
        if value.get('node_id')=='input' and value.get('cached'):
            services.management.call_sync('remove_document','local',{'kb_id':kb['id'],'document_id':doc['id']})
        return original(id,owner,value)
    monkeypatch.setattr(store,'worker_event',event)
    await worker.execute(store.claim_next(worker.owner))
    result=store.run(run['id'])
    assert 'sources are unavailable' in result['error'],result['error']
    assert not any(e.get('cached') and e.get('node_id')=='agent' for e in result['events'])


def test_vector_dependencies_are_deduplicated_bounded_and_fenced(tmp_path):
    from backend.app.storage import Store
    from backend.tests.test_platform_graph import platform_flow
    store=Store(f'sqlite:///{tmp_path}/deps.db',Fernet.generate_key());owner='worker'
    run=store.create_run(platform_flow().model_dump(),'Question')
    store.claim_next(owner)
    sources=[{'id':str(i)} for i in range(120)]
    store.record_vector_dependencies(run['id'],owner,'agent',sources)
    store.record_vector_dependencies(run['id'],owner,'agent',sources)
    assert len(store.run(run['id'])['vector_dependencies']['agent'])==120
    with pytest.raises(ValueError,match='limit'):
        store.record_vector_dependencies(run['id'],owner,'agent',[{'id':'overflow'}])
    with pytest.raises(ValueError,match='lease'):
        store.record_vector_dependencies(run['id'],'stale','agent',[])
    store.worker_event(run['id'],owner,{'node_id':'agent','status':'running'})
    assert 'agent' not in store.run(run['id'])['vector_dependencies']
    store.engine.dispose()


@pytest.mark.asyncio
async def test_specialist_evidence_belongs_to_root_checkpoint(setup, services, monkeypatch):
    app,worker,workflow=setup;store=app.state.store
    kb,doc,_=await populated(services);graph=workflow(kb['id'])
    graph.nodes.append(Node(id='helper',type='agent'))
    next(e for e in graph.edges if e.kind=='tool').target='helper'
    graph.edges.append(Edge(id='delegate',source='agent',target='helper',kind='agent'))
    responses=iter(['{"action":"call","target":"helper","input":"Question"}',
                    '{"action":"call","target":"search","input":"codename"}',
                    'Specialist result','Final result'])
    async def model(inputs,config,ctx):
        return {'text':'Evidence [S1]' if isinstance(config,QueryConfig) else next(responses),'provider':'demo'}
    monkeypatch.setattr('backend.app.registry.llm_node',model)
    run=store.create_run(graph.model_dump(),'Question')
    await worker.execute(store.claim_next(worker.owner))
    result=store.run(run['id'])
    assert set(result['vector_dependencies'])=={'agent'}
    assert result['checkpoints']['agent']['text']=='Final result'
    assert 'helper' not in result['checkpoints'] and 'search' not in result['checkpoints']
