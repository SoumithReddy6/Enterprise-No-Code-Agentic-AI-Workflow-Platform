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
    from backend.app.knowledge import Knowledge
    knowledge=Knowledge(queue)
    base=knowledge.create('Postgres PDFs','pg-owner')
    document=knowledge.upload(base['id'],'facts.pdf',b'%PDF-test-fixture','pg-owner')
    document_id,owner=knowledge.claim()
    assert knowledge.publish(document_id,owner,['Caloris is a basin on Mercury.'])
    sources=knowledge.retrieve(base['id'],'Caloris',4,'pg-owner')
    assert sources[0]['document_id']==document['id'] and sources[0]['page']==1
    assert knowledge.verify_sources(sources,'pg-owner')==sources
    assert knowledge.download(document['id'],'pg-owner')[1]==b'%PDF-test-fixture'
    try:knowledge.verify_sources(sources,'pg-other')
    except ValueError:pass
    else:raise AssertionError('Foreign sources were accepted')
    knowledge.remove(document['id'],'pg-owner')
    assert knowledge.retrieve(base['id'],'Caloris',4,'pg-owner')==[]
    import asyncio
    from tempfile import TemporaryDirectory
    from backend.app.vector_service import VectorService
    from backend.app.tool_service import ToolService
    from backend.app.agent_memory import MemoryService
    async def vector_check():
        with TemporaryDirectory(prefix='relay-pg-vectors-') as directory:
            prior=os.environ.get('VECTOR_DATA_DIR');os.environ['VECTOR_DATA_DIR']=directory
            try:
                vectors=VectorService(queue)
                async def embed(model,texts):return [[1.,0.,1.] for _ in texts]
                vectors.embed=embed
                resource=vectors.create({'name':'Postgres vectors','embedding_model':'fixture'},'pg-owner')
                doc=vectors.upload(resource['id'],'facts.txt',b'Caloris is a basin on Mercury.','pg-owner')
                assert await vectors.process_once()
                sources=await vectors.retrieve(resource['id'],'Caloris',{'mode':'rrf'},'pg-owner')
                assert sources[0]['document_id']==doc['id']
                assert vectors.verify_sources(sources,'pg-owner')==sources
                try:vectors.verify_sources(sources,'pg-other')
                except ValueError:pass
                else:raise AssertionError('Foreign vector sources were accepted')
                vectors.remove(doc['id'],'pg-owner');await vectors.cleanup()
                assert await vectors.retrieve(resource['id'],'Caloris',{'mode':'keyword'},'pg-owner')==[]
            finally:
                if prior is None:os.environ.pop('VECTOR_DATA_DIR',None)
                else:os.environ['VECTOR_DATA_DIR']=prior
    asyncio.run(vector_check())
    tools=ToolService(queue)
    connection=tools.save_connection({'name':'Read-only fixture','provider':'http','endpoint':'https://example.com','secret':'fixture'},'pg-owner')
    assert tools.resolve(connection['id'],'pg-owner')['secret']=='fixture'
    assert tools.connections('pg-other')==[]
    memory=MemoryService(queue);memory.write('context','question','answer','pg-owner')
    assert 'answer' in memory.read('context','pg-owner') and memory.read('context','pg-other')=='[]'
    queue.engine.dispose()
    print('PostgreSQL verified: workflows/runs, tenant isolation, ownership, leases, fencing, PDF/vector indexing, removal, encrypted connections and agent memory. Remote calls were not made.')
finally:
    with engine.begin() as conn: conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    engine.dispose()
