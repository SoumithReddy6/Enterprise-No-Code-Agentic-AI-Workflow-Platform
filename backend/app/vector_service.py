"""Tenant-owned vector resources with fenced ingestion and canonical evidence."""
import asyncio
from collections import Counter
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time
from sqlalchemy import JSON, Column, String, Text, LargeBinary, Integer, Float, select, delete, func, text
from sqlalchemy.orm import Session, deferred
from .storage import Base, new_id

class VectorResource(Base):
    __tablename__='vector_resources'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    name=Column(String(120),nullable=False)
    config=Column(JSON,nullable=False)
    dimensions=Column(Integer,nullable=False,default=0)

class VectorFile(Base):
    __tablename__='vector_files'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    base_id=Column(String(64),nullable=False,index=True)
    filename=Column(String(240),nullable=False)
    content=deferred(Column(LargeBinary,nullable=False))
    status=Column(String(24),nullable=False,index=True)
    error=Column(Text,nullable=False,default='')
    index_key=Column(String(64),nullable=False,default="")
    pages=Column(Integer,nullable=False,default=0)
    owner=Column(String(64),nullable=False,default='')
    lease_until=Column(Float,nullable=False,default=0)
    created=Column(Float,nullable=False,default=time.time)

class VectorChunk(Base):
    __tablename__='vector_chunks'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    base_id=Column(String(64),nullable=False,index=True)
    document_id=Column(String(64),nullable=False,index=True)
    ordinal=Column(Integer,nullable=False)
    page=Column(Integer,nullable=False)
    text=Column(Text,nullable=False)

class VectorSegment(Base):
    """Durable attempt inventory; retired keys remain tombstones for late writers."""
    __tablename__='vector_segments'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    resource_id=Column(String(64),nullable=False,index=True)
    document_id=Column(String(64),nullable=False,index=True)
    next_cleanup=Column(Float,nullable=False,default=0)

TENANT_MODELS=(VectorResource,VectorFile,VectorChunk,VectorSegment)
def tokens(value):return re.findall(r"\w+",value.casefold())
def metadata(d):return {k:getattr(d,k) for k in ('id','filename','status','error','pages')}

class VectorService:
    max_bytes=25*1024*1024
    max_pages=200
    max_documents=20
    max_chunks=50000
    lease_seconds=30
    extraction_timeout=300

    def __init__(self,store):self.store=store

    @contextmanager
    def transaction(self):
        # All knowledge mutations serialize across workers, including quota checks.
        with Session(self.store.engine) as s:
            if self.store.engine.dialect.name=='sqlite':s.execute(text('BEGIN IMMEDIATE'))
            elif self.store.engine.dialect.name=='postgresql':s.execute(text('SELECT pg_advisory_xact_lock(19482027)'))
            try:
                yield s
                s.commit()
            except BaseException:
                s.rollback();raise

    def _base(self,s,id,tenant):
        row=s.get(VectorResource,id)
        if not row or row.tenant_id!=tenant:raise KeyError('Knowledge base unavailable')
        return row

    def _document(self,s,id,tenant):
        row=s.get(VectorFile,id)
        if not row or row.tenant_id!=tenant or row.status=='removed':raise KeyError('VectorFile unavailable')
        self._base(s,row.base_id,tenant)
        return row

    def download(self,document_id,tenant_id='local'):
        with Session(self.store.engine) as s:
            d=self._document(s,document_id,tenant_id);return d.filename,d.content

    def remove(self,document_id,tenant_id='local'):
        with self.transaction() as s:
            d=self._document(s,document_id,tenant_id);d.status='removed';d.content=b'';d.owner='';d.lease_until=0
            s.execute(delete(VectorChunk).where(VectorChunk.document_id==d.id))
            return metadata(d)

    def retry(self,document_id,tenant_id='local'):
        with self.transaction() as s:
            d=self._document(s,document_id,tenant_id)
            if d.status!='failed':raise ValueError('Only failed documents can be retried')
            d.status='queued';d.error='';d.owner='';d.lease_until=0;return metadata(d)

    def claim(self):
        with self.transaction() as s:
            now=time.time()
            # Global capacity one, also when multiple API processes run the loop.
            if s.scalar(select(VectorFile.id).where(VectorFile.status=='processing',VectorFile.lease_until>now).limit(1)):return None
            d=s.scalar(select(VectorFile).where((VectorFile.status=='queued')|((VectorFile.status=='processing')&(VectorFile.lease_until<=now))).order_by(VectorFile.created).limit(1))
            if not d:return None
            self._base(s,d.base_id,d.tenant_id)
            self._track_segment(s,d,d.index_key)
            d.status='processing';d.owner=new_id();d.lease_until=now+self.lease_seconds
            return d.id,d.owner

    def _owned(self,s,id,owner):
        d=s.get(VectorFile,id)
        return d if d and d.status=='processing' and d.owner==owner and d.lease_until>time.time() else None

    def renew(self,id,owner):
        with self.transaction() as s:
            d=self._owned(s,id,owner)
            if not d:return False
            d.lease_until=time.time()+self.lease_seconds;return True

    def _info(self,r):return {'id':r.id,'name':r.name,**r.config,'dimensions':r.dimensions}

    def create(self,config,tenant='local'):
        from .vector_adapters import CAPABILITIES
        from .vector_api import ResourceCreate
        config=ResourceCreate.model_validate(config).model_dump()
        backend=config['backend'];caps=CAPABILITIES[backend]
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}',config['storage_path']):raise ValueError('Storage path must be a logical name using letters, numbers, dash or underscore')
        if config['chunk_overlap']>=config['chunk_size']:raise ValueError('Overlap must be smaller than chunk size')
        config['index_method']=config['index_method'] or caps['index_methods'][0]
        if config['index_method'] not in caps['index_methods']:raise ValueError('Index method is unsupported by this backend')
        if not caps['local']:
            if not config['connection_id']:raise ValueError('Remote backend requires a connection')
            self.connection(config,tenant)
            if backend=='elasticsearch' and not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,100}',config['index_name']):raise ValueError('Provide a valid existing Elasticsearch index name')
        with self.transaction() as s:
            keys=('backend','connection_id','index_name','storage_path')
            for existing in s.scalars(select(VectorResource).where(VectorResource.tenant_id==tenant)):
                if all(existing.config.get(k,'')==config[k] for k in keys):raise ValueError('A resource already exists at this storage path for this backend and connection')
            r=VectorResource(id=new_id(),tenant_id=tenant,name=config.pop('name'),config=config,dimensions=0);s.add(r)
            return self._info(r)

    def resource(self,id,tenant='local'):
        with Session(self.store.engine) as s:return self._info(self._base(s,id,tenant))
    def resources(self,tenant='local'):
        with Session(self.store.engine) as s:return [self._info(r) for r in s.scalars(select(VectorResource).where(VectorResource.tenant_id==tenant))]
    def files(self,id,tenant='local'):
        with Session(self.store.engine) as s:
            self._base(s,id,tenant)
            return [metadata(d) for d in s.scalars(select(VectorFile).where(VectorFile.base_id==id,VectorFile.tenant_id==tenant,VectorFile.status!='removed').order_by(VectorFile.created))]
    def upload(self,id,filename,content,tenant='local'):
        from .vector_extract import EXTENSIONS
        filename=re.sub(r'[\x00-\x1f\x7f/\\]','_',filename or 'document.txt')[:240]
        if Path(filename).suffix.lower() not in EXTENSIONS:raise ValueError('Unsupported format. Upload PDF, text, Markdown, CSV, JSON, HTML or DOCX')
        if not content or len(content)>self.max_bytes:raise ValueError('File must contain 1 byte to 25 MB')
        with self.transaction() as s:
            self._base(s,id,tenant)
            count=s.scalar(select(func.count()).select_from(VectorFile).where(VectorFile.base_id==id,VectorFile.status!='removed'))
            if count>=self.max_documents:raise ValueError('Resource limit: 20 active files')
            d=VectorFile(id=new_id(),tenant_id=tenant,base_id=id,filename=filename,content=content,status='queued',error='',pages=0);s.add(d)
            return metadata(d)
    def connection(self,r,tenant):
        from .tool_service import ToolService
        c=ToolService(self.store).resolve(r['connection_id'],tenant)
        if c['provider']!=r['backend']:raise ValueError('Connection type does not match vector backend')
        return c
    def adapter(self,r,tenant):
        from .vector_adapters import ADAPTERS
        root=Path(os.environ.get('VECTOR_DATA_DIR',str(Path(__file__).resolve().parents[2]/'.data'/'vectors')))
        path=root/r['id']/r['storage_path']
        return ADAPTERS[r['backend']](path,r,self.connection(r,tenant) if r['backend'] in ('elasticsearch','pinecone') else None)
    async def embed(self,model,texts):
        import httpx
        from .providers import ollama_url
        try:
            async with httpx.AsyncClient(timeout=120,trust_env=False) as client:
                tags=await client.get(ollama_url()+'/api/tags');tags.raise_for_status()
                if model not in {m['name'] for m in tags.json().get('models',[])}:raise ValueError('Embedding model is not installed in Ollama; select an installed embedding model')
                response=await client.post(ollama_url()+'/api/embed',json={'model':model,'input':texts,'truncate':False});response.raise_for_status()
                vectors=response.json().get('embeddings')
        except httpx.HTTPError:raise ValueError('Ollama embedding failed; check the installed model supports embeddings and chunk size') from None
        if not isinstance(vectors,list) or len(vectors)!=len(texts):raise ValueError('Embedding response has wrong number of vectors')
        if any(not isinstance(v,list) for v in vectors):raise ValueError('Invalid embedding vectors')
        dims=len(vectors[0]) if vectors else 0
        if not 1<=dims<=65536 or any(len(v)!=dims or any(not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(x) for x in v) or not any(v) for v in vectors):raise ValueError('Invalid embedding vectors')
        return vectors
    def split(self,pages,r):
        chunks=[]
        if not isinstance(pages,list) or not 1<=len(pages)<=200:raise ValueError('Document must have 1–200 pages')
        for page,value in enumerate(pages,1):
            if not isinstance(value,str):raise ValueError('Invalid extracted text')
            sections=re.split(r'\n\s*\n',value) if r['chunking']=='paragraph' else [value]
            for section in sections:
                for start in range(0,len(section),r['chunk_size']-r['chunk_overlap']):
                    passage=section[start:start+r['chunk_size']].strip()
                    if passage:chunks.append({'id':new_id(),'ordinal':len(chunks),'page':page,'text':passage})
                    if len(chunks)>50000:raise ValueError('Document exceeds 50,000 chunks')
        if not chunks:raise ValueError('Document has no readable text')
        return chunks
    async def _index(self,id,owner):
        with Session(self.store.engine) as s:
            d=self._owned(s,id,owner)
            if not d:return
            content=d.content;filename=d.filename;tenant=d.tenant_id;resource_id=d.base_id
        r=self.resource(resource_id,tenant)
        with tempfile.TemporaryDirectory(prefix='relay-vector-') as temp:
            source=Path(temp)/('input'+Path(filename).suffix.lower());output=Path(temp)/'output.json';source.write_bytes(content)
            # Run as module with explicit backend package root for all launch directories.
            env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
            process=await asyncio.create_subprocess_exec(sys.executable,'-m','app.vector_extract',str(source),str(output),env=env,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
            try:await asyncio.wait_for(process.wait(),timeout=self.extraction_timeout)
            finally:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()
            if not output.exists() or output.stat().st_size>64*1024*1024:raise ValueError('Extraction failed or exceeded text limits')
            result=json.loads(output.read_text())
            if result.get('error'):raise ValueError(result['error'])
        chunks=self.split(result['pages'],r);vectors=[]
        for start in range(0,len(chunks),32):
            batch=await self.embed(r['embedding_model'],[c['text'] for c in chunks[start:start+32]])
            if len(chunks)*len(batch[0])>8_000_000:raise ValueError('Document exceeds vector memory budget; use larger chunks or a smaller embedding model')
            vectors.extend(batch)
        dims=len(vectors[0])
        if any(len(v)!=dims for v in vectors):raise ValueError('Embedding dimensions changed during indexing')
        with self.transaction() as s:
            d=self._owned(s,id,owner)
            if not d:return
            resource=self._base(s,resource_id,tenant)
            if resource.dimensions and resource.dimensions!=dims:raise ValueError('Embedding dimensions changed; create a new resource')
            self._track_segment(s,d,d.index_key)
            self._track_segment(s,d,owner)
            resource.dimensions=dims;d.index_key=owner
        await self.adapter(r,tenant).upsert(owner,chunks,vectors)
        with self.transaction() as s:
            d=self._owned(s,id,owner)
            if not d:return
            count=s.scalar(select(func.count()).select_from(VectorChunk).where(VectorChunk.tenant_id==tenant))
            if count+len(chunks)>50000:raise ValueError('Workspace limit: 50,000 indexed chunks')
            s.execute(delete(VectorChunk).where(VectorChunk.document_id==id))
            s.add_all([VectorChunk(**c,tenant_id=tenant,base_id=resource_id,document_id=id) for c in chunks])
            d.status='ready';d.index_key=owner;d.owner='';d.lease_until=0;d.error='';d.pages=len(result['pages'])
    def fail(self,id,owner,error):
        with self.transaction() as s:
            d=self._owned(s,id,owner)
            if d:d.status='failed';d.error=str(error)[:500];d.owner='';d.lease_until=0
    def _track_segment(self,s,d,key):
        if key and not s.get(VectorSegment,key):
            s.add(VectorSegment(id=key,tenant_id=d.tenant_id,resource_id=d.base_id,document_id=d.id,next_cleanup=0))
    async def cleanup(self):
        with self.transaction() as s:
            # Backfill pre-inventory files before any attempt key can be replaced.
            for d in s.scalars(select(VectorFile).where(VectorFile.index_key!='')):self._track_segment(s,d,d.index_key)
            pending=[];now=time.time()
            for segment in s.scalars(select(VectorSegment).where(VectorSegment.next_cleanup<=now)):
                d=s.get(VectorFile,segment.document_id)
                published=d and d.status=='ready' and d.index_key==segment.id
                active=d and d.status=='processing' and d.owner==segment.id and d.lease_until>now
                if published or active:continue
                pending.append((segment.id,segment.resource_id,segment.tenant_id))
                segment.next_cleanup=now+300
        for key,resource,tenant in pending:
            try:await self.adapter(self.resource(resource,tenant),tenant).remove(key)
            except Exception:pass  # Durable tombstones retry even if an old writer finishes later.
    async def process_once(self):
        await self.cleanup()
        claim=self.claim()
        if not claim:return False
        id,owner=claim
        task=asyncio.create_task(self._index(id,owner))
        try:
            while not task.done():
                done,_=await asyncio.wait([task],timeout=5)
                if not done and not self.renew(id,owner):task.cancel();return True
            await task
        except asyncio.CancelledError:task.cancel();raise
        except Exception as exc:self.fail(id,owner,exc)
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        return True
    async def serve(self):
        while True:
            try:
                if not await self.process_once():await asyncio.sleep(1)
            except asyncio.CancelledError:raise
            except Exception:await asyncio.sleep(2)
    def _source(self,c,d,score):return {'id':c.id,'resource_id':c.base_id,'document_id':d.id,'filename':d.filename,'page':c.page,'text':c.text,'score':score,'url':f'/api/vector-files/{d.id}/file'}
    async def retrieve(self,resource_id,query,options=None,tenant='local'):
        from .vector_api import RetrievalOptions
        o=RetrievalOptions.model_validate(options or {}).model_dump()
        if o['candidate_k']<o['top_k']:raise ValueError('candidate_k must be at least top_k')
        if not isinstance(query,str) or not query.strip() or len(query)>20000:raise ValueError('Query must contain 1–20,000 characters')
        r=self.resource(resource_id,tenant)
        with Session(self.store.engine) as s:
            pairs=s.execute(select(VectorChunk,VectorFile).join(VectorFile,VectorFile.id==VectorChunk.document_id).where(VectorChunk.base_id==resource_id,VectorChunk.tenant_id==tenant,VectorFile.tenant_id==tenant,VectorFile.status=='ready')).all()
            pairs=[(c,d) for c,d in pairs if all(getattr(d,'id' if k=='document_id' else k)==v for k,v in o['filter'].items())]
            rows=[{'id':c.id,'ordinal':c.ordinal,'text':c.text,'index_key':d.index_key} for c,d in pairs]
            canonical={c.id:self._source(c,d,0) for c,d in pairs}
        if not rows:return []
        semantic=[];lexical=[]
        if o['mode']!='keyword' and not (o['mode']=='hybrid' and o['vector_weight']==0):
            vector=(await self.embed(r['embedding_model'],[query]))[0]
            if len(vector)!=r['dimensions']:raise ValueError('Embedding dimensions changed; create a new resource')
            semantic=await self.adapter(r,tenant).search(vector,rows,o['candidate_k'])
        if o['mode']!='similarity' and not (o['mode']=='hybrid' and o['vector_weight']==1):
            terms=set(tokens(query));counts=[Counter(tokens(row['text'])) for row in rows];avg=sum(sum(c.values()) for c in counts)/len(counts) or 1
            df={t:sum(t in c for c in counts) for t in terms}
            for row,freq in zip(rows,counts):
                score=sum(math.log(1+(len(rows)-df[t]+.5)/(df[t]+.5))*freq[t]*2.5/(freq[t]+1.5*(.25+.75*sum(freq.values())/avg)) for t in terms if freq[t])
                if score>0:lexical.append((row['id'],score))
            lexical=sorted(lexical,key=lambda x:-x[1])[:o['candidate_k']]
        if o['mode']=='similarity':ranked=semantic
        elif o['mode']=='keyword':ranked=lexical
        else:
            scores={}
            for is_vector,weight,ranking in ((True,o['vector_weight'],semantic),(False,1-o['vector_weight'],lexical)):
                if o['mode']=='hybrid' and weight==0:continue
                maximum=max((score for _,score in ranking),default=1) or 1
                for rank,(id,score) in enumerate(ranking,1):
                    if o['mode']=='rrf':contribution=1/(o['rrf_k']+rank)
                    else:
                        normalized=min(1,max(0,(score+1)/2)) if is_vector else max(0,score)/maximum
                        contribution=weight*normalized
                    if contribution>0:scores[id]=scores.get(id,0)+contribution
            ranked=sorted(scores.items(),key=lambda x:-x[1])
        result=[]
        for id,score in ranked:
            if id in canonical and math.isfinite(score) and (o['score_threshold'] is None or score>=o['score_threshold']):result.append(dict(canonical[id],score=score))
            if len(result)>=o['top_k']:break
        return self.verify_sources(result,tenant)
    def verify_sources(self,sources,tenant='local'):
        if not isinstance(sources,list) or len(sources)>20:raise ValueError('Invalid sources')
        canonical=[];seen=set()
        with Session(self.store.engine) as s:
            for item in sources:
                if not isinstance(item,dict) or not isinstance(item.get('id'),str) or item['id'] in seen:raise ValueError('Invalid source metadata')
                seen.add(item['id']);c=s.get(VectorChunk,item['id'])
                if not c or c.tenant_id!=tenant:raise ValueError('Source is unavailable or removed')
                d=self._document(s,c.document_id,tenant)
                if d.status!='ready':raise ValueError('Source is not ready')
                score=item.get('score',0)
                if isinstance(score,bool) or not isinstance(score,(float,int)) or not math.isfinite(score):raise ValueError('Invalid source score')
                value=self._source(c,d,score)
                if any(item.get(k)!=v for k,v in value.items()):raise ValueError('Source metadata does not match stored evidence')
                canonical.append(value)
        return canonical
    async def capabilities(self):
        from .vector_adapters import CAPABILITIES
        from .providers import ollama_models
        try:models=(await ollama_models())['models']
        except Exception:models=[]
        return {'backends':CAPABILITIES,'embedding_models':models,'extensions':['pdf','txt','md','markdown','csv','json','html','htm','docx'],'keyword_engine':'local BM25','max_file_bytes':self.max_bytes}
