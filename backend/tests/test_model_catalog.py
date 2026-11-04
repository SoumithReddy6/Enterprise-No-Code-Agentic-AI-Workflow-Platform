import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.tests.test_compiler import sample
from backend.app.worker import Worker

@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f'sqlite:///{tmp_path}/catalog.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False)) as c:yield c

def flow(provider='ollama',model='tiny:latest',credential_id=''):
    raw=sample()
    raw['nodes'][1]={'id':'prompt','type':'llm','inputs':{'prompt':'input.message'},'config':{'provider':provider,'model':model,'credential_id':credential_id}}
    return raw

def test_only_enabled_models_can_be_submitted_or_resumed(client):
    raw=flow()
    assert client.post('/api/runs',json={'workflow':raw}).status_code==422
    approved=client.post('/api/models',json={'provider':'ollama','model':'tiny:latest'} )
    assert approved.status_code==201
    assert client.post('/api/validate',json=raw).json()['valid']
    run=client.post('/api/runs',json={'workflow':raw}).json()
    client.post(f'/api/runs/{run["id"]}/cancel')
    assert client.put(f'/api/models/{approved.json()["id"]}',json={'enabled':False}).status_code==200
    assert not client.post('/api/validate',json=raw).json()['valid']
    assert client.post(f'/api/runs/{run["id"]}/resume').status_code==422

def test_cloud_keys_are_bound_to_their_provider_and_workspace(client):
    key=client.post('/api/credentials',json={'name':'Claude','secret':'private','provider':'claude'})
    assert key.status_code==201
    assert key.json()['provider']=='claude'
    assert 'private' not in client.get('/api/credentials').text
    config={'provider':'openai','model':'model-id','credential_id':key.json()['id']}
    assert client.post('/api/models',json=config).status_code==422
    config['provider']='claude'
    assert client.post('/api/models',json=config).status_code==201
    assert client.post('/api/runs',json={'workflow':flow(**config)}).status_code==201
    store=client.app.state.store
    assert store.models('other')==[]
    with pytest.raises(ValueError):store.allow_model(**config,tenant_id='other')

@pytest.mark.asyncio
async def test_worker_rechecks_permission_after_queuing(client,monkeypatch):
    from backend.app import providers
    async def forbidden(*args):raise AssertionError('Revoked model must not execute')
    monkeypatch.setattr(providers,'ollama_chat',forbidden)
    allowed=client.post('/api/models',json={'provider':'ollama','model':'tiny:latest'}).json()
    run=client.post('/api/runs',json={'workflow':flow()}).json()
    client.put(f'/api/models/{allowed["id"]}',json={'enabled':False})
    store=client.app.state.store
    job=store.claim_next('test-worker');job['_claim_owner']='test-worker'
    await Worker(store).execute(job)
    result=store.run(run['id'])
    assert result['status']=='failed'
    assert 'not enabled' in result['error']

def test_old_credentials_migrate_as_openai_without_losing_keys(tmp_path):
    import sqlite3
    from backend.app.storage import Store
    key=Fernet.generate_key();path=tmp_path/'old.db'
    encrypted=Fernet(key).encrypt(b'original-key').decode()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE credentials (id VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(64) NOT NULL, name VARCHAR(120) NOT NULL, encrypted TEXT NOT NULL)')
        db.execute('INSERT INTO credentials VALUES (?,?,?,?)',('legacy','owner','Existing',encrypted))
    store=Store(f'sqlite:///{path}',key)
    assert store.credentials('owner')==[{'id':'legacy','name':'Existing','provider':'openai'}]
    assert store.resolve_credential('legacy','owner')=='original-key'
    assert store.models('owner')==[]
    store.engine.dispose()

def test_catalog_ownership_is_claimed_on_first_registration(tmp_path,monkeypatch):
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','open')
    from backend.app.storage import Store
    url=f'sqlite:///{tmp_path}/claim.db';key=Fernet.generate_key()
    store=Store(url,key)
    allowed=store.allow_model('ollama','tiny')
    store.engine.dispose()
    with TestClient(create_app(url,key,embedded_worker=False)) as client:
        assert client.get('/api/models').status_code==401
        assert client.post('/api/auth/register',json={'email':'first@example.com','password':'a-good-test-password'}).status_code==201
        assert client.get('/api/models').json()==[allowed]
        client.post('/api/auth/logout')
        client.post('/api/auth/register',json={'email':'second@example.com','password':'a-good-test-password'})
        assert client.get('/api/models').json()==[]
        assert client.put(f'/api/models/{allowed["id"]}',json={'enabled':False}).status_code==404
        assert client.post('/api/runs',json={'workflow':flow(model='tiny')}).status_code==422

def test_concurrent_enables_create_one_permission_and_disable_revokes_it(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import event
    from backend.app.registry import LLMConfig
    store=client.app.state.store
    barrier=Barrier(2)
    def synchronize(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('SELECT allowed_models.') and not barrier.broken:
            try:barrier.wait(timeout=2)
            except Exception:pass
    event.listen(store.engine,'after_cursor_execute',synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows=list(pool.map(lambda _:store.allow_model('ollama','tiny'),range(2)))
    finally:event.remove(store.engine,'after_cursor_execute',synchronize)
    assert rows[0]['id']==rows[1]['id']
    assert len(store.models())==1
    store.set_model_enabled(rows[0]['id'],False)
    with pytest.raises(ValueError,match='not enabled'):store.authorize_model(LLMConfig(provider='ollama',model='tiny'))
