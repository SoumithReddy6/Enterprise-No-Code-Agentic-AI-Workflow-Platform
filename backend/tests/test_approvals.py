import json
import httpx
import pytest
from cryptography.fernet import Fernet
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_compiler import sample

@pytest.fixture
def setup(tmp_path,monkeypatch):
    store=Store(f'sqlite:///{tmp_path}/approvals.db',Fernet.generate_key());worker=Worker(store)
    connection=worker.tools.save_connection({'name':'Example','provider':'http','endpoint':'https://example.com','secret':'PRIVATE_SECRET'})
    workflow=sample();workflow['nodes'][1]={'id':'send','type':'tool_http','config':{'connection_id':connection['id'],'method':'POST','path':'/reply','body':'{input}','enable_writes':True,'approval':True},'inputs':{'input':'input.message'}}
    workflow['nodes'][2]['inputs']={'text':'send.text'};workflow['edges'][0]['target']='send';workflow['edges'][1]['source']='send'
    calls=[]
    monkeypatch.setattr('backend.app.tool_service.public_addresses',lambda *args:['93.184.216.34'])
    worker.tools.transport=httpx.MockTransport(lambda request:(calls.append(request) or httpx.Response(200,text='sent')))
    return store,worker,workflow,calls

@pytest.mark.asyncio
async def test_pause_then_approve_exactly_once(setup):
    store,worker,workflow,calls=setup
    run=store.create_run(workflow,'customer reply')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='awaiting_approval' and calls==[]
    assert store.claim_next('other') is None
    approval=saved['approvals'][0]
    assert approval['payload']['url']=='https://example.com/reply'
    assert 'PRIVATE_SECRET' not in json.dumps(approval)
    from backend.app.approvals import decide
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run['id'])['status']=='success' and len(calls)==1
    assert json.loads(calls[0].content)=={'input':'customer reply'}
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    assert store.claim_next('other') is None

@pytest.mark.asyncio
@pytest.mark.parametrize('decision',['reject','expire','wrong_digest','foreign','cancel'])
async def test_denied_actions_never_send(setup,decision):
    from backend.app.approvals import decide,ApprovalRecord,expire_pending
    from sqlalchemy.orm import Session
    store,worker,workflow,calls=setup;run=store.create_run(workflow,'reply')
    await worker.execute(store.claim_next(worker.owner));approval=store.run(run['id'])['approvals'][0]
    if decision=='wrong_digest':
        with pytest.raises(ValueError,match='digest'):decide(store,run['id'],'local',approval['id'],'0'*64,'approve')
    elif decision=='foreign':
        with pytest.raises(KeyError):decide(store,run['id'],'foreign',approval['id'],approval['digest'],'approve')
    elif decision=='expire':
        with Session(store.engine) as s:s.get(ApprovalRecord,approval['id']).expires_at=0;s.commit()
        expire_pending(store)
        with pytest.raises(ValueError):decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    elif decision=='cancel':store.request_cancel(run['id'])
    else:decide(store,run['id'],'local',approval['id'],approval['digest'],'reject')
    assert calls==[] and store.claim_next('next') is None
    if decision in ('reject','expire','cancel'):
        assert store.run(run['id'])['status']=='cancelled'
        with pytest.raises(ValueError):store.resume_run(run['id'])

@pytest.mark.asyncio
async def test_changed_connection_and_uncertain_delivery(setup,monkeypatch):
    from backend.app.approvals import decide
    store,worker,workflow,calls=setup;run=store.create_run(workflow,'reply')
    await worker.execute(store.claim_next(worker.owner));approval=store.run(run['id'])['approvals'][0]
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    connection=worker.tools.connections()[0]
    worker.tools.save_connection({'name':'Changed','provider':'http','endpoint':'https://changed.example.com'},id=connection['id'])
    await worker.execute(store.claim_next(worker.owner))
    assert calls==[] and store.run(run['id'])['status']=='failed'
    # A fresh approved run that loses its response must retain the write marker.
    run=store.create_run(workflow,'reply');await worker.execute(store.claim_next(worker.owner));approval=store.run(run['id'])['approvals'][0]
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    async def fail(*args):raise ValueError('delivery response lost')
    monkeypatch.setattr(worker.tools,'execute_prepared',fail)
    await worker.execute(store.claim_next(worker.owner))
    assert 'uncertain' in store.run(run['id'])['error'].lower()
    with pytest.raises(ValueError,match='external write'):store.resume_run(run['id'])

@pytest.mark.asyncio
async def test_agent_resumes_selected_action_without_replanning(setup,monkeypatch):
    from backend.app.approvals import decide
    from backend.tests.test_agent_tools import calc_flow,scripted
    store,worker,workflow,calls=setup;graph=calc_flow()
    graph.nodes[2].type='tool_http';graph.nodes[2].config=workflow['nodes'][1]['config']
    seen=[];scripted(monkeypatch,['{"action":"call","target":"calc","input":"reviewed reply"}','{"action":"final","text":"Sent"}'],seen)
    run=store.create_run(graph.model_dump(),'send')
    await worker.execute(store.claim_next(worker.owner));saved=store.run(run['id'])
    assert saved['status']=='awaiting_approval' and len(seen)==1
    approval=saved['approvals'][0];decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run['id'])['status']=='success' and len(calls)==1 and len(seen)==2
    assert json.loads(calls[0].content)=={'input':'reviewed reply'}

@pytest.mark.asyncio
async def test_query_parameters_are_visible_and_sent(setup):
    from backend.app.approvals import decide
    store,worker,workflow,calls=setup
    workflow['nodes'][1]['config']['path']='/reply?recipient=other%40example.com&send=true'
    run=store.create_run(workflow,'reply');await worker.execute(store.claim_next(worker.owner))
    approval=store.run(run['id'])['approvals'][0]
    assert approval['payload']['url'].endswith(workflow['nodes'][1]['config']['path'])
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    await worker.execute(store.claim_next(worker.owner))
    assert calls[0].url.query==b'recipient=other%40example.com&send=true'

@pytest.mark.asyncio
async def test_concurrent_approve_is_idempotent(setup):
    from concurrent.futures import ThreadPoolExecutor
    from backend.app.approvals import decide
    store,worker,workflow,calls=setup
    run=store.create_run(workflow,'reply');await worker.execute(store.claim_next(worker.owner))
    approval=store.run(run['id'])['approvals'][0]
    def submit(_):return decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(r['status']=='approved' for r in pool.map(submit,range(4)))
    claim=store.claim_next(worker.owner);assert store.claim_next('other') is None
    await worker.execute(claim)
    assert len(calls)==1 and store.run(run['id'])['status']=='success'

@pytest.mark.asyncio
async def test_expiry_after_claim_cancels_cleanly(setup):
    from backend.app.approvals import decide,ApprovalRecord
    from sqlalchemy.orm import Session
    store,worker,workflow,calls=setup
    run=store.create_run(workflow,'reply');await worker.execute(store.claim_next(worker.owner))
    approval=store.run(run['id'])['approvals'][0]
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    claim=store.claim_next(worker.owner)
    with Session(store.engine) as s:s.get(ApprovalRecord,approval['id']).expires_at=0;s.commit()
    await worker.execute(claim)
    saved=store.run(run['id'])
    assert saved['status']=='cancelled' and saved['approval_terminal'] and calls==[]
    assert saved['approvals'][0]['status']=='expired'

@pytest.mark.asyncio
async def test_nested_agent_frames_consumed_between_delegations(setup,monkeypatch):
    from backend.app.approvals import decide
    from backend.app.models import Workflow
    from backend.tests.test_agent_tools import calc_flow,scripted
    store,worker,workflow,calls=setup;raw=calc_flow().model_dump()
    raw['nodes'][2].update(type='tool_http',config=workflow['nodes'][1]['config'])
    raw['nodes'].append({'id':'specialist','type':'agent','config':{'provider':'demo'}})
    raw['edges'][-1]['target']='specialist'
    raw['edges'].append({'id':'delegate','source':'agent','target':'specialist','kind':'agent','sourceHandle':'agents','targetHandle':'input'})
    graph=Workflow.model_validate(raw);seen=[]
    scripted(monkeypatch,[
        '{"action":"call","target":"specialist","input":"first"}',
        '{"action":"call","target":"calc","input":"first reply"}',
        '{"action":"final","text":"first sent"}',
        '{"action":"call","target":"specialist","input":"second"}',
        '{"action":"call","target":"calc","input":"second reply"}',
        '{"action":"final","text":"second sent"}',
        '{"action":"final","text":"Both sent"}',
    ],seen)
    run=store.create_run(graph.model_dump(),'send twice');await worker.execute(store.claim_next(worker.owner))
    for index,text in enumerate(('first reply','second reply')):
        saved=store.run(run['id']);assert saved['status']=='awaiting_approval'
        approval=next(a for a in saved['approvals'] if a['status']=='pending')
        assert approval['payload']['body']=={'input':text}
        assert len(calls)==index
        decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
        await worker.execute(store.claim_next(worker.owner))
    assert store.run(run['id'])['status']=='success' and len(calls)==2 and len(seen)==7

@pytest.mark.asyncio
@pytest.mark.parametrize('required,override,method,paused',[(True,None,'POST',True),(False,None,'POST',False),(True,False,'POST',False),(True,None,'GET',False)])
async def test_approval_defaults_only_for_writes(setup,required,override,method,paused):
    store,worker,workflow,calls=setup
    workflow['nodes'][1]['config'].update(approval=override,method=method)
    run=store.create_run(workflow,'reply',approval_required=required)
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run['id'])['status']==('awaiting_approval' if paused else 'success')
    assert len(calls)==(0 if paused else 1)

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,config',[
    ('email',{'to':'customer@example.com','subject':'Review reply','body':'Hi {input}'}),
    ('github',{'operation':'create_issue','repository':'owner/repo','title':'Review'}),
    ('jira',{'operation':'comment','issue_key':'TEST-1'}),
    ('confluence',{'operation':'create_page','space':'TEST','title':'Review'}),
])
async def test_all_write_adapters_send_the_reviewed_body(setup,monkeypatch,kind,config):
    from backend.app.approvals import decide
    store,worker,workflow,calls=setup
    connection=worker.tools.save_connection({'name':kind,'provider':kind,'endpoint':'smtp.example.com' if kind=='email' else 'https://example.com','sender':'sender@example.com'})
    workflow['nodes'][1]['type']='tool_'+kind
    workflow['nodes'][1]['config']={**config,'connection_id':connection['id'],'enable_writes':True,'approval':True}
    emails=[]
    def email(c,config,text,prepared=False):
        emails.append((config.to,config.subject,config.body,prepared));return 'sent'
    monkeypatch.setattr(worker.tools,'_email',email)
    run=store.create_run(workflow,'literal {input} 🌍');await worker.execute(store.claim_next(worker.owner))
    approval=store.run(run['id'])['approvals'][0];assert calls==[] and emails==[]
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run['id'])['status']=='success'
    if kind=='email':assert emails==[('customer@example.com','Review reply',approval['payload']['body'],True)]
    else:assert json.loads(calls[0].content)==approval['payload']['body']

@pytest.mark.asyncio
async def test_approval_api_digest_tenant_and_rate_limit(tmp_path,monkeypatch):
    from backend.app.main import create_app
    from fastapi.testclient import TestClient
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','open')
    app=create_app(database_url=f'sqlite:///{tmp_path}/api.db',encryption_key=Fernet.generate_key(),embedded_worker=False)
    with TestClient(app) as client:
        assert client.post('/api/runs/missing/approve',json={'approval_id':'x','digest':'0'*64}).status_code==401
        owner=client.post('/api/auth/register',json={'email':'owner@example.com','password':'test-password-123'}).json()['tenant_id']
        store=app.state.store;worker=Worker(store)
        connection=worker.tools.save_connection({'name':'http','provider':'http','endpoint':'https://example.com'},tenant_id=owner)
        graph=sample();graph['nodes'][1]={'id':'send','type':'tool_http','config':{'connection_id':connection['id'],'method':'POST','enable_writes':True},'inputs':{'input':'input.message'}}
        graph['nodes'][2]['inputs']={'text':'send.text'};graph['edges'][0]['target']='send';graph['edges'][1]['source']='send'
        created=client.post('/api/runs',json={'workflow':graph,'message':'reply'});assert created.status_code==201
        id=created.json()['id'];await worker.execute(store.claim_next(worker.owner))
        approval=client.get(f'/api/runs/{id}').json()['approvals'][0]
        body={'approval_id':approval['id'],'digest':approval['digest']}
        assert client.post(f'/api/runs/{id}/approve',json={**body,'digest':'0'*64}).status_code==409
        assert client.post(f'/api/runs/{id}/approve',json=body).status_code==200
        assert client.post(f'/api/runs/{id}/approve',json=body).status_code==200
        client.post('/api/auth/register',json={'email':'other@example.com','password':'test-password-123'})
        assert client.post(f'/api/runs/{id}/approve',json=body).status_code==404
        for _ in range(29):assert client.post(f'/api/runs/{id}/approve',json=body).status_code==404
        assert client.post(f'/api/runs/{id}/approve',json=body).status_code==429
