import json
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.worker import Worker
from backend.app.models import Workflow
from backend.tests.test_platform_graph import platform_flow

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('VECTOR_DATA_DIR',str(tmp_path/'vectors'))
    with TestClient(create_app(f'sqlite:///{tmp_path}/platform.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False)) as c:yield c


def test_agent_model_permission_enforced_with_role_configuration(client):
    graph=platform_flow();graph.nodes[1].config={'provider':'ollama','model':'unapproved','role':'planner'}
    r=client.post('/api/runs',json={'workflow':graph.model_dump()})
    assert r.status_code==422 and 'not enabled' in r.text

@pytest.mark.asyncio
async def test_transient_tool_results_never_become_flow_checkpoints(client,monkeypatch):
    from backend.app import registry
    graph=platform_flow();graph.nodes.pop();graph.edges=graph.edges[:2]
    graph.nodes.append(type(graph.nodes[0])(id='python',type='tool_python',config={'code':'print(input_text)'}))
    graph.edges.append(type(graph.edges[0])(id='attach',source='python',target='agent',kind='tool',targetHandle='tools'))
    responses=iter(['{"action":"call","target":"python","input":"Hello"}','{"action":"final","text":"Done"}'])
    async def llm(*args):return {'text':next(responses),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',llm)
    worker=Worker(client.app.state.store)
    async def tool(*args):return 'Hello'
    monkeypatch.setattr(worker.tools,'execute',tool)
    created=client.post('/api/runs',json={'workflow':graph.model_dump()});assert created.status_code==201,created.text
    await worker.execute(worker.store.claim_next(worker.owner))
    run=worker.store.run(created.json()['id'])
    assert run['status']=='success' and run['output']=='Done'
    assert 'python' not in run['checkpoints']
    assert any(e.get('node_id')=='python' and e.get('transient') for e in run['events'])


def write_flow(connection_id):
    return Workflow.model_validate({'version':1,'name':'Write','nodes':[{'id':'input','type':'chat_input'},
     {'id':'tool','type':'tool_http','inputs':{'input':'input.message'},'config':{'connection_id':connection_id,'method':'POST','enable_writes':True}},
     {'id':'out','type':'response','inputs':{'text':'tool.text'}}],
     'edges':[{'id':'a','source':'input','target':'tool'},{'id':'b','source':'tool','target':'out'}]})

@pytest.mark.asyncio
async def test_interrupted_write_cannot_be_replayed_on_resume_or_lease_recovery(client,monkeypatch):
    connection=client.post('/api/connections',json={'name':'API','provider':'http','endpoint':'https://example.com'}).json()
    graph=write_flow(connection['id']);store=client.app.state.store;worker=Worker(store);calls=[]
    async def interrupted(*args):calls.append('write');raise ValueError('Uncertain external outcome')
    monkeypatch.setattr(worker.tools,'execute',interrupted)
    created=client.post('/api/runs',json={'workflow':graph.model_dump()}).json()
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(created['id'])['write_nodes']==['tool']
    assert client.post(f'/api/runs/{created["id"]}/resume').status_code==409
    second=store.create_run(graph.model_dump(),'')
    store.claim_next('expired');store.mark_write(second['id'],'expired','tool')
    from sqlalchemy.orm import Session
    from backend.app.storage import JobRecord
    with Session(store.engine) as s:s.get(JobRecord,second['id']).lease_until=0;s.commit()
    job=store.claim_next(worker.owner);await worker.execute(job)
    assert calls==['write']
    assert 'external write' in store.run(second['id'])['error']


def test_memory_is_workspace_scoped_and_bounded(client):
    from backend.app.agent_memory import MemoryService
    memory=MemoryService(client.app.state.store)
    for _ in range(12):memory.write('conversation','x'*3000,'y'*3000,'one')
    assert memory.read('conversation','two')=='[]'
    assert len(memory.read('conversation','one'))<=16000

@pytest.mark.asyncio
async def test_agent_write_failure_stops_and_keeps_resume_blocked(client,monkeypatch):
    from backend.tests.test_agent_tools import calc_flow,scripted
    connection=client.post('/api/connections',json={'name':'API','provider':'http','endpoint':'https://example.com'}).json()
    graph=calc_flow();graph.nodes[2].type='tool_http'
    graph.nodes[2].config={'method':'POST','enable_writes':True,'connection_id':connection['id']}
    scripted(monkeypatch,['{"action":"call","target":"calc","input":"create"}']*3)
    store=client.app.state.store;worker=Worker(store);attempts=[]
    async def uncertain(*args):
        attempts.append(1)
        raise ValueError('Remote accepted request; connection lost')
    monkeypatch.setattr(worker.tools,'execute',uncertain)
    response=client.post('/api/runs',json={'workflow':graph.model_dump()})
    assert response.status_code==201,response.text
    id=response.json()['id']
    await worker.execute(store.claim_next(worker.owner))
    run=store.run(id)
    assert attempts==[1] and run['status']=='failed'
    assert 'reconciliation' in run['error'] and run['write_nodes']==['agent']
    assert 'agent' not in run['checkpoints']
    assert client.post(f'/api/runs/{id}/resume').status_code==409
