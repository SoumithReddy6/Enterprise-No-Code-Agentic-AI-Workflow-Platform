import base64
import json
import pytest
from cryptography.fernet import Fernet
from backend.app.kb.management import Management
from backend.app.kb.search import Search
from backend.app.kb.ingestion import Ingestion
from backend.app.kb_gateway import KnowledgeServices,install_kb_routes
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_kb_workflow import graph

class Local:
    def __init__(self,domain):self.domain=domain
    async def call(self,action,tenant='local',payload=None):
        result=self.domain.call(action,tenant,payload or {})
        import inspect
        return await result if inspect.isawaitable(result) else result
    def call_sync(self,action,tenant='local',payload=None):return self.domain.call(action,tenant,payload or {})

@pytest.fixture
def services(tmp_path):
    key=Fernet.generate_key()
    management=Management(f'sqlite:///{tmp_path}/management.db',tmp_path/'files',key)
    search=Search(f'sqlite:///{tmp_path}/search.db',tmp_path/'indexes',key)
    services=KnowledgeServices(Local(management),Local(search))
    yield services
    management.engine.dispose();search.engine.dispose()

def text_pdf():
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(612,792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 18 Tf 50 700 Td (Mercury contains the large Caloris basin.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    out=BytesIO();writer.write(out);return out.getvalue()

async def populated(services):
    kb=await services.management.call('create','local',{'name':'Policies','config':{'search_defaults':{'mode':'keyword'}}})
    doc=await services.management.call('upload','local',{'kb_id':kb['id'],'filename':'policy.txt','content_b64':base64.b64encode(b'Bluebird is the project codename.').decode(),'idempotency_key':'upload1'})
    worker=Ingestion(services.management,services.index)
    job=await services.management.call('claim','system',{})
    await worker.execute(job)
    detail=await services.management.call('get','local',{'kb_id':kb['id']})
    assert detail['status']=='ready',detail
    return kb,doc,worker

@pytest.mark.asyncio
async def test_full_ingestion_named_retrieve_agent_and_deleted_provenance(services,tmp_path,monkeypatch):
    async def model(inputs,config,ctx):
        return {'text':'{"answer":"Bluebird [S1]","citations":["S1"],"abstain":false,"reason":""}','provider':'demo'}
    monkeypatch.setattr('backend.app.registry.llm_node',model)
    kb,doc,ingestion=await populated(services)
    result=await services.retrieve(kb['id'],'codename',{'mode':'keyword'},'local')
    assert result['sources'][0]['text']=='Bluebird is the project codename.'
    store=Store(f'sqlite:///{tmp_path}/relay.db',Fernet.generate_key());worker=Worker(store);worker.kbs=services
    workflow=graph();workflow.nodes[1].config['knowledge_base_id']=kb['id']
    run=store.create_run(workflow.model_dump(),'codename')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='success',saved.get('error')
    assert 'Bluebird' in saved['output'] and saved['vector_dependencies']['retrieve']
    await services.management.call('remove_document','local',{'kb_id':kb['id'],'document_id':doc['id']})
    with pytest.raises((KeyError,ValueError)):await services.verify(result['sources'],'local')
    store.engine.dispose()

@pytest.mark.asyncio
async def test_rebuild_retains_old_results_until_publish_and_cleanup(services):
    kb,doc,worker=await populated(services)
    old=await services.retrieve(kb['id'],'codename',{'mode':'keyword'},'local')
    await services.management.call('rebuild','local',{'kb_id':kb['id'],'config':{'chunk_size':500,'chunk_overlap':100}})
    assert (await services.retrieve(kb['id'],'codename',{'mode':'keyword'},'local'))['version']==old['version']
    await worker.execute(await services.management.call('claim','system',{}))
    current=await services.retrieve(kb['id'],'codename',{'mode':'keyword'},'local')
    assert current['version']!=old['version']
    await worker.cleanup()
    assert await services.verify(old['sources'],'local')==old['sources']

@pytest.mark.asyncio
async def test_nonready_is_not_empty_search(services):
    kb=await services.management.call('create','local',{'name':'Empty','config':{}})
    with pytest.raises(ValueError):await services.retrieve(kb['id'],'q',{'mode':'keyword'},'local')
    with pytest.raises(KeyError):await services.retrieve(kb['id'],'q',{'mode':'keyword'},'foreign')

@pytest.mark.asyncio
async def test_pdf_extraction_works_under_service_module_path(services):
    worker=Ingestion(services.management,services.index)
    pages=await worker.extract('facts.pdf',text_pdf())
    assert any('Mercury' in page for page in pages)

@pytest.mark.asyncio
async def test_gateway_upload_idempotency_search_download_and_authorization(services,tmp_path):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    with TestClient(create_app(f'sqlite:///{tmp_path}/gateway.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False,knowledge_services=services)) as client:
        response=client.post('/api/knowledge-bases',json={'name':'API KB','config':{'search_defaults':{'mode':'keyword'}}})
        assert response.status_code==201,response.text
        kb=response.json();route=f'/api/knowledge-bases/{kb["id"]}'
        upload=client.post(route+'/documents?filename=facts.txt',content=b'Bluebird is the codename.',headers={'Idempotency-Key':'once'})
        assert upload.status_code==202,upload.text
        again=client.post(route+'/documents?filename=facts.txt',content=b'Bluebird is the codename.',headers={'Idempotency-Key':'once'})
        assert again.json()['id']==upload.json()['id']
        job=await services.management.call('claim','system',{})
        await Ingestion(services.management,services.index).execute(job)
        response=client.post(route+'/search',json={'query':'Bluebird'})
        assert response.status_code==200 and response.json()['sources'],response.text
        download=client.get(response.json()['sources'][0]['url'])
        assert download.content==b'Bluebird is the codename.' and download.headers['x-content-type-options']=='nosniff'
        preview=client.get(route+'/documents/'+upload.json()['id']+'/preview')
        assert preview.status_code==200 and preview.json()['total']==1
        foreign=await services.management.call('create','someoneelse',{'name':'Other','config':{}})
        assert client.get('/api/knowledge-bases/'+foreign['id']).status_code==404
        assert client.post('/api/knowledge-bases/'+foreign['id']+'/search',json={'query':'Bluebird'}).status_code==404
        assert client.post(route+'/documents/'+upload.json()['id']+'/remove').status_code==200
        assert client.get(response.json()['sources'][0]['url']).status_code==404

@pytest.mark.asyncio
async def test_chat_models_are_refused_as_knowledge_base_embedders(services,tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    async def fingerprint(model,require_embedding=False):
        if require_embedding and model=='chatty':raise ValueError('chatty is a chat model and cannot produce embeddings.')
        return 'digest'
    monkeypatch.setattr(services.embeddings,'fingerprint',fingerprint)
    with TestClient(create_app(f'sqlite:///{tmp_path}/embed.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False,knowledge_services=services)) as client:
        refused=client.post('/api/knowledge-bases',json={'name':'Bad','config':{'embedding_model':'chatty'}})
        assert refused.status_code==400 and 'chat model' in refused.text
        assert client.post('/api/knowledge-bases',json={'name':'Good','config':{'embedding_model':'embedder'}}).status_code==201
