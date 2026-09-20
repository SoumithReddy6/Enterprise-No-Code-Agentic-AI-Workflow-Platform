import asyncio
import json
import logging
import time
from types import SimpleNamespace
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.tests.test_compiler import sample

class HealthyService:
    async def health(self):return {'status':'ok'}

@pytest.fixture
def app(tmp_path):
    services=SimpleNamespace(management=HealthyService(),index=HealthyService())
    return create_app(f'sqlite:///{tmp_path}/app.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False,knowledge_services=services)

def test_ready_checks_dependencies_and_worker(app):
    with TestClient(app) as client:
        assert client.get('/api/health').status_code==200
        result=client.get('/api/ready')
        assert result.status_code==503
        assert result.json()['checks']['worker']['status']=='unavailable'
        app.state.store.worker_seen('idle-worker')
        assert client.get('/api/ready').status_code==200
        async def down():raise ConnectionError('SECRET connection info')
        app.state.knowledge_services.index.health=down
        result=client.get('/api/ready')
        assert result.status_code==503 and result.json()['checks']['search']['status']=='unavailable'
        assert 'SECRET' not in result.text

def test_request_ids_echo_generate_and_log_without_query_secrets(app,caplog):
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        response=client.get('/api/health?token=SECRET',headers={'X-Request-ID':'client-42'})
        assert response.headers['X-Request-ID']=='client-42'
        generated=client.get('/api/health').headers['X-Request-ID']
        assert generated and generated!='client-42'
    records=[json.loads(r.message) for r in caplog.records if r.name=='relay.journal']
    assert any(r['event']=='http.request' and r['request_id']=='client-42' and r['status']==200 for r in records)
    assert 'SECRET' not in json.dumps(records)

def test_operator_metrics_never_open_when_auth_disabled(app):
    with TestClient(app) as client:assert client.get('/api/operator/metrics').status_code==401

@pytest.mark.asyncio
async def test_originating_request_id_reaches_run_and_worker_journal(app,caplog):
    from backend.app.worker import Worker
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        response=client.post('/api/runs',json={'workflow':sample(),'message':'PRIVATE_PASSAGE'},headers={'X-Request-ID':'submission-7'})
        assert response.status_code==201
        worker=Worker(app.state.store)
        await worker.execute(app.state.store.claim_next(worker.owner))
    records=[json.loads(r.message) for r in caplog.records if r.name=='relay.journal']
    start=next(r for r in records if r['event']=='run.start')
    assert start['request_id']=='submission-7' and start['run_id']==response.json()['id']
    assert any(r['event']=='http.request' and r['request_id']=='submission-7' for r in records)
    assert 'PRIVATE_PASSAGE' not in json.dumps(records)

def test_login_outcomes_are_structured_without_secrets(tmp_path,caplog,monkeypatch):
    from backend.app import auth
    caplog.set_level(logging.INFO)
    app=create_app(f'sqlite:///{tmp_path}/secure.db',Fernet.generate_key(),embedded_worker=False)
    password='PRIVATE_PASSWORD_123';email='private@example.com'
    with TestClient(app) as client:
        client.post('/api/auth/register',json={'email':email,'password':password})
        assert client.post('/api/auth/login',json={'email':email,'password':password}).status_code==200
        clock=[time.time()];monkeypatch.setattr(auth.time,'time',lambda:clock[0])
        for i in range(10):
            clock[0]+=32
            assert client.post('/api/auth/login',json={'email':email,'password':'PRIVATE_WRONG'}).status_code==401
        assert client.post('/api/auth/login',json={'email':email,'password':password}).status_code==429
    records=[json.loads(r.message) for r in caplog.records if r.name=='relay.journal']
    assert any(r['event']=='auth.login_success' for r in records)
    assert any(r['event']=='auth.lockout' for r in records)
    assert all(secret not in json.dumps(records) for secret in [password,email,'PRIVATE_WRONG'])

@pytest.mark.asyncio
async def test_provider_retry_logs_metadata_not_exception_content(caplog):
    from backend.app.providers import ProviderBusy,with_retries
    caplog.set_level(logging.INFO)
    async def fails():raise ProviderBusy('PRIVATE_PROVIDER_PASSAGE')
    with pytest.raises(ValueError):await with_retries(fails,attempts=2,base_delay=0)
    records=[json.loads(r.message) for r in caplog.records if r.name=='relay.journal']
    assert any(r['event']=='provider.retry' for r in records)
    assert 'PRIVATE_PROVIDER_PASSAGE' not in '\n'.join(r.message for r in caplog.records)


def test_database_failure_does_not_break_liveness(app,monkeypatch):
    import backend.app.readiness as probes
    def unavailable(store):raise ConnectionError('PRIVATE_DB_PASSWORD')
    monkeypatch.setattr(probes,'database_probe',unavailable)
    with TestClient(app) as client:
        result=client.get('/api/ready')
        assert result.status_code==503 and result.json()['checks']['database']['status']=='unavailable'
        assert 'PRIVATE_DB_PASSWORD' not in result.text
        assert client.get('/api/health').status_code==200

@pytest.mark.asyncio
async def test_hung_probes_have_deadlines_and_no_unbounded_db_tasks(app,monkeypatch):
    import backend.app.readiness as probes
    calls=[]
    def slow(store):calls.append(1);time.sleep(.15);return time.time()
    async def slow_service():await asyncio.sleep(1)
    monkeypatch.setattr(probes,'PROBE_SECONDS',.02)
    monkeypatch.setattr(probes,'database_probe',slow)
    app.state.knowledge_services.management.health=slow_service
    started=time.perf_counter()
    results=await asyncio.gather(*(app.state.readiness.check() for _ in range(10)))
    assert time.perf_counter()-started<.12 and len(calls)==1
    assert all(status==503 and body['checks']['management']['status']=='unavailable' for body,status in results)
    await app.state.readiness.pending

def test_stale_worker_and_management_failure_are_named(app):
    from backend.app.readiness import WorkerHeartbeat
    from sqlalchemy.orm import Session
    app.state.store.worker_seen('worker')
    with Session(app.state.store.engine) as session:
        session.get(WorkerHeartbeat,'worker').last_seen=time.time()-60;session.commit()
    async def down():raise ConnectionError()
    app.state.knowledge_services.management.health=down
    with TestClient(app) as client:
        checks=client.get('/api/ready').json()['checks']
        assert checks['worker']['status']=='unavailable' and checks['management']['status']=='unavailable'
        assert checks['database']['status']=='ok'

def test_500_has_correlated_header_and_no_exception_contents(app,caplog):
    @app.get('/explode')
    async def explode():raise ValueError('PRIVATE_EXCEPTION_PASSAGE')
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        result=client.get('/explode',headers={'X-Request-ID':'failure-id'})
        assert result.status_code==500 and result.headers['X-Request-ID']=='failure-id'
    records=[json.loads(r.message) for r in caplog.records if r.name=='relay.journal']
    assert any(r['event']=='http.request' and r['status']==500 and r['request_id']=='failure-id' for r in records)
    assert 'PRIVATE_EXCEPTION_PASSAGE' not in json.dumps(records)

@pytest.mark.asyncio
async def test_cancelled_agent_preserves_reported_token_usage(monkeypatch):
    from backend.app.compiler import compile_workflow
    from backend.app import registry
    from backend.tests.test_agent_tools import calc_flow
    waiting=asyncio.Event();events=[]
    async def model(inputs,config,ctx):
        registry.account_usage(ctx,{'prompt_tokens':13,'completion_tokens':7},config)
        return {'text':'{"action":"call","target":"calc","input":"task"}','provider':'ollama'}
    async def platform(*args):waiting.set();await asyncio.Event().wait()
    async def emit(event):events.append(event)
    monkeypatch.setattr(registry,'llm_node',model)
    graph=compile_workflow(calc_flow(),message='task',platform_resolver=platform,emit=emit).graph
    task=asyncio.create_task(graph.ainvoke({'values':{}}))
    await asyncio.wait_for(waiting.wait(),1);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    cancelled=next(e for e in events if e['status']=='cancelled')
    assert cancelled['usage']['prompt_tokens']==13 and cancelled['usage']['completion_tokens']==7

@pytest.mark.asyncio
async def test_health_client_forwards_correlation_id(monkeypatch):
    import httpx
    from backend.app.kb.rpc import Client
    from backend.app.observability import request_id
    observed=[]
    def handle(request):
        observed.append(request.headers.get('X-Request-ID'))
        return httpx.Response(200,json={'status':'ok'})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    token=request_id.set('ready-trace')
    try:await Client('search',url='http://service').health()
    finally:request_id.reset(token)
    assert observed==['ready-trace']

@pytest.mark.asyncio
async def test_concurrent_request_contexts_are_isolated(app):
    import httpx
    from backend.app.observability import request_id
    @app.get('/context/{identifier}')
    async def context(identifier:str):
        await asyncio.sleep(.01)
        return {'id':request_id.get()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
        responses=await asyncio.gather(*(client.get(f'/context/{i}',headers={'X-Request-ID':f'id-{i}'}) for i in range(10)))
    assert [r.json()['id'] for r in responses]==[f'id-{i}' for i in range(10)]
    assert request_id.get() is None


def test_invite_consumed_journal_omits_token(tmp_path,monkeypatch,caplog):
    from backend.app.auth import issue_invite
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','invite');caplog.set_level(logging.INFO)
    app=create_app(f'sqlite:///{tmp_path}/invite.db',Fernet.generate_key(),embedded_worker=False)
    token=issue_invite(app.state.store.engine)
    with TestClient(app) as client:
        assert client.post('/api/auth/register',json={'email':'private@example.com','password':'PRIVATE_PASSWORD_123','invite_token':token}).status_code==201
    lines=[r.message for r in caplog.records if r.name=='relay.journal']
    assert any(json.loads(line)['event']=='auth.invite_consumed' for line in lines)
    assert all(secret not in '\n'.join(lines) for secret in [token,'private@example.com','PRIVATE_PASSWORD_123'])

@pytest.mark.asyncio
async def test_kb_failure_logs_do_not_include_payloads(tmp_path,caplog):
    from backend.app.kb.management import Management
    from backend.app.kb.search import Search
    caplog.set_level(logging.INFO)
    management=Management(f'sqlite:///{tmp_path}/management.db',tmp_path/'documents',Fernet.generate_key())
    with pytest.raises(KeyError):management.call('get','tenant',{'kb_id':'missing','secret':'PRIVATE_KB_TOKEN'})
    # Search validation fails before touching a vector backend.
    search=Search(f'sqlite:///{tmp_path}/search.db',tmp_path/'indexes',Fernet.generate_key())
    with pytest.raises(ValueError,match='document IDs'):await search.call('search','tenant',{'query':'PRIVATE_QUERY_PASSAGE'})
    lines=[r.message for r in caplog.records if r.name=='relay.journal']
    assert {'kb.management.failure','kb.search.failure'}<={json.loads(line)['event'] for line in lines}
    assert 'PRIVATE_KB_TOKEN' not in '\n'.join(lines) and 'PRIVATE_QUERY_PASSAGE' not in '\n'.join(lines)


def test_recent_job_lease_counts_as_worker_readiness(app):
    from sqlalchemy import update
    from backend.app.storage import JobRecord
    app.state.store.create_run(sample(),'message')
    run=app.state.store.claim_next('worker')
    with TestClient(app) as client:
        assert client.get('/api/ready').status_code==200
        with app.state.store.engine.begin() as connection:
            connection.execute(update(JobRecord).where(JobRecord.run_id==run['id']).values(lease_until=time.time()-1))
        assert client.get('/api/ready').json()['checks']['worker']['status']=='unavailable'

@pytest.mark.asyncio
@pytest.mark.parametrize('deadline',[False,True])
async def test_cancellation_during_event_commit_does_not_double_count_tokens(tmp_path,monkeypatch,deadline):
    import threading
    import httpx
    from backend.app import providers
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from backend.app.operator_metrics import metrics
    store=Store(f'sqlite:///{tmp_path}/cancel-write.db',Fernet.generate_key());store.allow_model('ollama','tiny','','local')
    raw=sample();raw['nodes'][1]={'id':'prompt','type':'llm','inputs':{'prompt':'input.message'},'config':{'provider':'ollama','model':'tiny'}}
    real=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:real(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'message':{'content':'answer'},'prompt_eval_count':13,'eval_count':7}))))
    entered=threading.Event();release=threading.Event();original=store.worker_event
    def paused(id,owner,event):
        if event.get('node_id')=='prompt' and event['status']=='success':
            entered.set();assert release.wait(3)
        return original(id,owner,event)
    monkeypatch.setattr(store,'worker_event',paused)
    if deadline:monkeypatch.setattr('backend.app.worker.RUN_TIMEOUT_SECONDS',.1)
    run=store.create_run(raw,'input');worker=Worker(store)
    execution=asyncio.create_task(worker.execute(store.claim_next(worker.owner)))
    assert await asyncio.to_thread(entered.wait,2)
    if not deadline:execution.cancel()
    # Deliver explicit cancellation or the run deadline while the write is in flight.
    await asyncio.sleep(.15 if deadline else .03);release.set()
    await asyncio.wait_for(execution,3)
    tokens=metrics(store)['tokens']
    assert tokens==[{'provider':'ollama','model':'tiny','prompt_tokens':13,'completion_tokens':7,'calls':1}]
    saved=store.run(run['id'])
    assert saved['checkpoints']['prompt']['text']=='answer'
    if deadline:assert saved['status']=='failed' and 'timed out' in saved['error']
