import time
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app as _create_app
from functools import partial
create_app = partial(_create_app, auth_enabled=False)
from backend.tests.test_compiler import sample
from cryptography.fernet import Fernet

@pytest.fixture
def client(tmp_path):
    app = create_app(database_url=f'sqlite:///{tmp_path}/test.db', encryption_key=Fernet.generate_key())
    with TestClient(app) as client:
        yield client


def wait_run(client, id):
    for _ in range(100):
        run = client.get(f'/api/runs/{id}').json()
        if run['status'] not in ('queued','running'): return run
        time.sleep(.02)
    raise AssertionError('Run did not complete')


def test_saved_workflow_runs_and_retains_ordered_events(client):
    saved = client.post('/api/workflows', json=sample()).json()
    assert client.get(f'/api/workflows/{saved["id"]}').json()['workflow']['name'] == 'Greeting'
    response = client.post('/api/runs', json={'workflow':sample(), 'message':'Ada'})
    assert response.status_code == 201
    run = wait_run(client, response.json()['id'])
    assert run['status'] == 'success'
    assert run['output'] == 'Hello Ada'
    assert [e['seq'] for e in run['events']] == list(range(len(run['events'])))
    with client.stream('GET', f'/api/runs/{run["id"]}/events') as stream:
        assert 'event: done' in ''.join(stream.iter_text())


def test_credentials_encrypted_and_never_returned(client):
    secret = 'test-key-do-not-expose'
    result = client.post('/api/credentials', json={'name':'Development','secret':secret})
    assert result.status_code == 201
    assert secret not in result.text
    assert secret not in client.get('/api/credentials').text
    store = client.app.state.store
    assert store.resolve_credential(result.json()['id']) == secret
    from sqlalchemy.orm import Session
    from backend.app.storage import CredentialRecord
    with Session(store.engine) as session:
        assert secret not in session.get(CredentialRecord, result.json()['id']).encrypted


def test_invalid_graph_cannot_run(client):
    raw = sample(); raw['edges'] = []
    result = client.post('/api/runs', json={'workflow':raw, 'message':'Ada'})
    assert result.status_code == 422
    assert 'unreachable' in result.text


def test_missing_records_are_404(client):
    for path in ['/api/workflows/absent','/api/runs/absent','/api/runs/absent/events']:
        assert client.get(path).status_code == 404


def test_drafts_can_be_saved_but_not_run(client):
    raw = sample(); raw['edges'] = []
    assert client.post('/api/workflows',json=raw).status_code == 201
    assert client.post('/api/validate',json=raw).json()['valid'] is False


def test_run_failure_is_visible_and_secret_safe(client,monkeypatch):
    from backend.app import registry
    import httpx
    key=client.post('/api/credentials',json={'name':'Test','secret':'never-expose-this'}).json()
    client.post('/api/models',json={'provider':'openai','model':'gpt-4.1-mini','credential_id':key['id']})
    original=httpx.AsyncClient
    monkeypatch.setattr(registry.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(lambda _:httpx.Response(401,json={'error':'never-expose-this'}))))
    raw=sample()
    raw['nodes'][1]={'id':'prompt','type':'llm','inputs':{'prompt':'input.message'},'config':{'provider':'openai','credential_id':key['id']}}
    response=client.post('/api/runs',json={'workflow':raw,'message':'Hello'})
    run=wait_run(client,response.json()['id'])
    assert run['status']=='failed'
    assert 'HTTP 401' in run['error']
    assert 'never-expose-this' not in str(run)
    assert any(e['status']=='failed' and e.get('node_id')=='prompt' for e in run['events'])


def test_draft_save_rejects_inline_credential_config(client):
    raw=sample();raw['nodes'][1]['config']['api_key']='should-never-be-stored'
    assert client.post('/api/workflows',json=raw).status_code==422
    assert client.get('/api/workflows').json()==[]


def test_validation_returns_normalized_document_for_import(client):
    raw=sample()
    result=client.post('/api/validate',json=raw).json()
    assert result['workflow']['nodes'][0]['config']=={}
    assert result['workflow']['nodes'][0]['version']==1
    assert result['workflow']['edges'][0]['sourceHandle'] is None


def test_validation_errors_do_not_echo_secret_input(client):
    secret='sensitive-value-'*400
    response=client.post('/api/credentials',json={'name':'Test','secret':secret})
    assert response.status_code==422
    assert secret not in response.text
