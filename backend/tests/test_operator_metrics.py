import json
from datetime import datetime,timezone
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.tests.test_compiler import sample


def test_operator_metrics_permissions_and_known_aggregates(tmp_path,monkeypatch):
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','open')
    app=create_app(f'sqlite:///{tmp_path}/metrics.db',Fernet.generate_key(),embedded_worker=False)
    store=app.state.store
    with TestClient(app) as client:
        assert client.get('/api/operator/metrics').status_code==401
        operator=client.post('/api/auth/register',json={'email':'operator@example.com','password':'OperatorPassword!'}).json()
        assert client.get('/api/operator/metrics').status_code==200
        for i in range(4):
            run=store.create_run(sample(),'SECRET prompt',operator['tenant_id']);store.claim_next('worker')
            store.worker_event(run['id'],'worker',{'node_id':'agent','status':'success','usage':{'by_model':[{'provider':'ollama','model':'llama3.1','prompt_tokens':10,'completion_tokens':5,'calls':1}]},'outputs':{'grounding':json.dumps({'abstain':i==0}),'sources':'[]','text':'SECRET passage'}})
            store.finish_run(run['id'],'worker','success',output='SECRET answer',truncated=i<2)
        result=client.get('/api/operator/metrics')
        assert result.status_code==200
        body=result.json();assert body['runs_by_status']=={'success':4}
        assert body['definitions']['scope']=='deployment-wide'
        assert body['abstention_rate']==.25 and body['truncation_rate']==.5
        assert body['tokens']==[{'provider':'ollama','model':'llama3.1','prompt_tokens':40,'completion_tokens':20,'calls':4}]
        assert body['duration_seconds']['p95']>=body['duration_seconds']['p50']>=0
        assert 'SECRET' not in result.text
        assert client.get('/api/operator/metrics?hours=0').status_code==422
        client.post('/api/auth/logout')
        ordinary=client.post('/api/auth/register',json={'email':'ordinary@example.com','password':'OtherPassword!123'}).json()
        assert client.get('/api/operator/metrics').status_code==403
        monkeypatch.setenv('RELAY_OPERATOR_ACCOUNT_IDS',ordinary['id'])
        assert client.get('/api/operator/metrics').status_code==200
        client.post('/api/auth/login',json={'email':'operator@example.com','password':'OperatorPassword!'})
        assert client.get('/api/operator/metrics').status_code==403


def test_exact_percentiles_window_cached_usage_and_resume(tmp_path):
    from backend.app.storage import Store,RunRecord
    from backend.app.operator_metrics import metrics
    from sqlalchemy.orm import Session
    from datetime import timedelta
    store=Store(f'sqlite:///{tmp_path}/known.db',Fernet.generate_key())
    stamp=datetime.now(timezone.utc)
    with Session(store.engine) as session:
        for i in range(1,21):
            session.add(RunRecord(id=f'known{i}',tenant_id='a',created_at=(stamp-timedelta(hours=1)).isoformat(),status='success',name='known',duration_seconds=float(i),grounded=True,abstained=i<=2,truncated=i<=4,data={}))
        session.add(RunRecord(id='outside',tenant_id='a',created_at=(stamp-timedelta(days=8)).isoformat(),status='failed',duration_seconds=999,name='old',data={}))
        session.commit()
    report=metrics(store,24)
    assert report['runs_by_status']=={'success':20}
    assert report['duration_seconds']=={'p50':10.0,'p95':19.0}
    assert report['abstention_rate']==.1 and report['truncation_rate']==.2
    run=store.create_run(sample(),'test');store.claim_next('worker')
    usage={'by_model':[{'provider':'ollama','model':'a','prompt_tokens':10,'completion_tokens':2,'calls':1},{'provider':'ollama','model':'b','prompt_tokens':20,'completion_tokens':3,'calls':1}]}
    store.worker_event(run['id'],'worker',{'node_id':'agent','status':'failed','usage':usage})
    store.finish_run(run['id'],'worker','failed')
    store.resume_run(run['id']);store.claim_next('worker')
    store.worker_event(run['id'],'worker',{'node_id':'agent','status':'success','cached':True,'usage':usage,'outputs':{}})
    store.finish_run(run['id'],'worker','success')
    assert metrics(store,24)['tokens']==[{'provider':'ollama','model':'a','prompt_tokens':10,'completion_tokens':2,'calls':1},{'provider':'ollama','model':'b','prompt_tokens':20,'completion_tokens':3,'calls':1}]
    store.engine.dispose()


def test_metrics_backfill_is_idempotent_and_queries_avoid_payloads(tmp_path):
    import sqlite3
    from datetime import timedelta
    from sqlalchemy import event
    from backend.app.storage import Store
    from backend.app.operator_metrics import metrics
    path=tmp_path/'legacy.db';key=Fernet.generate_key()
    stamp=datetime.now(timezone.utc)-timedelta(hours=1)
    workflow=sample();workflow['nodes'][1]['config']={'provider':'ollama','model':'legacy'}
    data={'id':'legacy','created_at':stamp.isoformat(),'finished_at':(stamp+timedelta(seconds=12)).isoformat(),'status':'success','workflow':workflow,'checkpoints':{'agent':{'grounding':'{"abstain":true}'}},'truncated':True,'events':[{'seq':0,'timestamp':stamp.isoformat(),'node_id':'prompt','status':'success','usage':{'calls':1,'prompt_tokens':8,'completion_tokens':4}}]}
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runs(id VARCHAR(64) PRIMARY KEY,tenant_id VARCHAR(64) NOT NULL,data JSON NOT NULL)')
        db.execute('INSERT INTO runs VALUES(?,?,?)',('legacy','tenant',json.dumps(data)))
    first=Store(f'sqlite:///{path}',key);first.engine.dispose()
    second=Store(f'sqlite:///{path}',key);queries=[]
    def sql(conn,cursor,statement,*args):queries.append(statement)
    event.listen(second.engine,'before_cursor_execute',sql)
    report=metrics(second)
    assert report['duration_seconds']=={'p50':12.0,'p95':12.0}
    assert report['abstention_rate']==1 and report['truncation_rate']==1
    assert report['tokens']==[{'provider':'ollama','model':'legacy','calls':1,'prompt_tokens':8,'completion_tokens':4}]
    assert all('runs.data' not in statement and 'payload' not in statement for statement in queries)
    second.engine.dispose()


def test_startup_backfill_journals_success_and_failure(tmp_path,caplog,monkeypatch):
    from backend.app.storage import Store
    key=Fernet.generate_key()
    caplog.set_level('INFO',logger='relay.journal')
    store=Store(f'sqlite:///{tmp_path}/startup.db',key);store.engine.dispose()
    records=[json.loads(r.message) for r in caplog.records if r.name=='relay.journal']
    assert [r['event'] for r in records]==['storage.backfill.start','storage.backfill.finish']
    assert records[-1]['status']=='success' and records[-1]['seconds']>=0
    caplog.clear()
    def fail(conn):raise ValueError('PRIVATE database detail')
    monkeypatch.setattr(Store,'_migrate_run_events',staticmethod(fail))
    with pytest.raises(ValueError):Store(f'sqlite:///{tmp_path}/failed.db',key)
    records=[json.loads(r.message) for r in caplog.records if r.name=='relay.journal']
    assert [r['event'] for r in records]==['storage.backfill.start','storage.backfill.finish']
    assert records[-1]['status']=='failed' and records[-1]['error_type']=='ValueError'
    assert 'PRIVATE' not in caplog.text
