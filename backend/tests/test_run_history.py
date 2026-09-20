import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event,inspect,select,text
from sqlalchemy.orm import Session
from backend.app.storage import Store,RunRecord
from backend.tests.test_compiler import sample

@pytest.fixture
def store(tmp_path):
    value=Store(f'sqlite:///{tmp_path}/runs.db',Fernet.generate_key())
    yield value
    value.engine.dispose()

def test_event_only_append_inserts_without_rewriting_run_json(store):
    run=store.create_run(sample(),'hello');store.claim_next('worker')
    statements=[]
    def record(conn,cursor,statement,*args):statements.append(statement.lower())
    event.listen(store.engine,'before_cursor_execute',record)
    try:
        for i in range(20):assert store.worker_event(run['id'],'worker',{'kind':'observation','status':'success','transient':True,'text':'x'*2000,'ordinal':i})
    finally:event.remove(store.engine,'before_cursor_execute',record)
    assert not any('update runs set data' in q for q in statements)
    assert sum('insert into run_events' in q for q in statements)==20
    with Session(store.engine) as session:
        assert 'events' not in session.get(RunRecord,run['id']).data
    assert len(store.run(run['id'])['events'])==21

def test_concurrent_appends_are_contiguous_fenced_and_tenant_scoped(store):
    run=store.create_run(sample(),'hello',tenant_id='one');store.claim_next('worker')
    def append(i):return store.worker_event(run['id'],'worker',{'status':'success','transient':True,'ordinal':i})
    with ThreadPoolExecutor(max_workers=8) as pool:assert all(pool.map(append,range(60)))
    rows=store.run_events(run['id'],'one',after=0,limit=100)
    assert [e['seq'] for e in rows]==list(range(1,61))
    assert {e['ordinal'] for e in rows}==set(range(60))
    assert not store.worker_event(run['id'],'stale',{'status':'failed'})
    with pytest.raises(KeyError):store.run_events(run['id'],'two')

def test_legacy_migration_preserves_payloads_order_and_checkpoints(tmp_path):
    path=tmp_path/'legacy.db'
    events=[{'seq':i,'timestamp':f'2026-09-18T00:00:0{i}+00:00','status':'success','outputs':{'text':f'event{i}'}} for i in range(3)]
    data={'id':'old','workflow':sample(),'status':'failed','message':'hi','created_at':'2026-09-18T00:00:00+00:00','events':events,'checkpoints':{'input':{'message':'hi'}},'output':'','error':'failure'}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runs (id VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(64) NOT NULL, data JSON NOT NULL)')
        db.execute('INSERT INTO runs VALUES (?,?,?)',('old','one',json.dumps(data)))
    key=Fernet.generate_key();migrated=Store(f'sqlite:///{path}',key)
    assert 'run_events' in inspect(migrated.engine).get_table_names()
    assert migrated.run('old','one')==data
    migrated.engine.dispose();again=Store(f'sqlite:///{path}',key)
    assert again.run('old','one')==data
    assert again.resume_run('old','one')['checkpoints']==data['checkpoints']
    assert [e['seq'] for e in again.run('old','one')['events']]==[0,1,2,3]
    again.engine.dispose()

def test_indexed_pagination_3000_runs_is_bounded_and_stable(store):
    created='2026-09-18T00:00:00+00:00'
    with Session(store.engine) as session:
        session.add_all([RunRecord(id=f'run{i:05}',tenant_id='one',created_at=created,status='success',name='Test',data={'id':f'run{i:05}','created_at':created,'status':'success','workflow':{'name':'Test','large':'x'*2000},'output':'OK','error':'','checkpoints':{}}) for i in range(3000)])
        session.commit()
    statements=[];loaded=[]
    def sql(conn,cursor,statement,*args):statements.append(statement.lower())
    def load(target,context):loaded.append(target.id)
    event.listen(store.engine,'before_cursor_execute',sql);event.listen(RunRecord,'load',load)
    started=time.perf_counter()
    try:page=store.runs_page('one',limit=100)
    finally:event.remove(store.engine,'before_cursor_execute',sql);event.remove(RunRecord,'load',load)
    assert time.perf_counter()-started<3
    assert len(page['items'])==100 and page['next_cursor']
    assert loaded==[]
    assert all('limit' in q and 'order by' in q for q in statements if q.lstrip().startswith('select'))
    ids=[r['id'] for r in page['items']]
    while page['next_cursor']:
        page=store.runs_page('one',cursor=page['next_cursor']);ids.extend(r['id'] for r in page['items'])
    assert ids==[f'run{i:05}' for i in reversed(range(3000))]
    assert store.runs_page('two')['items']==[]
    with pytest.raises(ValueError):store.runs_page('two',cursor=store.runs_page('one')['next_cursor'])
    with pytest.raises(ValueError):store.runs_page('one',cursor='invalid')
    with store.engine.connect() as connection:
        plan=connection.execute(text("EXPLAIN QUERY PLAN SELECT id FROM runs WHERE tenant_id='one' ORDER BY created_at DESC,id DESC LIMIT 100")).all()
    assert any('ix_runs_tenant_created_id' in str(row) for row in plan)


def test_paginated_api_and_incremental_sse_keep_existing_shapes(tmp_path,monkeypatch):
    from backend.app.main import create_app
    from fastapi.testclient import TestClient
    app=create_app(f'sqlite:///{tmp_path}/api.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False)
    store=app.state.store
    ids=[store.create_run(sample(),'hello')['id'] for _ in range(3)]
    run=store.claim_next('worker')
    for i in range(405):store.worker_event(run['id'],'worker',{'status':'success','transient':True,'ordinal':i})
    store.finish_run(run['id'],'worker','success',output='done')
    with TestClient(app) as client:
        first=client.get('/api/runs/page?limit=2')
        assert first.status_code==200
        assert len(first.json()['items'])==2
        second=client.get('/api/runs/page',params={'cursor':first.json()['next_cursor'],'limit':2})
        assert len(second.json()['items'])==1
        assert len(client.get('/api/runs').json())==3
        assert client.get('/api/runs/page?cursor=invalid').status_code==422
        # SSE must not load a growing full run/event history every poll.
        monkeypatch.setattr(store,'run',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('full run read')))
        response=client.get(f'/api/runs/{run["id"]}/events',headers={'Last-Event-ID':'399'})
        assert response.status_code==200
        assert 'id: 399\n' not in response.text and 'id: 400\n' in response.text and 'id: 406\n' in response.text
        assert 'event: done' in response.text


def test_failed_migration_rolls_back_and_can_be_retried(tmp_path):
    path=tmp_path/'broken.db';key=Fernet.generate_key()
    data={'id':'old','status':'failed','events':[{'seq':0,'timestamp':'2026-09-18','status':'running'},{'seq':9,'timestamp':'2026-09-18','status':'failed'}]}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runs (id VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(64) NOT NULL, data JSON NOT NULL)')
        db.execute('INSERT INTO runs VALUES (?,?,?)',('old','one',json.dumps(data)))
    with pytest.raises(ValueError,match='invalid event ordering'):Store(f'sqlite:///{path}',key)
    with sqlite3.connect(path) as db:
        assert json.loads(db.execute('SELECT data FROM runs').fetchone()[0])==data
        assert db.execute("SELECT name FROM sqlite_master WHERE name='run_events'").fetchone() is None
        data['events'][1]['seq']=1
        db.execute('UPDATE runs SET data=?',(json.dumps(data),))
    store=Store(f'sqlite:///{path}',key)
    assert store.run('old','one')['events']==data['events']
    store.engine.dispose()


def test_failed_agent_transient_sources_reserve_labels_across_resume(store):
    from backend.app.evidence_registry import allocate_labels
    run=store.create_run(sample(),'hello');store.claim_next('worker')
    store.worker_event(run['id'],'worker',{'node_id':'tool','status':'success','transient':True,'outputs':{'sources':json.dumps([{'citation':'S21','text':'old fact'}])}})
    store.finish_run(run['id'],'worker','failed',error='agent failed')
    store.resume_run(run['id'])
    resumed=store.claim_next('next')
    assert resumed['checkpoints']=={}
    assert allocate_labels({'evidence':[],'citation_counter':resumed['citation_counter']},1)==['S22']
