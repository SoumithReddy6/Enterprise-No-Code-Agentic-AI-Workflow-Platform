import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from backend.app.main import create_app
from backend.app.models import Workflow
from backend.app.registry import REGISTRY, QueryConfig
from backend.app.storage import JobRecord
from backend.app.vector_service import VectorFile, VectorChunk
from backend.app.worker import Worker


@pytest.fixture(params=['retrieve','query'])
def setup(tmp_path, monkeypatch, request):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    app = create_app(f'sqlite:///{tmp_path}/db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    store = app.state.store
    vectors = app.state.vectors
    resource = vectors.create({'name':'Evidence','backend':'faiss','embedding_model':'mock'})
    with Session(store.engine) as session:
        document = VectorFile(id='doc',tenant_id='local',base_id=resource['id'],filename='evidence.txt',content=b'evidence',status='ready')
        chunk = VectorChunk(id='chunk',tenant_id='local',base_id=resource['id'],document_id='doc',ordinal=0,page=1,text='Verified evidence')
        session.add_all([document,chunk]);session.commit()
        sources = [vectors._source(chunk,document,1.)]
    workflow = Workflow.model_validate({'name':'Evidence agent','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'agent','type':'agent','inputs':{'input':'input.message'}},
        {'id':'search','type':request.param},
        {'id':'vector','type':'vector_faiss','config':{'resource_id':resource['id']}},
        {'id':'out','type':'response','inputs':{'text':'agent.text'}}],
        'edges':[{'id':'a','source':'input','target':'agent'},{'id':'b','source':'agent','target':'out'},
                 {'id':'t','source':'search','target':'agent','kind':'tool'},
                 {'id':'s','source':'vector','target':'search','kind':'store'}]})
    worker = Worker(store)
    async def retrieve(*args):return vectors.verify_sources(sources)
    monkeypatch.setattr(worker.vectors,'retrieve',retrieve)
    responses = iter(['{"action":"call","target":"search","input":"evidence"}', '{"action":"final","text":"Verified answer"}'])
    async def model(inputs,config,ctx):
        return {'text':'Evidence answer [S1]' if isinstance(config,QueryConfig) else next(responses),'provider':'demo'}
    monkeypatch.setattr('backend.app.registry.llm_node',model)
    async def fail(*args):raise ValueError('Temporary downstream failure')
    monkeypatch.setattr(REGISTRY['response'],'handler',fail)
    return app, worker, workflow


@pytest.mark.asyncio
@pytest.mark.parametrize('removed',[False,True])
async def test_agent_evidence_checked_on_api_resume(setup, removed,monkeypatch):
    app,worker,workflow = setup;store=app.state.store
    run=store.create_run(workflow.model_dump(), 'Question')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id'])
    assert 'agent' in saved['checkpoints'] and 'search' not in saved['checkpoints']
    assert [s['id'] for s in saved['vector_dependencies']['agent']]==['chunk']
    if removed:app.state.vectors.remove('doc')
    with TestClient(app) as client:
        response=client.post(f'/api/runs/{run["id"]}/resume')
    assert response.status_code == (422 if removed else 202), response.text
    if not removed:
        async def finish(inputs,config,ctx):return {'text':inputs['text']}
        monkeypatch.setattr(REGISTRY['response'],'handler',finish)
        await worker.execute(store.claim_next(worker.owner))
        assert store.run(run['id'])['output']=='Verified answer'
        assert store.run(run['id'])['status']=='success'


@pytest.mark.asyncio
async def test_agent_evidence_checked_on_lease_recovery(setup):
    app,worker,workflow=setup;store=app.state.store
    run=store.create_run(workflow.model_dump(),'Question')
    await worker.execute(store.claim_next(worker.owner))
    store.resume_run(run['id'])
    store.claim_next('dead-worker')
    with Session(store.engine) as session:
        session.get(JobRecord,run['id']).lease_until=0;session.commit()
    app.state.vectors.remove('doc')
    await worker.execute(store.claim_next(worker.owner))
    result=store.run(run['id'])
    assert result['status']=='failed'
    assert 'saved sources' in result['error'],result['error']
    assert not any(e.get('cached') and e.get('node_id')=='agent' for e in result['events'])


@pytest.mark.asyncio
async def test_agent_evidence_rechecked_immediately_before_cached_result(setup,monkeypatch):
    app,worker,workflow=setup;store=app.state.store
    run=store.create_run(workflow.model_dump(),'Question')
    await worker.execute(store.claim_next(worker.owner))
    store.resume_run(run['id'])
    original=store.worker_event
    def event(id,owner,value):
        if value.get('node_id')=='input' and value.get('cached'):app.state.vectors.remove('doc')
        return original(id,owner,value)
    monkeypatch.setattr(store,'worker_event',event)
    await worker.execute(store.claim_next(worker.owner))
    result=store.run(run['id'])
    assert 'saved sources' in result['error'],result['error']
    assert not any(e.get('cached') and e.get('node_id')=='agent' for e in result['events'])


def test_vector_dependencies_are_deduplicated_bounded_and_fenced(setup):
    app,worker,workflow=setup;store=app.state.store
    run=store.create_run(workflow.model_dump(),'Question')
    store.claim_next(worker.owner)
    sources=[{'id':str(i)} for i in range(120)]
    store.record_vector_dependencies(run['id'],worker.owner,'agent',sources)
    store.record_vector_dependencies(run['id'],worker.owner,'agent',sources)
    assert len(store.run(run['id'])['vector_dependencies']['agent'])==120
    with pytest.raises(ValueError,match='limit'):
        store.record_vector_dependencies(run['id'],worker.owner,'agent',[{'id':'overflow'}])
    with pytest.raises(ValueError,match='lease'):
        store.record_vector_dependencies(run['id'],'stale','agent',[])
    store.worker_event(run['id'],worker.owner,{'node_id':'agent','status':'running'})
    assert 'agent' not in store.run(run['id'])['vector_dependencies']


@pytest.mark.asyncio
async def test_specialist_evidence_belongs_to_root_checkpoint(setup,monkeypatch):
    from backend.app.models import Node,Edge
    app,worker,workflow=setup;store=app.state.store
    workflow.nodes.append(Node(id='helper',type='agent'))
    next(e for e in workflow.edges if e.kind=='tool').target='helper'
    workflow.edges.append(Edge(id='delegate',source='agent',target='helper',kind='agent'))
    responses=iter(['{"action":"call","target":"helper","input":"Question"}',
                    '{"action":"call","target":"search","input":"Evidence"}',
                    'Specialist result','Final result'])
    async def model(inputs,config,ctx):
        return {'text':'Evidence [S1]' if isinstance(config,QueryConfig) else next(responses),'provider':'demo'}
    monkeypatch.setattr('backend.app.registry.llm_node',model)
    run=store.create_run(workflow.model_dump(),'Question')
    await worker.execute(store.claim_next(worker.owner))
    result=store.run(run['id'])
    assert set(result['vector_dependencies'])=={'agent'}
    assert result['checkpoints']['agent']['text']=='Final result'
    assert 'helper' not in result['checkpoints'] and 'search' not in result['checkpoints']
