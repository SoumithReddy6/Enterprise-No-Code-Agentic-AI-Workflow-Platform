import time
import pytest
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app.storage import Store, WorkflowRecord, RunRecord, CredentialRecord


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    monkeypatch.setenv('AUTH_REGISTRATION_MODE', 'open')
    from backend.app.auth import install_auth
    store = Store(f'sqlite:///{tmp_path}/auth.db', Fernet.generate_key())
    app = FastAPI()
    controller = install_auth(app, store)
    @app.get('/protected')
    @app.post('/protected')
    def protected(tenant=Depends(controller.tenant)):
        return {'tenant_id': tenant}
    return TestClient(app), store


def register(client, email='owner@example.com'):
    return client.post('/api/auth/register', json={'email': email, 'password': 'test-password-123'})


def test_setup_claims_local_records_only_once(auth_app):
    client, store = auth_app
    with Session(store.engine) as session:
        session.add_all([WorkflowRecord(id='w', document={'name':'legacy'}, updated_at='now'), RunRecord(id='r', data={}), CredentialRecord(id='c', name='legacy', encrypted='encrypted')])
        session.commit()
    assert client.get('/api/auth/status').json() == {'enabled': True, 'needs_setup': True, 'registration_mode':'open'}
    assert client.get('/protected').status_code == 401
    response = register(client)
    assert response.status_code == 201
    tenant = response.json()['tenant_id']
    assert client.get('/protected').json()['tenant_id'] == tenant
    assert client.get('/api/auth/status').json()['needs_setup'] is False
    with Session(store.engine) as session:
        for model in (WorkflowRecord, RunRecord, CredentialRecord):
            assert session.scalar(select(model)).tenant_id == tenant
    second = register(client, 'second@example.com')
    assert second.status_code == 201
    assert second.json()['tenant_id'] != tenant
    with Session(store.engine) as session:
        assert session.get(WorkflowRecord, 'w').tenant_id == tenant


def test_login_cookie_revocation_expiry_and_invalid_tokens(auth_app):
    from backend.app.auth import SessionRecord, COOKIE_NAME
    client, store = auth_app
    response = register(client)
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    token = client.cookies.get(COOKIE_NAME)
    assert client.post('/api/auth/logout').status_code == 200
    client.cookies.set(COOKIE_NAME, token)
    assert client.get('/api/auth/me').status_code == 401
    client.cookies.clear()
    assert client.post('/api/auth/login', json={'email':'OWNER@example.com','password':'test-password-123'}).status_code == 200
    with Session(store.engine) as session:
        row = session.scalar(select(SessionRecord))
        row.expires_at = int(time.time()) - 1
        session.commit()
    assert client.get('/protected').status_code == 401
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, 'invented')
    assert client.get('/protected').status_code == 401


def test_auth_validation_and_cross_origin_rejection(auth_app):
    client, _ = auth_app
    assert register(client).status_code == 201
    assert register(client).status_code == 409
    for email in ('owner@example.com', 'unknown@example.com'):
        response = client.post('/api/auth/login', json={'email':email,'password':'incorrect-password'})
        assert response.status_code == 401
        assert response.json()['detail'] == 'Invalid email or password.'
    for route in ('register', 'login', 'logout'):
        assert client.post('/api/auth/' + route, headers={'Origin':'https://evil.example'}, json={'email':'third@example.com','password':'test-password-123'}).status_code == 403
    assert client.post('/api/auth/register', json={'email':'x@example.com','password':'short'}).status_code == 422


def test_auth_disabled_retains_local_mode(tmp_path):
    from backend.app.auth import install_auth
    app = FastAPI()
    store = Store(f'sqlite:///{tmp_path}/disabled.db', Fernet.generate_key())
    controller = install_auth(app, store, enabled=False)
    @app.get('/protected')
    def protected(tenant=Depends(controller.tenant)):
        return tenant
    client = TestClient(app)
    assert client.get('/protected').json() == 'local'
    assert client.get('/api/auth/status').json() == {'enabled':False,'needs_setup':False,'registration_mode':'closed'}


def test_accounts_and_sessions_survive_app_restart(auth_app):
    from backend.app.auth import AccountRecord, SessionRecord, COOKIE_NAME, install_auth
    client, store = auth_app
    register(client)
    token = client.cookies.get(COOKIE_NAME)
    app = FastAPI()
    install_auth(app, store)
    restarted = TestClient(app)
    restarted.cookies.set(COOKIE_NAME, token)
    assert restarted.get('/api/auth/me').json()['email'] == 'owner@example.com'
    with Session(store.engine) as session:
        account = session.scalar(select(AccountRecord))
        assert 'test-password-123' not in account.password_hash
        assert session.scalar(select(SessionRecord)).token_hash != token
    register(client, 'second@example.com')
    with Session(store.engine) as session:
        hashes = list(session.scalars(select(AccountRecord.password_hash)))
        assert hashes[0] != hashes[1]


def test_concurrent_bootstrap_has_one_legacy_owner(auth_app):
    from concurrent.futures import ThreadPoolExecutor
    from backend.app.auth import BootstrapRecord
    client, store = auth_app
    with Session(store.engine) as session:
        session.add(WorkflowRecord(id='legacy', document={'name':'old'}, updated_at='now'))
        session.commit()
    def create(index):
        with TestClient(client.app) as separate:
            return register(separate, f'person{index}@example.com')
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(create, range(4)))
    assert all(response.status_code in (201, 409) for response in responses)
    owners = [response.json()['tenant_id'] for response in responses if response.status_code == 201]
    assert owners and len(set(owners)) == len(owners)
    with Session(store.engine) as session:
        claimed = session.get(WorkflowRecord, 'legacy').tenant_id
        assert claimed in owners
        assert session.get(BootstrapRecord, 1).tenant_id == claimed



def test_protected_mutations_reject_cross_origin(auth_app):
    client, _ = auth_app
    assert register(client).status_code == 201
    assert client.post('/protected', headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.post('/protected', headers={'Origin':'http://testserver'}).status_code == 200
    assert client.post('/protected').status_code == 200
    assert client.post('/protected', headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
