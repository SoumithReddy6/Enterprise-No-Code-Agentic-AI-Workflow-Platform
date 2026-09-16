"""Search service persistence, provenance and failed side-effect isolation."""
import asyncio
import json
import pytest
from cryptography.fernet import Fernet


def build(segment='seg1', backend='faiss', vectors=None):
    return {'kb_id':'kb1','version':1,'segment_id':segment,
        'config':{'backend':backend,'storage_path':'test','embedding_model':'' if vectors is None else 'test','index_name':'test'},
        'connection':{'endpoint':'https://example.com','secret':'TOP_SECRET'},
        'documents':[{'id':'doc1','filename':'one.txt','content_hash':'a'*64},{'id':'doc2','filename':'two.txt','content_hash':'b'*64}],
        'chunks':[{'id':segment+'c1','document_id':'doc1','ordinal':0,'page':1,'text':'apple apple pear'}, {'id':segment+'c2','document_id':'doc2','ordinal':0,'page':2,'text':'banana orange'}],
        'vectors':vectors or []}


@pytest.fixture
def service(tmp_path):
    from backend.app.kb.search import Search
    return Search('sqlite:///'+str(tmp_path/'search.db'),tmp_path/'artifacts',Fernet.generate_key())


def query(segment='seg1', **kwargs):
    return dict({'kb_id':'kb1','version':1,'segment_id':segment,'document_ids':['doc1','doc2'],'query':'apple','query_vector':[], 'options':{'mode':'keyword'}},**kwargs)


@pytest.mark.asyncio
async def test_keyword_persistent_idempotent_and_canonical(service):
    p=build()
    assert await service.call('build','alice',p)=={'segment_id':'seg1','chunk_count':2}
    assert await service.call('build','alice',p)=={'segment_id':'seg1','chunk_count':2}
    rows=await service.call('search','alice',query())
    assert len(rows)==1 and rows[0]['text']=='apple apple pear'
    assert rows[0]['knowledge_base_id']=='kb1' and rows[0]['version']==1
    assert rows[0]['url']=='/api/knowledge-bases/kb1/documents/doc1/file'
    assert await service.call('verify','alice',{'sources':rows})==rows
    assert await service.call('search','alice',query(document_ids=['doc2']))==[]
    preview=await service.call('preview','alice',{'kb_id':'kb1','segment_id':'seg1','document_id':'doc1','limit':10})
    assert preview['total']==1 and preview['sources'][0]['score']==0
    from backend.app.kb.search import Search
    reopened=Search(str(service.engine.url),service.root,service.secret_key)
    assert await reopened.call('search','alice',query())==rows
    p['chunks'][0]['text']='changed'
    with pytest.raises(ValueError):await service.call('build','alice',p)
    for bad in (dict(rows[0],text='forged'),dict(rows[0],version=2),dict(rows[0],url='/evil')):
        with pytest.raises((ValueError,KeyError)):await service.call('verify','alice',{'sources':[bad]})
    with pytest.raises((ValueError,KeyError)):await service.call('verify','bob',{'sources':rows})
    with pytest.raises((ValueError,KeyError)):await service.call('search','bob',query())
    with pytest.raises((ValueError,KeyError)):await service.call('search','alice',query(version=2))


@pytest.mark.asyncio
async def test_inventory_survives_partial_upsert_and_cleanup_failure(service, monkeypatch):
    from backend.app.vector_adapters import ADAPTERS
    class Remote:
        fail_cleanup=True
        artifacts=set()
        def __init__(self,*args):pass
        async def upsert(self,key,chunks,vectors):
            self.artifacts.add(key)
            raise RuntimeError('partial upload TOP_SECRET')
        async def remove(self,key):
            if self.fail_cleanup:raise RuntimeError('TOP_SECRET cleanup failed')
            self.artifacts.discard(key)
    monkeypatch.setitem(ADAPTERS,'pinecone',Remote)
    with pytest.raises(Exception):await service.call('build','alice',build(backend='pinecone',vectors=[[1.,0.],[0.,1.]]))
    with pytest.raises((ValueError,KeyError)):await service.call('search','alice',query())
    stats=await service.call('stats','alice',{'kb_id':'kb1'})
    assert 'TOP_SECRET' not in json.dumps(stats)
    from pathlib import Path
    assert b'TOP_SECRET' not in Path(service.engine.url.database).read_bytes()
    with pytest.raises(Exception):await service.call('cleanup','alice',{'kb_id':'kb1','segment_id':'seg1'})
    Remote.fail_cleanup=False
    await service.call('cleanup_pending','alice',{})
    assert not Remote.artifacts
    with pytest.raises(ValueError):await service.call('build','alice',build(backend='pinecone',vectors=[[1.,0.],[0.,1.]]))
    Remote.artifacts.add('seg1') # eventual remote write arrives after deletion
    await service.call('cleanup_pending','alice',{})
    assert not Remote.artifacts


@pytest.mark.asyncio
async def test_cleanup_before_build_tombstones_and_tenant_protection(service):
    await service.call('cleanup','alice',{'kb_id':'kb1','segment_id':'seg1'})
    with pytest.raises(ValueError):await service.call('build','alice',build())
    with pytest.raises(KeyError):await service.call('cleanup','bob',{'kb_id':'kb1','segment_id':'seg1'})


@pytest.mark.asyncio
@pytest.mark.parametrize('backend',['faiss','chroma'])
async def test_real_vector_backend_global_ordinals_and_cleanup(service,backend):
    await service.call('build','alice',build(backend=backend,vectors=[[1.,0.],[0.,1.]]))
    rows=await service.call('search','alice',query(query_vector=[0.,1.],options={'mode':'similarity','top_k':1}))
    assert rows[0]['document_id']=='doc2'
    await service.call('cleanup','alice',{'kb_id':'kb1','segment_id':'seg1'})
    with pytest.raises((KeyError,ValueError)):await service.call('verify','alice',{'sources':rows})


@pytest.mark.asyncio
async def test_inflight_build_serializes_cleanup_across_instances(service,monkeypatch):
    from backend.app.kb.search import Search
    from backend.app.vector_adapters import ADAPTERS
    started=asyncio.Event();release=asyncio.Event();artifacts=set()
    class Remote:
        def __init__(self,*args):pass
        async def upsert(self,key,chunks,vectors):
            started.set();await release.wait();artifacts.add(key)
        async def remove(self,key):artifacts.discard(key)
    monkeypatch.setitem(ADAPTERS,'pinecone',Remote)
    second=Search(str(service.engine.url),service.root,service.secret_key)
    task=asyncio.create_task(service.call('build','alice',build(backend='pinecone',vectors=[[1.,0.],[0.,1.]])))
    await started.wait()
    cleanup=asyncio.create_task(second.call('cleanup','alice',{'kb_id':'kb1','segment_id':'seg1'}))
    await asyncio.sleep(.05);assert not cleanup.done()
    release.set();await task;await cleanup
    assert not artifacts
    with pytest.raises(ValueError):await service.call('build','alice',build(backend='pinecone',vectors=[[1.,0.],[0.,1.]]))


@pytest.mark.asyncio
async def test_failed_write_reserves_chunk_identity_permanently(service,monkeypatch):
    from backend.app.vector_adapters import ADAPTERS
    class Remote:
        def __init__(self,*args):pass
        async def upsert(self,*args):raise RuntimeError('credentials secret')
        async def remove(self,*args):pass
    monkeypatch.setitem(ADAPTERS,'pinecone',Remote)
    first=build(backend='pinecone',vectors=[[1.,0.],[0.,1.]])
    with pytest.raises(Exception):await service.call('build','alice',first)
    second=build(segment='seg2')
    second['chunks'][0]['id']='seg1c1'
    with pytest.raises(ValueError):await service.call('build','alice',second)
    await service.call('cleanup','alice',{'kb_id':'kb1','segment_id':'seg1'})
    with pytest.raises(ValueError):await service.call('build','alice',second)


@pytest.mark.asyncio
async def test_backend_errors_never_expose_credentials(service,monkeypatch):
    from backend.app.vector_adapters import ADAPTERS
    class Remote:
        def __init__(self,*args):pass
        async def upsert(self,*args):raise ValueError('TOP_SECRET')
        async def remove(self,*args):raise ValueError('TOP_SECRET')
    monkeypatch.setitem(ADAPTERS,'pinecone',Remote)
    with pytest.raises(Exception) as error:await service.call('build','alice',build(backend='pinecone',vectors=[[1.,0.],[0.,1.]]))
    assert 'TOP_SECRET' not in str(error.value)
    with pytest.raises(Exception) as error:await service.call('cleanup','alice',{'kb_id':'kb1','segment_id':'seg1'})
    assert 'TOP_SECRET' not in str(error.value)


@pytest.mark.asyncio
async def test_capacity_counts_cleanup_pending_artifacts(service):
    from backend.app.kb.search_models import Segment
    from sqlalchemy.orm import Session
    # A durable reservation from an earlier failed cleanup still consumes capacity.
    with Session(service.engine) as session:
        session.add(Segment(id='prior',tenant_id='alice',kb_id='oldkb',version=1,state='tombstone',chunk_count=49999,created_at=0))
        session.commit()
    with pytest.raises(ValueError):await service.call('build','alice',build())
    # Workspace isolation: another owner's inventory cannot consume Bob's quota.
    assert (await service.call('build','bob',build()))['chunk_count']==2


@pytest.mark.asyncio
async def test_vector_workspace_capacity_is_reserved_before_side_effects(service):
    from backend.app.kb.search_models import Segment
    from sqlalchemy.orm import Session
    with Session(service.engine) as session:
        session.add(Segment(id='prior',tenant_id='alice',kb_id='oldkb',version=1,state='ready',chunk_count=4000,dimensions=2000,created_at=0))
        session.commit()
    with pytest.raises(ValueError):await service.call('build','alice',build(vectors=[[1.,0.],[0.,1.]]))


@pytest.mark.asyncio
@pytest.mark.parametrize('backend',['elasticsearch','pinecone'])
async def test_remote_adapter_ranking_and_isolated_cleanup(service,backend,monkeypatch):
    from backend.app.vector_adapters import ADAPTERS
    records={}
    async def request(self,method,path,**kwargs):
        if path.endswith('/_mapping'):return {'test':{'mappings':{'properties':{'vector':{'type':'dense_vector','dims':2,'similarity':'cosine'}}}}}
        if path=='/describe_index_stats':return {'dimension':2}
        if path=='/_bulk?refresh=wait_for':
            lines=kwargs['content'].decode().splitlines()
            for i in range(0,len(lines),2):records[json.loads(lines[i])['index']['_id']]=json.loads(lines[i+1])
            return {'errors':False}
        if path=='/vectors/upsert':
            for row in kwargs['json']['vectors']:records[row['id']]=row
            return {}
        if path=='/vectors/fetch':return {'vectors':records}
        if path.endswith('/_search'):return {'hits':{'hits':[{'_id':'seg1c2','_score':1.}]}}
        if path=='/query':return {'matches':[{'id':'seg1c2','score':1.}]}
        if path.endswith('/_delete_by_query') or path=='/vectors/delete':records.clear();return {}
        raise AssertionError('Unexpected remote operation: '+path)
    monkeypatch.setattr(ADAPTERS[backend],'request',request)
    await service.call('build','alice',build(backend=backend,vectors=[[1.,0.],[0.,1.]]))
    rows=await service.call('search','alice',query(query_vector=[0.,1.],options={'mode':'similarity','top_k':1}))
    assert rows[0]['document_id']=='doc2'
    assert await service.call('search','alice',query(document_ids=['doc1'],query_vector=[0.,1.],options={'mode':'similarity'}))==[]
    await service.call('cleanup','alice',{'kb_id':'kb1','segment_id':'seg1'})
    assert not records


@pytest.mark.asyncio
async def test_keyword_uses_persisted_postings_and_only_query_terms(service,monkeypatch):
    import backend.app.kb.search as module
    await service.call('build','alice',build())
    original=module.tokens
    def query_only(value):
        assert value=='apple', 'Search must not tokenize stored corpus text'
        return original(value)
    monkeypatch.setattr(module,'tokens',query_only)
    assert (await service.call('search','alice',query()))[0]['document_id']=='doc1'
    assert await service.call('search','alice',query(options={'mode':'keyword','filter':{'filename':'two.txt'}}))==[]
    with pytest.raises(ValueError):await service.call('search','alice',query(options={'mode':'keyword','top_k':21}))


@pytest.mark.asyncio
async def test_retired_source_archive_survives_cleanup_but_cannot_be_searched(service):
    await service.call('build','alice',build())
    saved=await service.call('search','alice',query())
    cleanup={'kb_id':'kb1','segment_id':'seg1','retain_provenance':True}
    await service.call('cleanup','alice',cleanup)
    assert await service.call('verify','alice',{'sources':saved})==saved
    await service.call('cleanup','alice',cleanup)
    await service.call('cleanup_pending','alice',{})
    assert await service.call('verify','alice',{'sources':saved})==saved
    assert (await service.call('stats','alice',{'kb_id':'kb1'}))['chunk_count']==0
    with pytest.raises((ValueError,KeyError)):await service.call('search','alice',query())
    with pytest.raises((ValueError,KeyError)):await service.call('preview','alice',{'kb_id':'kb1','segment_id':'seg1','document_id':'doc1'})
    with pytest.raises(ValueError):await service.call('build','alice',build())
    with pytest.raises((ValueError,KeyError)):await service.call('verify','bob',{'sources':saved})


@pytest.mark.asyncio
async def test_provenance_purge_is_permanent_even_after_stale_retain_cleanup(service):
    await service.call('build','alice',build())
    saved=await service.call('search','alice',query())
    cleanup={'kb_id':'kb1','segment_id':'seg1'}
    await service.call('cleanup','alice',dict(cleanup,retain_provenance=True))
    await service.call('cleanup','alice',dict(cleanup,retain_provenance=False))
    await service.call('cleanup','alice',dict(cleanup,retain_provenance=True))
    with pytest.raises((KeyError,ValueError)):await service.call('verify','alice',{'sources':saved})
    from backend.app.kb.search_models import Chunk, Posting
    from sqlalchemy import select,func
    from sqlalchemy.orm import Session
    with Session(service.engine) as session:
        assert session.scalar(select(func.count()).select_from(Chunk))==0
        assert session.scalar(select(func.count()).select_from(Posting))==0


@pytest.mark.asyncio
async def test_chroma_rebuild_can_change_dimensions(service):
    await service.call('build','alice',build(vectors=[[1.,0.],[0.,1.]],backend='chroma'))
    replacement=build(segment='seg2',vectors=[[1.,0.,0.],[0.,1.,0.]],backend='chroma')
    replacement['version']=2
    await service.call('build','alice',replacement)
    rows=await service.call('search','alice',query(segment='seg2',version=2,query_vector=[0.,1.,0.],options={'mode':'similarity','top_k':1}))
    assert rows[0]['document_id']=='doc2'


@pytest.mark.asyncio
async def test_old_keyword_search_does_not_wait_for_staging_upsert(service,monkeypatch):
    from backend.app.vector_adapters import ADAPTERS
    started=asyncio.Event();release=asyncio.Event()
    class Remote:
        def __init__(self,*args):pass
        async def upsert(self,*args):started.set();await release.wait()
        async def remove(self,*args):pass
    monkeypatch.setitem(ADAPTERS,'pinecone',Remote)
    await service.call('build','alice',build())
    staging=build(segment='seg2',backend='pinecone',vectors=[[1.,0.],[0.,1.]])
    staging['version']=2
    pending=asyncio.create_task(service.call('build','alice',staging))
    await started.wait()
    try:
        result=await asyncio.wait_for(service.call('search','alice',query()),timeout=.2)
        assert result[0]['document_id']=='doc1'
    finally:
        release.set();await pending


@pytest.mark.asyncio
async def test_same_kb_rebuild_gets_bounded_staging_headroom(service):
    from backend.app.kb.search_models import Segment
    from sqlalchemy.orm import Session
    with Session(service.engine) as session:
        session.add(Segment(id='old',tenant_id='alice',kb_id='kb1',version=1,state='ready',chunk_count=50000,dimensions=160,created_at=0))
        session.commit()
    replacement=build(segment='seg2');replacement['version']=2
    assert (await service.call('build','alice',replacement))['chunk_count']==2
    with Session(service.engine) as session:
        session.add(Segment(id='stale',tenant_id='alice',kb_id='kb1',version=0,state='tombstone',chunk_count=50000,dimensions=160,created_at=0))
        session.commit()
    third=build(segment='seg3');third['version']=3
    with pytest.raises(ValueError):await service.call('build','alice',third)


@pytest.mark.asyncio
@pytest.mark.parametrize('retain',[False,True])
async def test_query_overlapping_cleanup_fails_closed(service,monkeypatch,retain):
    from backend.app.vector_adapters import ADAPTERS
    started=asyncio.Event();release=asyncio.Event()
    class Remote:
        def __init__(self,*args):pass
        async def upsert(self,*args):pass
        async def remove(self,*args):pass
        async def search(self,*args):
            started.set();await release.wait();return [('seg1c1',1.)]
    monkeypatch.setitem(ADAPTERS,'pinecone',Remote)
    await service.call('build','alice',build(backend='pinecone',vectors=[[1.,0.],[0.,1.]]))
    pending=asyncio.create_task(service.call('search','alice',query(query_vector=[1.,0.],options={'mode':'similarity'})))
    await started.wait()
    await service.call('cleanup','alice',{'kb_id':'kb1','segment_id':'seg1','retain_provenance':retain})
    release.set()
    with pytest.raises((KeyError,ValueError)):await pending
