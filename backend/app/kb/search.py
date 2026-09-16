"""Immutable attempt indexes, durable cleanup, canonical evidence and BM25.

Only the gateway's management resolution determines which segment/documents may
be searched. This service never reads the management database. A tenant mutex
covers vector side effects as well as SQL commits; PostgreSQL advisory locks or
SQLite flock are released by the OS on process exit. Inventories and permanent
tombstones survive crashes and remote eventually-consistent late writes.
"""
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import hashlib
import json
import math
from pathlib import Path
import re
import time

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select, delete, func, text
from sqlalchemy.orm import Session
from .search_models import SQLBase, Segment, Chunk, Posting, ChunkOwner
from ..vector_adapters import ADAPTERS, CAPABILITIES
from .options import RetrievalOptions


_ID = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

def identifier(value):
    if not isinstance(value,str) or not _ID.fullmatch(value):
        raise ValueError('Invalid identifier')
    return value


def tokens(value):
    return re.findall(r'\w+',value.lower(),flags=re.UNICODE)


class Search:
    actions = {'build','search','verify','cleanup','cleanup_pending','stats','preview'}

    def __init__(self,database_url,root,secret_key):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        self.secret_key=secret_key
        self.cipher=Fernet(secret_key.encode() if isinstance(secret_key,str) else secret_key)
        if database_url.startswith('postgresql://'):
            database_url=database_url.replace('postgresql://','postgresql+psycopg://',1)
        self.engine=create_engine(database_url,connect_args={'check_same_thread':False,'timeout':30} if database_url.startswith('sqlite') else {})
        SQLBase.metadata.create_all(self.engine)

    @asynccontextmanager
    async def _lock(self,tenant):
        # Tenant-wide serialization also makes workspace capacity reservations atomic.
        lock_hash=hashlib.sha256(('kb-search:'+tenant).encode()).digest()
        if self.engine.dialect.name=='postgresql':
            conn=self.engine.connect();key=int.from_bytes(lock_hash[:8],'big',signed=True)
            acquired=False
            try:
                while not conn.execute(text('SELECT pg_try_advisory_lock(:key)'),{'key':key}).scalar():
                    await asyncio.sleep(.025)
                acquired=True
                yield
            finally:
                if acquired:conn.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':key})
                conn.close()
        else:
            import fcntl
            # Lock directory follows the SQLite database, so different root settings
            # cannot silently produce independent locks for the same inventory.
            database=self.engine.url.database
            directory=Path(database).resolve().parent/'.kb-search-locks' if database and database!=':memory:' else self.root/'.locks'
            directory.mkdir(parents=True,exist_ok=True)
            with (directory/lock_hash.hex()).open('a') as handle:
                while True:
                    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                    except BlockingIOError:await asyncio.sleep(.025)
                try:yield
                finally:fcntl.flock(handle,fcntl.LOCK_UN)

    async def call(self,action,tenant,payload):
        if action not in self.actions:raise ValueError('Unknown search action')
        identifier(tenant)
        if not isinstance(payload,dict):raise ValueError('Invalid payload')
        if action in {'search','preview','verify','stats'}:
            return await getattr(self,'_'+action)(tenant,payload)
        async with self._lock(tenant):
            return await getattr(self,'_'+action)(tenant,payload)

    def _segment(self,session,tenant,kb_id,segment_id,ready=False,version=None):
        identifier(kb_id);identifier(segment_id)
        row=session.get(Segment,segment_id)
        if row is None or row.tenant_id!=tenant or row.kb_id!=kb_id:raise KeyError('Segment unavailable')
        if ready and row.state!='ready':raise ValueError('Segment is not ready')
        if version is not None and (type(version) is not int or row.version!=version):raise ValueError('Segment version mismatch')
        return row

    def _adapter(self,segment):
        config=segment.config
        connection=json.loads(self.cipher.decrypt(segment.connection)) if segment.connection else {}
        # Namespaces and paths are generated from trusted ownership, not supplied paths.
        namespace=segment.tenant_id+':'+segment.kb_id
        if config['backend']=='chroma':namespace+=':'+segment.id
        resource_id=hashlib.sha256(namespace.encode()).hexdigest()[:32]
        resource=dict(config,id=resource_id)
        return ADAPTERS[config['backend']](self.root/resource_id,resource,connection)

    def _validate_build(self,p):
        identifier(p['kb_id']);identifier(p['segment_id'])
        if type(p.get('version')) is not int or p['version']<1:raise ValueError('Invalid version')
        config=p.get('config',{})
        if not isinstance(config,dict) or config.get('backend') not in CAPABILITIES:raise ValueError('Invalid backend')
        if config.get('index_method') and config['index_method'] not in CAPABILITIES[config['backend']]['index_methods']:raise ValueError('Unsupported index method')
        if not isinstance(p.get('connection',{}),dict):raise ValueError('Invalid connection')
        # Never permit embedded credential fields in cleartext config storage.
        allowed={'backend','storage_path','embedding_model','embedding_digest','chunking','chunk_size','chunk_overlap','index_method','connection_id','index_name','search_defaults'}
        if set(config)-allowed:raise ValueError('Unknown configuration fields')
        docs=p.get('documents');chunks=p.get('chunks');vectors=p.get('vectors',[])
        if not isinstance(docs,list) or len(docs)>20 or not isinstance(chunks,list) or len(chunks)>50000:raise ValueError('Index capacity exceeded')
        documents={}
        for d in docs:
            identifier(d['id'])
            if d['id'] in documents:raise ValueError('Duplicate document')
            if not isinstance(d.get('filename'),str) or not 1<=len(d['filename'])<=240:raise ValueError('Invalid filename')
            if not isinstance(d.get('content_hash'),str) or len(d['content_hash'])>64:raise ValueError('Invalid document hash')
            documents[d['id']]=d
        ids=set()
        for c in chunks:
            identifier(c['id'])
            if c['id'] in ids or c.get('document_id') not in documents:raise ValueError('Invalid chunk document or duplicate chunk')
            ids.add(c['id'])
            if type(c.get('ordinal')) is not int or c['ordinal']<0 or type(c.get('page')) is not int or not 1<=c['page']<=200:raise ValueError('Invalid chunk position')
            if not isinstance(c.get('text'),str) or not 1<=len(c['text'])<=100000:raise ValueError('Invalid chunk text')
        if not isinstance(vectors,list) or (vectors and len(vectors)!=len(chunks)):raise ValueError('Vector count mismatch')
        dimensions=0
        if vectors:
            if not isinstance(vectors[0],list):raise ValueError('Invalid vectors')
            dimensions=len(vectors[0])
            if not 1<=dimensions<=65536 or len(chunks)*dimensions>8_000_000:raise ValueError('Vector memory budget exceeded')
            for v in vectors:
                if not isinstance(v,list) or len(v)!=dimensions or any(type(x) not in (int,float) or not math.isfinite(x) for x in v) or not any(v):raise ValueError('Invalid vectors')
        return documents,dimensions

    async def _build(self,tenant,p):
        documents,dimensions=self._validate_build(p)
        digest=hashlib.sha256(json.dumps(p,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        with Session(self.engine,expire_on_commit=False) as s:
            segment=s.get(Segment,p['segment_id'])
            if segment:
                self._segment(s,tenant,p['kb_id'],p['segment_id'])
                if segment.state=='tombstone':raise ValueError('Segment has been retired')
                if segment.build_hash!=digest:raise ValueError('Conflicting build payload')
                if segment.state=='ready':return {'segment_id':segment.id,'chunk_count':segment.chunk_count}
            # Bound physical staging at twice the logical capacity so an old active
            # version and its replacement can coexist. Per-KB maxima prevent a
            # second version from counting twice against the logical workspace cap.
            inventory=s.execute(select(Segment.kb_id,Segment.chunk_count,Segment.dimensions).where(Segment.tenant_id==tenant,Segment.id!=p['segment_id'])).all()
            logical_chunks={};logical_cells={};physical_chunks=physical_cells=0
            for kb,count,dims in inventory:
                physical_chunks+=count;physical_cells+=count*dims
                logical_chunks[kb]=max(logical_chunks.get(kb,0),count)
                logical_cells[kb]=max(logical_cells.get(kb,0),count*dims)
            count=len(p['chunks']);cells=count*dimensions
            logical_chunks[p['kb_id']]=max(logical_chunks.get(p['kb_id'],0),count)
            logical_cells[p['kb_id']]=max(logical_cells.get(p['kb_id'],0),cells)
            if sum(logical_chunks.values())>50000 or physical_chunks+count>100000:raise ValueError('Workspace chunk capacity exceeded')
            if sum(logical_cells.values())>8_000_000 or physical_cells+cells>16_000_000:raise ValueError('Workspace vector memory budget exceeded')
            ids=[c['id'] for c in p['chunks']]
            # Bounded SQL IN lists work on SQLite builds with small variable limits.
            for start in range(0,len(ids),500):
                conflict=s.scalar(select(ChunkOwner.id).where(ChunkOwner.id.in_(ids[start:start+500]),ChunkOwner.segment_id!=p['segment_id']).limit(1))
                if conflict:raise ValueError('Chunk ID already belongs to another segment')
            if segment is None:
                segment=Segment(id=p['segment_id'],tenant_id=tenant,kb_id=p['kb_id'],version=p['version'],created_at=time.time())
                s.add(segment)
            segment.state='building';segment.build_hash=digest;segment.config=p['config']
            segment.connection=self.cipher.encrypt(json.dumps(p.get('connection',{})).encode())
            segment.dimensions=dimensions;segment.chunk_count=len(p['chunks'])
            existing=set(s.scalars(select(ChunkOwner.id).where(ChunkOwner.segment_id==segment.id)))
            owners=[{'id':id,'segment_id':segment.id} for id in ids if id not in existing]
            if owners:s.execute(ChunkOwner.__table__.insert(),owners)
            s.commit() # Must precede every adapter side effect.
            try:
                if dimensions:
                    await self._adapter(segment).upsert(segment.id,p['chunks'],p['vectors'])
                s.execute(delete(Posting).where(Posting.segment_id==segment.id))
                s.execute(delete(Chunk).where(Chunk.segment_id==segment.id))
                postings=[]
                for ordinal,c in enumerate(p['chunks']):
                    d=documents[c['document_id']];counts=Counter(t[:128] for t in tokens(c['text']))
                    s.add(Chunk(id=c['id'],segment_id=segment.id,document_id=d['id'],filename=d['filename'],content_hash=d['content_hash'],ordinal=ordinal,page=c['page'],text=c['text'],token_count=sum(counts.values())))
                    postings.extend({'segment_id':segment.id,'term':term,'chunk_id':c['id'],'frequency':frequency} for term,frequency in counts.items())
                    if len(postings)>=5000:
                        s.execute(Posting.__table__.insert(),postings);postings=[]
                if postings:s.execute(Posting.__table__.insert(),postings)
                segment.state='ready';s.commit()
            except BaseException as exc:
                s.rollback()
                segment=s.get(Segment,p['segment_id']);segment.state='failed';s.commit()
                if isinstance(exc,Exception):raise RuntimeError('Vector indexing failed; retry the build') from None
                raise
            return {'segment_id':segment.id,'chunk_count':segment.chunk_count}

    def _source(self,segment,c,score=0):
        return {'id':c.id,'knowledge_base_id':segment.kb_id,'version':segment.version,'document_id':c.document_id,'filename':c.filename,'page':c.page,'text':c.text,'score':score,'url':f'/api/knowledge-bases/{segment.kb_id}/documents/{c.document_id}/file'}

    async def _search(self,tenant,p):
        query=p.get('query')
        if not isinstance(query,str) or not query.strip() or len(query)>20000:raise ValueError('Query must contain 1–20,000 characters')
        o=RetrievalOptions.model_validate(p.get('options') or {}).model_dump()
        if o['candidate_k']<o['top_k']:raise ValueError('candidate_k must be at least top_k')
        allowed=p.get('document_ids')
        if not isinstance(allowed,list) or len(allowed)>20:raise ValueError('Authoritative document IDs required')
        for id in allowed:identifier(id)
        with Session(self.engine,expire_on_commit=False) as s:
            segment=self._segment(s,tenant,p['kb_id'],p['segment_id'],ready=True,version=p['version'])
            conditions=[Chunk.segment_id==segment.id,Chunk.document_id.in_(allowed)]
            for field,value in o['filter'].items():conditions.append(getattr(Chunk,field)==value)
            metadata=s.execute(select(Chunk.id,Chunk.ordinal,Chunk.token_count).where(*conditions)).all()
            if not metadata:
                with Session(self.engine) as fresh:self._segment(fresh,tenant,p['kb_id'],p['segment_id'],ready=True,version=p['version'])
                return []
            semantic=[];lexical=[]
            if o['mode']!='keyword' and not (o['mode']=='hybrid' and o['vector_weight']==0):
                vector=p.get('query_vector')
                if not segment.dimensions:raise ValueError('This index supports keyword retrieval only')
                if not isinstance(vector,list) or len(vector)!=segment.dimensions or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector) or not any(vector):raise ValueError('Query vector dimensions or values invalid')
                rows=[{'id':c.id,'ordinal':c.ordinal,'index_key':segment.id} for c in metadata]
                semantic=await self._adapter(segment).search(vector,rows,o['candidate_k'])
            if o['mode']!='similarity' and not (o['mode']=='hybrid' and o['vector_weight']==1):
                terms={t[:128] for t in tokens(query)}
                lengths={c.id:c.token_count for c in metadata};avg=sum(lengths.values())/len(lengths) or 1
                # Load postings for query terms only. Corpus text is never tokenized at query time.
                postings=s.execute(select(Posting.term,Posting.chunk_id,Posting.frequency).join(Chunk,Chunk.id==Posting.chunk_id).where(Posting.segment_id==segment.id,Posting.term.in_(terms),*conditions)).all()
                df=Counter(r.term for r in postings);scores=Counter()
                for term,id,frequency in postings:
                    scores[id]+=math.log(1+(len(lengths)-df[term]+.5)/(df[term]+.5))*frequency*2.5/(frequency+1.5*(.25+.75*lengths[id]/avg))
                lexical=sorted(scores.items(),key=lambda x:(-x[1],x[0]))[:o['candidate_k']]
            if o['mode']=='similarity':ranked=semantic
            elif o['mode']=='keyword':ranked=lexical
            else:
                scores=Counter()
                for weight,ranking in ((o['vector_weight'],semantic),(1-o['vector_weight'],lexical)):
                    if (o['mode']=='hybrid' and weight==0) or not ranking:continue
                    # Cosine and BM25 live on different scales (cosine clusters near 0.6–0.9, BM25 spreads
                    # 0–max), so weighting raw values lets one signal dominate whatever vector_weight says.
                    # Min–max over each candidate list puts both on 0–1 before the convex combination.
                    values=[score for _,score in ranking];low=min(values);spread=max(values)-low
                    for rank,(id,score) in enumerate(ranking,1):
                        scores[id]+=1/(o['rrf_k']+rank) if o['mode']=='rrf' else weight*((score-low)/spread if spread>0 else 1.)
                # Chunk IDs are random per build, so ties must resolve by rank, not by ID, to keep results repeatable.
                order={id:rank for rank,(id,_) in enumerate(semantic or lexical)}
                ranked=sorted(scores.items(),key=lambda x:(-x[1],order.get(x[0],len(order)),x[0]))
            eligible={c.id for c in metadata};result=[];seen=set()
            for id,score in ranked:
                if id in seen or id not in eligible or not math.isfinite(score) or (o['score_threshold'] is not None and score<o['score_threshold']):continue
                chunk=s.get(Chunk,id)
                if chunk is None:raise KeyError('Source was retired during retrieval')
                seen.add(id);result.append(self._source(segment,chunk,float(score)))
                if len(result)>=o['top_k']:break
            # A remote query can overlap retirement; never return a now-retired
            # segment as a current search result. Management also checks authority.
            with Session(self.engine) as fresh:
                self._segment(fresh,tenant,p['kb_id'],p['segment_id'],ready=True,version=p['version'])
            return result

    async def _verify(self,tenant,p):
        sources=p.get('sources')
        if not isinstance(sources,list) or len(sources)>20:raise ValueError('Invalid sources')
        result=[];seen=set()
        with Session(self.engine) as s:
            for value in sources:
                if not isinstance(value,dict):raise ValueError('Invalid source')
                id=identifier(value.get('id'))
                if id in seen:raise ValueError('Duplicate source')
                seen.add(id);c=s.get(Chunk,id)
                if c is None:raise KeyError('Source unavailable')
                segment=self._segment(s,tenant,value.get('knowledge_base_id'),c.segment_id,version=value.get('version'))
                if segment.state!='ready' and not segment.retain_provenance:raise KeyError('Source unavailable')
                score=value.get('score')
                if type(score) not in (int,float) or not math.isfinite(score):raise ValueError('Invalid source score')
                canonical=self._source(segment,c,score)
                if value!=canonical:raise ValueError('Source metadata does not match stored evidence')
                result.append(canonical)
        return result

    async def _preview(self,tenant,p):
        identifier(p.get('document_id'));limit=p.get('limit',10)
        if type(limit) is not int or not 1<=limit<=20:raise ValueError('Invalid preview limit')
        with Session(self.engine) as s:
            segment=self._segment(s,tenant,p['kb_id'],p['segment_id'],ready=True)
            conditions=[Chunk.segment_id==segment.id,Chunk.document_id==p['document_id']]
            total=s.scalar(select(func.count()).select_from(Chunk).where(*conditions))
            rows=s.scalars(select(Chunk).where(*conditions).order_by(Chunk.ordinal).limit(limit)).all()
            result={'sources':[self._source(segment,c) for c in rows],'total':total}
            with Session(self.engine) as fresh:self._segment(fresh,tenant,p['kb_id'],p['segment_id'],ready=True)
            return result

    async def _cleanup(self,tenant,p):
        identifier(p['kb_id']);identifier(p['segment_id'])
        with Session(self.engine,expire_on_commit=False) as s:
            segment=s.get(Segment,p['segment_id'])
            if segment is None:
                segment=Segment(id=p['segment_id'],tenant_id=tenant,kb_id=p['kb_id'],version=0,state='tombstone',created_at=time.time())
                s.add(segment)
            else:self._segment(s,tenant,p['kb_id'],p['segment_id'])
            retain=p.get('retain_provenance',False)
            if type(retain) is not bool:raise ValueError('Invalid provenance retention flag')
            if not retain:segment.provenance_revoked=True
            segment.retain_provenance=bool(retain and not segment.provenance_revoked and (segment.state=='ready' or segment.retain_provenance))
            segment.state='tombstone';segment.cleanup_attempts=(segment.cleanup_attempts or 0)+1
            s.commit() # Tombstone before potentially failing remote deletion.
            try:
                if segment.config and segment.dimensions:await self._adapter(segment).remove(segment.id)
                s.execute(delete(Posting).where(Posting.segment_id==segment.id))
                if not segment.retain_provenance:s.execute(delete(Chunk).where(Chunk.segment_id==segment.id))
                segment.chunk_count=0;segment.cleanup_error='';s.commit()
            except BaseException as exc:
                s.rollback();segment=s.get(Segment,p['segment_id']);segment.cleanup_error='Vector cleanup failed; retry pending';s.commit()
                if isinstance(exc,Exception):raise RuntimeError('Vector cleanup failed; retry pending') from None
                raise
        return {'ok':True}

    async def _cleanup_pending(self,tenant,p):
        with Session(self.engine) as s:
            rows=s.execute(select(Segment.kb_id,Segment.id,Segment.retain_provenance).where(Segment.tenant_id==tenant,Segment.state.in_(['tombstone','failed','building']))).all()
        completed=failed=0
        for kb_id,id,retain in rows:
            try:await self._cleanup(tenant,{'kb_id':kb_id,'segment_id':id,'retain_provenance':retain});completed+=1
            except Exception:failed+=1
        return {'attempted':len(rows),'completed':completed,'failed':failed}

    async def _stats(self,tenant,p):
        identifier(p['kb_id'])
        with Session(self.engine) as s:
            rows=s.scalars(select(Segment).where(Segment.tenant_id==tenant,Segment.kb_id==p['kb_id'])).all()
            return {'segments':[{'segment_id':r.id,'version':r.version,'status':r.state,'chunk_count':r.chunk_count,'dimensions':r.dimensions,'cleanup_attempts':r.cleanup_attempts,'cleanup_error':r.cleanup_error} for r in rows], 'chunk_count':sum(r.chunk_count for r in rows if r.state=='ready')}
