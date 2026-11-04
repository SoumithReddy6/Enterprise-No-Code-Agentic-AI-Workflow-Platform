import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.tests.test_compiler import sample

def client_for(tmp_path):
    return TestClient(create_app(f'sqlite:///{tmp_path}/api.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False))

def test_fresh_save_then_stale_save_returns_current_document(tmp_path):
    with client_for(tmp_path) as client:
        saved=client.post('/api/workflows',json=sample()).json()
        fresh={**sample(),'name':'Editor One','updated_at':saved['updated_at']}
        a=client.put('/api/workflows/'+saved['id'],json=fresh)
        assert a.status_code==200
        b=client.put('/api/workflows/'+saved['id'],json={**fresh,'name':'Editor Two'})
        assert b.status_code==409 and b.json()['current']==a.json()
        assert client.get('/api/workflows/'+saved['id']).json()==a.json()
        assert client.put('/api/workflows/'+saved['id'],json=sample()).status_code==409

def test_concurrent_puts_have_one_winner(tmp_path):
    with client_for(tmp_path) as client:
        saved=client.post('/api/workflows',json=sample()).json()
        barrier=Barrier(2)
        def save(name):
            barrier.wait()
            return client.put('/api/workflows/'+saved['id'],json={**sample(),'name':name,'updated_at':saved['updated_at']})
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(save,['one','two']))
        assert sorted(r.status_code for r in results)==[200,409]
        winner=next(r.json() for r in results if r.status_code==200)
        assert client.get('/api/workflows/'+saved['id']).json()==winner

def test_save_versions_advance_even_with_a_frozen_clock(tmp_path,monkeypatch):
    monkeypatch.setattr('backend.app.storage.now',lambda:'2026-09-18T00:00:00+00:00')
    with client_for(tmp_path) as client:
        saved=client.post('/api/workflows',json=sample()).json()
        first=client.put('/api/workflows/'+saved['id'],json={**sample(),'updated_at':saved['updated_at']})
        assert first.status_code==200 and first.json()['updated_at']!=saved['updated_at']
        stale=client.put('/api/workflows/'+saved['id'],json={**sample(),'updated_at':saved['updated_at']})
        assert stale.status_code==409
        invalid=client.put('/api/workflows/'+saved['id'],json={**sample(),'updated_at':'wrong-token'})
        assert invalid.status_code==409

def test_retired_vectordb_fixture_has_actionable_validation(tmp_path):
    raw=json.loads((Path(__file__).parent/'fixtures/pre-vectordb-removal-workflow.json').read_text())
    with client_for(tmp_path) as client:
        validation=client.post('/api/validate',json=raw)
        assert validation.status_code==200
        assert not validation.json()['valid']
        assert 'retired VectorDB nodes' in ' '.join(validation.json()['errors'])
        for route,payload in [('/api/workflows',raw),('/api/runs',{'workflow':raw,'message':'test'})]:
            result=client.post(route,json=payload)
            assert result.status_code==422 and 'retired VectorDB nodes' in result.text
