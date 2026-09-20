from fastapi.testclient import TestClient
from cryptography.fernet import Fernet
from backend.app.main import create_app
from backend.tests.test_compiler import sample
from backend.tests.test_api import wait_run


def test_all_resource_routes_enforce_tenant_scope(tmp_path,monkeypatch):
    monkeypatch.setenv('AUTH_REGISTRATION_MODE','open')
    app=create_app(f'sqlite:///{tmp_path}/tenants.db',Fernet.generate_key())
    with TestClient(app) as client:
        assert client.get('/api/workflows').status_code==401
        first=client.post('/api/auth/register',json={'email':'one@example.com','password':'first-password-123'})
        assert first.status_code==201
        saved=client.post('/api/workflows',json=sample()).json()
        credential=client.post('/api/credentials',json={'name':'one','secret':'one-secret'}).json()
        run_id=client.post('/api/runs',json={'workflow':sample(),'message':'One'}).json()['id']
        assert wait_run(client,run_id)['output']=='Hello One'
        client.post('/api/auth/logout')
        assert client.post('/api/auth/register',json={'email':'two@example.com','password':'second-password-123'}).status_code==201
        assert client.get('/api/workflows').json()==[]
        assert client.get('/api/credentials').json()==[]
        assert client.get('/api/runs').json()==[]
        for route in [f'/api/workflows/{saved["id"]}',f'/api/runs/{run_id}',f'/api/runs/{run_id}/events']:
            assert client.get(route).status_code==404
        for action in ('cancel','resume'):
            assert client.post(f'/api/runs/{run_id}/{action}').status_code==404
        assert client.put(f'/api/workflows/{saved["id"]}',json=sample()).status_code==404
        raw=sample();raw['nodes'][1]={'id':'prompt','type':'llm','inputs':{'prompt':'input.message'},'config':{'provider':'openai','credential_id':credential['id']}}
        response=client.post('/api/runs',json={'workflow':raw,'message':'Two'})
        assert response.status_code==422
        assert 'not enabled' in response.text
        assert 'one-secret' not in response.text


def test_api_restart_does_not_cancel_external_worker_job(tmp_path):
    url=f'sqlite:///{tmp_path}/restart.db';key=Fernet.generate_key()
    with TestClient(create_app(url,key,auth_enabled=False,embedded_worker=False)) as client:
        id=client.post('/api/runs',json={'workflow':sample(),'message':'Saved'}).json()['id']
    with TestClient(create_app(url,key,auth_enabled=False,embedded_worker=True)) as client:
        assert wait_run(client,id)['output']=='Hello Saved'
