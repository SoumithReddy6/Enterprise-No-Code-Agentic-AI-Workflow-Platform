"""Verify the project-owned local PostgreSQL service using an isolated schema."""
import os
import uuid
from sqlalchemy import create_engine, text
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.main import create_app as _create_app
from functools import partial
create_app = partial(_create_app, auth_enabled=False)
from backend.tests.test_compiler import sample
from backend.tests.test_api import wait_run

url=os.environ.get('DATABASE_URL','postgresql+psycopg://relay:relay_local@127.0.0.1:55432/relay')
engine=create_engine(url)
schema='verify_'+uuid.uuid4().hex
with engine.begin() as conn: conn.execute(text(f'CREATE SCHEMA {schema}'))
try:
    scoped=url+('?options=-csearch_path%3D'+schema)
    key=Fernet.generate_key()
    with TestClient(create_app(scoped,key)) as client:
        saved=client.post('/api/workflows',json=sample()).json()
        result=client.post('/api/runs',json={'workflow':sample(),'message':'Postgres'})
        run=wait_run(client,result.json()['id'])
        assert run['status']=='success' and run['output']=='Hello Postgres'
        assert len(run['events'])==8
    with TestClient(create_app(scoped,key)) as client:
        assert client.get('/api/workflows/'+saved['id']).json()['workflow']['name']=='Greeting'
        assert client.get('/api/runs/'+run['id']).json()['output']=='Hello Postgres'
    with TestClient(_create_app(scoped,key)) as client:
        assert client.post('/api/auth/register',json={'email':'pg-one@example.com','password':'postgres-test-123'}).status_code==201
        assert len(client.get('/api/workflows').json())==1
        client.post('/api/auth/logout')
        assert client.post('/api/auth/register',json={'email':'pg-two@example.com','password':'postgres-test-456'}).status_code==201
        assert client.get('/api/workflows').json()==[]
        assert client.get('/api/runs/'+run['id']).status_code==404
    from backend.app.storage import Store
    import time
    queue=Store(scoped,key)
    pending=queue.create_run(sample(),'Recovery')
    assert queue.claim_next('expired',lease_seconds=.01)['id']==pending['id']
    time.sleep(.02)
    assert queue.claim_next('replacement')['id']==pending['id']
    assert queue.finish_run(pending['id'],'expired','success') is False
    assert queue.finish_run(pending['id'],'replacement','success') is True
    from backend.app.tool_service import ToolService
    from backend.app.agent_memory import MemoryService
    tools=ToolService(queue)
    connection=tools.save_connection({'name':'Read-only fixture','provider':'http','endpoint':'https://example.com','secret':'fixture'},'pg-owner')
    assert tools.resolve(connection['id'],'pg-owner')['secret']=='fixture'
    assert tools.connections('pg-other')==[]
    memory=MemoryService(queue);memory.write('context','question','answer','pg-owner')
    assert 'answer' in memory.read('context','pg-owner') and memory.read('context','pg-other')=='[]'
    queue.engine.dispose()
    print('PostgreSQL verified: workflows/runs, tenant isolation, ownership, leases, fencing, encrypted connections and agent memory. Knowledge services are covered by scripts.check_kb_postgres. Remote calls were not made.')
finally:
    with engine.begin() as conn: conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    engine.dispose()
