"""HTTP behavior, persistent concurrency fences, and pre-hash denial."""
from concurrent.futures import ThreadPoolExecutor
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from backend.app import auth
from backend.app.storage import Store

PASSWORD='test-password-123'

@pytest.fixture
def secured(tmp_path,monkeypatch):
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','closed')
    store=Store(f'sqlite:///{tmp_path}/auth.db',Fernet.generate_key())
    app=FastAPI();auth.install_auth(app,store)
    client=TestClient(app,client=('192.0.2.1',1234))
    assert client.post('/api/auth/register',json={'email':'owner@example.com','password':PASSWORD}).status_code==201
    clock=[2_000_000_000.]
    monkeypatch.setattr(auth.time,'time',lambda:clock[0])
    return client,store,clock

def login(client,email='owner@example.com',password='wrong'):
    return client.post('/api/auth/login',json={'email':email,'password':password})

def test_ten_failures_lock_before_hash_and_expire(secured,monkeypatch):
    client,store,clock=secured
    for i in range(10):
        if i:clock[0]+=32
        assert login(client).status_code==401
    calls=[];original=auth.password_matches
    def observed(*args):calls.append(True);return original(*args)
    monkeypatch.setattr(auth,'password_matches',observed)
    for password in ('wrong',PASSWORD):
        response=login(client,password=password)
        assert response.status_code==429
        assert int(response.headers['retry-after'])==900
    assert calls==[]
    clock[0]+=901
    assert login(client,password=PASSWORD).status_code==200
    assert len(calls)==1

def test_escalating_cooldown_and_success_reset(secured):
    client,_,clock=secured
    for _ in range(5):assert login(client).status_code==401
    assert login(client).status_code==429
    clock[0]+=1
    assert login(client).status_code==401
    assert int(login(client).headers['retry-after'])==2
    clock[0]+=2
    assert login(client,password=PASSWORD).status_code==200
    for _ in range(5):assert login(client).status_code==401
    assert int(login(client).headers['retry-after'])==1

def test_account_limit_spans_ips_and_restart(secured):
    client,store,clock=secured
    for i in range(10):
        clock[0]+=32
        other=TestClient(client.app,client=(f'192.0.2.{1+i%2}',1234))
        assert login(other).status_code==401
    app=FastAPI();auth.install_auth(app,store)
    assert login(TestClient(app,client=('192.0.2.3',1234))).status_code==429

def test_ip_limit_unknown_accounts_and_untrusted_forwarded_headers(secured,monkeypatch):
    client,_,clock=secured
    for i in range(10):
        clock[0]+=32
        assert login(client,email=f'unknown{i}@example.com').status_code==401
    original=auth.password_matches
    def forbidden(*args):raise AssertionError('Locked request must not hash')
    monkeypatch.setattr(auth,'password_matches',forbidden)
    response=client.post('/api/auth/login',json={'email':'new@example.com','password':'wrong'},headers={'X-Forwarded-For':'198.51.100.7','X-Real-IP':'198.51.100.8'})
    assert response.status_code==429
    monkeypatch.setattr(auth,'password_matches',original)
    assert login(TestClient(client.app,client=('192.0.2.9',1234)),email='unknown9@example.com').status_code==401

def test_concurrent_login_reservations_bound_hash_work(secured,monkeypatch):
    client,_,_=secured
    import threading
    entered=[];lock=threading.Lock();original=auth.password_matches
    def observed(*args):
        with lock:entered.append(1)
        return original(*args)
    monkeypatch.setattr(auth,'password_matches',observed)
    def attempt(_):return login(TestClient(client.app,client=('192.0.2.1',1234))).status_code
    with ThreadPoolExecutor(max_workers=16) as pool:statuses=list(pool.map(attempt,range(16)))
    assert statuses.count(401)==5
    assert statuses.count(429)==11
    assert len(entered)==5


def test_default_closed_rejects_second_account_before_hash(secured,monkeypatch):
    client,_,_=secured
    monkeypatch.setattr(auth,'password_hash',lambda *args: (_ for _ in ()).throw(AssertionError('Denied registration hashed password')))
    for email in ('second@example.com','owner@example.com'):
        response=client.post('/api/auth/register',json={'email':email,'password':PASSWORD})
        assert response.status_code==403
        assert response.json()['detail']=='Registration is not permitted.'


def test_closed_bootstrap_concurrency_has_exactly_one_account(tmp_path,monkeypatch):
    monkeypatch.delenv('AUTH_REGISTRATION_MODE',raising=False)
    store=Store(f'sqlite:///{tmp_path}/race.db',Fernet.generate_key())
    app=FastAPI();auth.install_auth(app,store)
    def create(i):return TestClient(app).post('/api/auth/register',json={'email':f'{i}@example.com','password':PASSWORD}).status_code
    with ThreadPoolExecutor(max_workers=5) as pool:statuses=list(pool.map(create,range(5)))
    assert statuses.count(201)==1
    assert statuses.count(403)==4


def test_invite_is_required_expiring_email_bound_and_single_use(tmp_path,monkeypatch):
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','invite')
    store=Store(f'sqlite:///{tmp_path}/invite.db',Fernet.generate_key())
    app=FastAPI();auth.install_auth(app,store);client=TestClient(app)
    assert client.post('/api/auth/register',json={'email':'a@example.com','password':PASSWORD,'invite_token':'invalid'}).status_code==403
    from backend.app.auth import issue_invite
    token=issue_invite(store.engine,email='a@example.com',hours=1)
    wrong=client.post('/api/auth/register',json={'email':'b@example.com','password':PASSWORD,'invite_token':token})
    assert wrong.status_code==403
    valid={'email':'a@example.com','password':PASSWORD,'invite_token':token}
    assert client.post('/api/auth/register',json=valid).status_code==201
    assert client.post('/api/auth/register',json=valid).status_code==403
    token=issue_invite(store.engine,hours=1)
    current=auth.time.time();monkeypatch.setattr(auth.time,'time',lambda:current+3601)
    assert client.post('/api/auth/register',json={'email':'c@example.com','password':PASSWORD,'invite_token':token}).status_code==403


def test_invite_concurrent_use_creates_one_account(tmp_path,monkeypatch):
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','invite')
    store=Store(f'sqlite:///{tmp_path}/inviterace.db',Fernet.generate_key())
    app=FastAPI();auth.install_auth(app,store)
    from backend.app.auth import issue_invite
    token=issue_invite(store.engine)
    def create(i):return TestClient(app).post('/api/auth/register',json={'email':f'{i}@example.com','password':PASSWORD,'invite_token':token}).status_code
    with ThreadPoolExecutor(max_workers=5) as pool:statuses=list(pool.map(create,range(5)))
    assert statuses.count(201)==1
    assert statuses.count(403)==4


def test_operator_unlock_restores_login(secured):
    client,store,clock=secured
    for _ in range(10):
        clock[0]+=32
        assert login(client).status_code==401
    from backend.app.auth_security import unlock_login
    assert unlock_login(store.engine,email=' OWNER@example.com ',address='192.0.2.1')==2
    assert login(client,password=PASSWORD).status_code==200


def test_operator_cli_invite_and_unlock(secured,monkeypatch):
    import subprocess,sys
    client,store,clock=secured
    for _ in range(5):assert login(client).status_code==401
    result=subprocess.run([sys.executable,'-m','scripts.auth_admin','--database-url',str(store.engine.url),'unlock','--email','owner@example.com','--ip','192.0.2.1'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert login(client,password=PASSWORD).status_code==200
    result=subprocess.run([sys.executable,'-m','scripts.auth_admin','--database-url',str(store.engine.url),'invite','--email','guest@example.com'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','invite')
    # CLI uses real time; undo this test's synthetic clock before redeeming.
    monkeypatch.undo()
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','invite')
    app=FastAPI();auth.install_auth(app,store)
    response=TestClient(app).post('/api/auth/register',json={'email':'guest@example.com','password':PASSWORD,'invite_token':result.stdout.strip()})
    assert response.status_code==201
    from backend.app.auth import InviteRecord
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    with Session(store.engine) as session:
        row=session.scalar(select(InviteRecord))
        assert row.token_hash!=result.stdout.strip()
        assert row.used_at is not None


def test_success_cannot_erase_other_accounts_ip_failures(secured):
    client,_,clock=secured
    for i in range(4):assert login(client,email=f'unknown{i}@example.com').status_code==401
    assert login(client,password=PASSWORD).status_code==200
    # A known password must not provide an IP-budget reset for password spraying.
    assert login(client,email='new@example.com').status_code==429


def test_failed_registration_does_not_consume_invite(secured,monkeypatch):
    client,store,_=secured
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','invite')
    app=FastAPI();auth.install_auth(app,store);client=TestClient(app)
    token=auth.issue_invite(store.engine)
    assert client.post('/api/auth/register',json={'email':'owner@example.com','password':PASSWORD,'invite_token':token}).status_code==409
    assert client.post('/api/auth/register',json={'email':'new@example.com','password':PASSWORD,'invite_token':token}).status_code==201


def test_success_does_not_erase_newer_concurrent_failures(secured,monkeypatch):
    import threading
    client,_,_=secured;started=threading.Event();release=threading.Event()
    original=auth.password_matches
    def delayed(password,encoded):
        if password==PASSWORD:
            started.set();assert release.wait(5)
        return original(password,encoded)
    monkeypatch.setattr(auth,'password_matches',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(login,TestClient(client.app,client=('192.0.2.1',1234)),password=PASSWORD)
        assert started.wait(5)
        try:
            for _ in range(4):assert login(client).status_code==401
        finally:release.set()
        assert future.result().status_code==200
    assert login(client).status_code==429


def test_later_success_resets_older_reserved_attempts(secured,monkeypatch):
    import threading
    client,_,_=secured;started=threading.Event();release=threading.Event()
    original=auth.password_matches
    def delayed(password,encoded):
        if password=='pending-wrong':
            started.set();assert release.wait(5)
        return original(password,encoded)
    monkeypatch.setattr(auth,'password_matches',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(login,TestClient(client.app,client=('192.0.2.1',1234)),password='pending-wrong')
        assert started.wait(5)
        try:assert login(client,password=PASSWORD).status_code==200
        finally:release.set()
        assert future.result().status_code==401
    # Resets are ordered by admission, not by completion of hashing. The older
    # attempt cannot re-create a cleared budget after a later successful login.
    for _ in range(5):assert login(client).status_code==401
    assert login(client).status_code==429
