import pytest
from cryptography.fernet import Fernet
from backend.app.storage import Store,Base
from backend.app.vector_service import VectorService

@pytest.fixture
def service(tmp_path,monkeypatch):
    store=Store('sqlite:///'+str(tmp_path/'test.db'),Fernet.generate_key())
    Base.metadata.create_all(store.engine)
    monkeypatch.setenv('VECTOR_DATA_DIR',str(tmp_path/'vectors'))
    service=VectorService(store)
    async def embed(model,texts):return [[float('apple' in t.lower()),float('banana' in t.lower()),1.] for t in texts]
    service.embed=embed
    return service

def config(**kw):return dict(name='Fruit',backend='faiss',embedding_model='mock',**kw)

def test_paths_and_tenant(service):
    with pytest.raises(ValueError):service.create(config(storage_path='../escape'),'a')
    r=service.create(config(),'a')
    with pytest.raises(KeyError):service.resource(r['id'],'b')
    assert service.resources('b')==[]

@pytest.mark.asyncio
async def test_index_retrieve_remove(service):
    r=service.create(config(),'a');d=service.upload(r['id'],'fruit.txt',b'Apple orchard apples.','a')
    assert service.files(r['id'],'a')[0]['status']=='queued'
    assert await service.process_once()
    assert service.files(r['id'],'a')[0]['status']=='ready'
    sources=await service.retrieve(r['id'],'apple',{},'a')
    assert sources and sources[0]['document_id']==d['id']
    assert service.verify_sources(sources,'a')==sources
    with pytest.raises(ValueError):service.verify_sources(sources,'b')
    forged=[dict(sources[0],text='fake')]
    with pytest.raises(ValueError):service.verify_sources(forged,'a')
    service.remove(d['id'],'a')
    assert await service.retrieve(r['id'],'apple',{},'a')==[]

@pytest.mark.asyncio
async def test_failed_no_partial(service):
    r=service.create(config(),'a');service.upload(r['id'],'fruit.txt',b'apple','a')
    async def bad(*args):raise ValueError('missing model')
    service.embed=bad
    await service.process_once()
    assert service.files(r['id'],'a')[0]['status']=='failed'
    assert await service.retrieve(r['id'],'apple',{'mode':'keyword'},'a')==[]

def test_unsupported_and_config(service):
    r=service.create(config(),'a')
    with pytest.raises(ValueError):service.upload(r['id'],'a.exe',b'abc','a')
    with pytest.raises(ValueError):service.create(config(chunk_overlap=1500),'a')
    with pytest.raises(ValueError):service.create(config(index_method='hnsw'),'a')

@pytest.mark.asyncio
async def test_chroma_real_and_modes_filters(service):
    r=service.create(dict(config(),backend='chroma'),'a')
    apple=service.upload(r['id'],'apple.txt',b'apple orchard green','a')
    banana=service.upload(r['id'],'banana.txt',b'banana fruit yellow','a')
    await service.process_once();await service.process_once()
    assert all(d['status']=='ready' for d in service.files(r['id'],'a')),service.files(r['id'],'a')
    for mode in ('similarity','keyword','hybrid','rrf'):
        result=await service.retrieve(r['id'],'apple',{'mode':mode,'filter':{'document_id':apple['id']}},'a')
        assert result and {s['document_id'] for s in result}=={apple['id']}
    assert await service.retrieve(r['id'],'apple',{'mode':'keyword','filter':{'filename':'absent'}},'a')==[]
    with pytest.raises(ValueError):await service.retrieve(r['id'],'apple',{'filter':{'tenant_id':'b'}},'a')

@pytest.mark.asyncio
async def test_dimension_drift(service):
    r=service.create(config(),'a');service.upload(r['id'],'apple.txt',b'apple','a');await service.process_once()
    async def drift(*args):return [[1.,2.]]
    service.embed=drift
    with pytest.raises(ValueError,match='dimensions changed'):await service.retrieve(r['id'],'apple',{},'a')

@pytest.mark.asyncio
async def test_removed_during_index_cannot_publish(service):
    r=service.create(config(),'a');d=service.upload(r['id'],'apple.txt',b'apple','a')
    original=service.embed
    async def removed(*args):
        service.remove(d['id'],'a');return await original(*args)
    service.embed=removed
    await service.process_once()
    assert service.files(r['id'],'a')==[]
    assert await service.retrieve(r['id'],'apple',{'mode':'keyword'},'a')==[]

@pytest.mark.asyncio
async def test_remote_wire_protocols(tmp_path):
    from backend.app.vector_adapters import ElasticsearchAdapter,PineconeAdapter
    chunks=[{'id':'chunk1','text':'apple'}];vectors=[[1.,2.]]
    resource={'id':'resource1','index_name':'test-index'}
    c={'endpoint':'https://vector.example','secret':'secret'}
    calls=[]
    es=ElasticsearchAdapter(tmp_path,resource,c)
    async def es_request(method,path,**kw):
        calls.append((method,path,kw))
        if path.endswith('_mapping'):return {'test-index':{'mappings':{'properties':{'vector':{'type':'dense_vector','dims':2,'similarity':'cosine'}}}}}
        if path.endswith('_search'):return {'hits':{'hits':[{'_id':'chunk1','_score':.9}]}}
        return {'errors':False}
    es.request=es_request
    await es.upsert('segment1',chunks,vectors)
    assert await es.search([1.,2.],[{'id':'chunk1'}],4)==[('chunk1',.8)]
    assert calls[-1][2]['json']['knn']['filter']=={'ids':{'values':['chunk1']}}
    assert b'"resource_id": "resource1"' in calls[1][2]['content']
    pc=PineconeAdapter(tmp_path,resource,c)
    async def pc_request(method,path,**kw):
        calls.append((method,path,kw))
        if path=='/describe_index_stats':return {'dimension':2}
        if path=='/vectors/fetch':return {'vectors':{'chunk1':{}}}
        if path=='/query':return {'matches':[{'id':'chunk1','score':.8}]}
        return {}
    pc.request=pc_request
    await pc.upsert('segment1',chunks,vectors)
    assert await pc.search([1.,2.],[{'id':'chunk1'}],4)==[('chunk1',.8)]
    assert calls[-1][2]['json']['namespace']=='resource1'
    assert calls[-1][2]['json']['filter']=={'chunk_id':{'$in':['chunk1']}}
    await pc.remove('segment1')
    assert calls[-1][2]['json']['filter']=={'segment':{'$eq':'segment1'}}

@pytest.mark.asyncio
async def test_file_parsers(service):
    import io,zipfile
    content=io.BytesIO()
    with zipfile.ZipFile(content,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>apple docx</w:t></w:r></w:p></w:document>')
    r=service.create(config(),'a')
    for filename,data in [('a.docx',content.getvalue()),('a.html',b'<script>hidden</script><p>apple html</p>'),('a.json',b'{"fruit":"apple"}'),('a.csv',b'fruit\napple'),('a.md',b'# apple')]:
        service.upload(r['id'],filename,data,'a');await service.process_once()
    assert all(d['status']=='ready' for d in service.files(r['id'],'a'))
    assert len(await service.retrieve(r['id'],'apple',{'mode':'keyword','top_k':10},'a'))==5

def test_api_permissions_and_caps(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.vector_api import install_vector_routes
    app=FastAPI();install_vector_routes(app,service,lambda:'a');client=TestClient(app)
    r=client.post('/api/vector-resources',json=config()).json()
    response=client.post('/api/vector-resources/'+r['id']+'/files?filename=a.txt',content=b'apple')
    assert response.status_code==202
    assert client.get('/api/vector-files/'+response.json()['id']+'/file').content==b'apple'
    other=service.create(config(),'b')
    assert client.get('/api/vector-resources/'+other['id']+'/files').status_code==404
    assert client.post('/api/vector-resources/'+r['id']+'/files?filename=a.exe',content=b'bytes').status_code==400
    assert client.get('/api/vector-capabilities').json()['backends']['faiss']['index_methods']==['flat']

def test_duplicate_logical_paths_rejected(service):
    service.create(config(),'a')
    with pytest.raises(ValueError,match='already exists'):service.create(config(),'a')
    assert service.create(config(storage_path='separate'),'a')['storage_path']=='separate'
    assert service.create(config(),'b')['id']
    assert service.create(dict(config(),backend='chroma'),'a')['backend']=='chroma'

@pytest.mark.asyncio
async def test_retry_preserves_garbage_and_published_segment(service):
    from backend.app.vector_service import VectorFile,VectorSegment
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    r=service.create(config(),'a');d=service.upload(r['id'],'apple.txt',b'apple','a')
    original=service.adapter;removed=[]
    class Failing:
        async def upsert(self,key,chunks,vectors):raise ValueError('partial index')
        async def remove(self,key):removed.append(key)
    service.adapter=lambda *args:Failing()
    await service.process_once()
    with Session(service.store.engine) as s:old=s.get(VectorFile,d['id']).index_key
    assert old
    service.retry(d['id'],'a')
    service.adapter=original
    await service.process_once()
    with Session(service.store.engine) as s:
        current=s.get(VectorFile,d['id']).index_key
        assert current!=old
        assert s.get(VectorSegment,old)
    service.adapter=lambda *args:Failing()
    with service.transaction() as s:
        for segment in s.scalars(select(VectorSegment)):segment.next_cleanup=0
    await service.cleanup()
    assert old in removed and current not in removed
    service.adapter=original
    assert await service.retrieve(r['id'],'apple',{},'a')

@pytest.mark.asyncio
async def test_stale_claim_preserves_attempt(service):
    from backend.app.vector_service import VectorFile,VectorSegment
    from sqlalchemy.orm import Session
    r=service.create(config(),'a');d=service.upload(r['id'],'apple.txt',b'apple','a')
    id,old=service.claim()
    with service.transaction() as s:
        row=s.get(VectorFile,id);row.index_key=old;row.lease_until=0
    id,new=service.claim()
    assert old!=new
    with Session(service.store.engine) as s:assert s.get(VectorSegment,old)

@pytest.mark.asyncio
async def test_embedding_invalid_shape_and_proxy(service,monkeypatch):
    import httpx
    original=httpx.AsyncClient
    seen=[]
    def respond(request):
        return httpx.Response(200,json={'models':[{'name':'mock'}]} if request.url.path=='/api/tags' else {'embeddings':[42]})
    def client(**kwargs):
        seen.append(kwargs);return original(**kwargs,transport=httpx.MockTransport(respond))
    monkeypatch.setattr(httpx,'AsyncClient',client)
    with pytest.raises(ValueError,match='Invalid embedding'):await VectorService.embed(service,'mock',['apple'])
    assert seen[0]['trust_env'] is False

@pytest.mark.asyncio
async def test_hybrid_zero_weight_and_negative_cosine(service):
    r=service.create(config(),'a')
    service.upload(r['id'],'apple.txt',b'apple','a');service.upload(r['id'],'banana.txt',b'banana','a')
    await service.process_once();await service.process_once()
    sources=await service.retrieve(r['id'],'apple',{'mode':'hybrid','vector_weight':0},'a')
    assert len(sources)==1 and sources[0]['filename']=='apple.txt'
    class Negative:
        async def search(self,vector,rows,limit):return [(row['id'],-.8 if row['text']=='apple' else -.2) for row in rows]
    service.adapter=lambda *args:Negative()
    sources=await service.retrieve(r['id'],'unused',{'mode':'hybrid','vector_weight':1},'a')
    assert [s['filename'] for s in sources]==['banana.txt','apple.txt']
    assert [s['score'] for s in sources]==pytest.approx([.4,.1])

def test_concurrent_duplicate_creation_serialized(service):
    from concurrent.futures import ThreadPoolExecutor
    def create_one(_):
        try:return service.create(config(),'a')['id']
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(create_one,range(2)))
    assert sum(value is not None for value in results)==1
    assert len(service.resources('a'))==1

@pytest.mark.asyncio
async def test_tombstone_cleans_late_writer_again_and_keeps_active(service):
    from backend.app.vector_service import VectorFile,VectorSegment
    r=service.create(config(),'a');d=service.upload(r['id'],'apple.txt',b'apple','a')
    id,old=service.claim()
    with service.transaction() as s:
        row=s.get(VectorFile,id);row.index_key=old;row.lease_until=0
    id,new=service.claim()
    with service.transaction() as s:
        row=s.get(VectorFile,id);row.index_key=new;service._track_segment(s,row,new)
    deleted=[]
    class Cleaner:
        async def remove(self,key):deleted.append(key)
    service.adapter=lambda *args:Cleaner()
    await service.cleanup()
    assert deleted==[old]
    # The old process may finish its upsert after this deletion; retained inventory retries.
    with service.transaction() as s:s.get(VectorSegment,old).next_cleanup=0
    await service.cleanup()
    assert deleted==[old,old] and new not in deleted
