"""Exercise service ownership/version cleanup against disposable PostgreSQL schemas."""
import asyncio
import base64
import os
import uuid
from tempfile import TemporaryDirectory
from sqlalchemy import create_engine,text,inspect
from cryptography.fernet import Fernet
from backend.app.kb.management import Management
from backend.app.kb.search import Search
from backend.app.kb.ingestion import Ingestion
from backend.app.kb_gateway import KnowledgeServices

class Local:
    def __init__(self,domain):self.domain=domain
    async def call(self,action,tenant='local',payload=None):
        value=self.domain.call(action,tenant,payload or {})
        import inspect
        return await value if inspect.isawaitable(value) else value

async def check(management,search):
    service=KnowledgeServices(Local(management),Local(search));worker=Ingestion(service.management,service.index)
    kb=management.call('create','owner',{'name':'Postgres KB','config':{'search_defaults':{'mode':'keyword'}}})
    doc=management.call('upload','owner',{'kb_id':kb['id'],'filename':'facts.txt','content_b64':base64.b64encode(b'Bluebird is the project codename.').decode(),'idempotency_key':'pg-upload'})
    await worker.execute(management.call('claim','system',{}))
    first=await service.retrieve(kb['id'],'Bluebird',{'mode':'keyword'},'owner');assert first['sources']
    management.call('rebuild','owner',{'kb_id':kb['id'],'config':{'chunk_size':300,'chunk_overlap':100}})
    assert (await service.retrieve(kb['id'],'Bluebird',{'mode':'keyword'},'owner'))['version']==first['version']
    await worker.execute(management.call('claim','system',{}));await worker.cleanup()
    assert await service.verify(first['sources'],'owner')
    try:await service.verify(first['sources'],'foreign')
    except (KeyError,ValueError):pass
    else:raise AssertionError('Foreign evidence accepted')
    management.call('remove_document','owner',{'kb_id':kb['id'],'document_id':doc['id']})
    try:await service.verify(first['sources'],'owner')
    except (KeyError,ValueError):pass
    else:raise AssertionError('Removed evidence accepted')
    management.call('delete','owner',{'kb_id':kb['id']});await worker.cleanup()
    assert not management.call('list','owner',{})
    print('PostgreSQL service-owned schemas: ingestion, active-version rebuild, archive cleanup, tenant isolation, deletion and source revocation passed.')

def main():
    url=os.environ.get('KB_POSTGRES_TEST_URL','postgresql+psycopg://relay:relay_local@127.0.0.1:55432/relay')
    engine=create_engine(url);schemas=['kb_m_'+uuid.uuid4().hex,'kb_s_'+uuid.uuid4().hex]
    with engine.begin() as conn:
        for schema in schemas:conn.execute(text('CREATE SCHEMA '+schema))
    try:
        with TemporaryDirectory(prefix='relay-kb-pg-') as root:
            key=Fernet.generate_key()
            management=Management(url+'?options=-csearch_path%3D'+schemas[0],root,key)
            search=Search(url+'?options=-csearch_path%3D'+schemas[1],root,key)
            try:
                assert all(n.startswith('kb_management_') for n in inspect(management.engine).get_table_names())
                assert all(not n.startswith('kb_management_') for n in inspect(search.engine).get_table_names())
                asyncio.run(check(management,search))
            finally:management.engine.dispose();search.engine.dispose()
    finally:
        with engine.begin() as conn:
            for schema in schemas:conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        engine.dispose()
if __name__=='__main__':main()
