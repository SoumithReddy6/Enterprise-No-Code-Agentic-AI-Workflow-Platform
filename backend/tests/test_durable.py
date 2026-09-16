import time
import pytest
from cryptography.fernet import Fernet
from backend.app.storage import Store
from backend.tests.test_compiler import sample

@pytest.fixture
def store(tmp_path):
    s=Store(f'sqlite:///{tmp_path}/queue.db',Fernet.generate_key());yield s;s.engine.dispose()

def test_tenant_records_and_credentials_are_isolated(store):
    w=store.save_workflow(sample(),tenant_id='one')
    assert store.workflows('two')==[]
    with pytest.raises(KeyError):store.workflow(w['id'],'two')
    c=store.credential('key','private',tenant_id='one')
    with pytest.raises(ValueError):store.resolve_credential(c['id'],'two')

def test_expired_claim_can_be_recovered_and_stale_owner_is_fenced(store):
    run=store.create_run(sample(),'Ada',tenant_id='one')
    first=store.claim_next('old',lease_seconds=.01)
    assert first['id']==run['id']
    assert store.claim_next('other') is None
    store.worker_event(run['id'],'old',{'node_id':'input','status':'success','outputs':{'message':'Ada'}})
    time.sleep(.02)
    recovered=store.claim_next('new')
    assert recovered['checkpoints']['input']=={'message':'Ada'}
    assert store.worker_event(run['id'],'old',{'status':'failed'}) is False
    assert store.finish_run(run['id'],'old','failed') is False
    assert store.finish_run(run['id'],'new','success',output='Hello Ada') is True
    assert store.run(run['id'],'one')['output']=='Hello Ada'

def test_cancellation_and_resume_preserve_completed_results(store):
    run=store.create_run(sample(),'Ada')
    store.claim_next('worker')
    store.worker_event(run['id'],'worker',{'node_id':'input','status':'success','outputs':{'message':'Ada'}})
    store.request_cancel(run['id'])
    assert store.cancel_requested(run['id'],'worker')
    store.finish_run(run['id'],'worker','cancelled')
    store.resume_run(run['id'])
    assert store.claim_next('new')['checkpoints']['input']['message']=='Ada'


def test_only_one_worker_claims_a_job(store):
    from concurrent.futures import ThreadPoolExecutor
    store.create_run(sample(),'Ada')
    with ThreadPoolExecutor(max_workers=6) as workers:
        claimed=list(workers.map(store.claim_next,[f'worker-{i}' for i in range(6)]))
    assert sum(job is not None for job in claimed)==1


def test_v1_database_is_migrated_without_losing_records(tmp_path):
    import sqlite3,json
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE workflows (id VARCHAR(64) PRIMARY KEY, document JSON NOT NULL, updated_at VARCHAR(64) NOT NULL)')
        db.execute('INSERT INTO workflows VALUES (?,?,?)',('legacy',json.dumps(sample()),'before'))
    migrated=Store(f'sqlite:///{path}',Fernet.generate_key())
    assert migrated.workflow('legacy')['workflow']['name']=='Greeting'
    assert migrated.workflows('another')==[]
    migrated.engine.dispose()


def test_active_legacy_run_keeps_credential_access_after_workspace_claim(store):
    from sqlalchemy import update
    from sqlalchemy.orm import Session
    from backend.app.storage import RunRecord,CredentialRecord
    credential=store.credential('legacy','secret')
    run=store.create_run(sample(),'Ada')
    store.claim_next('worker')
    with Session(store.engine) as session:
        session.execute(update(RunRecord).values(tenant_id='claimed'))
        session.execute(update(CredentialRecord).values(tenant_id='claimed'))
        session.commit()
    assert store.resolve_run_credential(run['id'],'worker',credential['id'])=='secret'
    with pytest.raises(ValueError):store.resolve_run_credential(run['id'],'stale',credential['id'])
