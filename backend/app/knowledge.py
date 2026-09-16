"""Workspace-scoped PDF storage, fenced indexing and local lexical retrieval."""
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
from sqlalchemy import Column, String, Text, LargeBinary, Integer, Float, select, delete, func, text
from sqlalchemy.orm import Session, deferred
from .storage import Base, new_id

class KnowledgeBase(Base):
    __tablename__='knowledge_bases'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    name=Column(String(120),nullable=False)

class Document(Base):
    __tablename__='knowledge_documents'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    base_id=Column(String(64),nullable=False,index=True)
    filename=Column(String(240),nullable=False)
    content=deferred(Column(LargeBinary,nullable=False))
    status=Column(String(24),nullable=False,index=True)
    error=Column(Text,nullable=False,default='')
    pages=Column(Integer,nullable=False,default=0)
    owner=Column(String(64),nullable=False,default='')
    lease_until=Column(Float,nullable=False,default=0)
    created=Column(Float,nullable=False,default=time.time)

class Chunk(Base):
    __tablename__='knowledge_chunks'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    base_id=Column(String(64),nullable=False,index=True)
    document_id=Column(String(64),nullable=False,index=True)
    page=Column(Integer,nullable=False)
    text=Column(Text,nullable=False)

TENANT_MODELS=(KnowledgeBase,Document,Chunk)
def tokens(value):return re.findall(r"\w+",value.casefold())
def metadata(d):return {k:getattr(d,k) for k in ('id','filename','status','error','pages')}

class Knowledge:
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
        row=s.get(KnowledgeBase,id)
        if not row or row.tenant_id!=tenant:raise KeyError('Knowledge base unavailable')
        return row

    def _document(self,s,id,tenant):
        row=s.get(Document,id)
        if not row or row.tenant_id!=tenant or row.status=='removed':raise KeyError('Document unavailable')
        self._base(s,row.base_id,tenant)
        return row

    def create(self,name,tenant_id='local'):
        if not isinstance(name,str) or not name.strip() or len(name.strip())>120:raise ValueError('Name must contain 1–120 characters')
        with self.transaction() as s:
            row=KnowledgeBase(id=new_id(),tenant_id=tenant_id,name=name.strip());s.add(row)
            return {'id':row.id,'name':row.name}

    def bases(self,tenant_id='local'):
        with Session(self.store.engine) as s:return [{'id':r.id,'name':r.name} for r in s.scalars(select(KnowledgeBase).where(KnowledgeBase.tenant_id==tenant_id).order_by(KnowledgeBase.name))]

    def check_base(self,base_id,tenant_id='local'):
        with Session(self.store.engine) as s:
            row=self._base(s,base_id,tenant_id);return {'id':row.id,'name':row.name}

    def documents(self,base_id,tenant_id='local'):
        with Session(self.store.engine) as s:
            self._base(s,base_id,tenant_id)
            return [metadata(d) for d in s.scalars(select(Document).where(Document.base_id==base_id,Document.tenant_id==tenant_id,Document.status!='removed').order_by(Document.created))]

    def upload(self,base_id,filename,content,tenant_id='local'):
        if not content or len(content)>self.max_bytes:raise ValueError('PDF must be at most 25 MB')
        if b'%PDF-' not in content[:1024]:raise ValueError('File is not a PDF')
        filename=re.sub(r'[\x00-\x1f\x7f/\\]','_',filename or 'document.pdf')[:240]
        with self.transaction() as s:
            self._base(s,base_id,tenant_id)
            count=s.scalar(select(func.count()).select_from(Document).where(Document.base_id==base_id,Document.status!='removed'))
            if count>=self.max_documents:raise ValueError('Knowledge base limit: 20 active PDFs')
            d=Document(id=new_id(),tenant_id=tenant_id,base_id=base_id,filename=filename,content=content,status='queued',error='',pages=0)
            s.add(d);return metadata(d)

    def download(self,document_id,tenant_id='local'):
        with Session(self.store.engine) as s:
            d=self._document(s,document_id,tenant_id);return d.filename,d.content

    def remove(self,document_id,tenant_id='local'):
        with self.transaction() as s:
            d=self._document(s,document_id,tenant_id);d.status='removed';d.content=b'';d.owner='';d.lease_until=0
            s.execute(delete(Chunk).where(Chunk.document_id==d.id))
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
            if s.scalar(select(Document.id).where(Document.status=='processing',Document.lease_until>now).limit(1)):return None
            d=s.scalar(select(Document).where((Document.status=='queued')|((Document.status=='processing')&(Document.lease_until<=now))).order_by(Document.created).limit(1))
            if not d:return None
            self._base(s,d.base_id,d.tenant_id)
            d.status='processing';d.owner=new_id();d.lease_until=now+self.lease_seconds
            return d.id,d.owner

    def _owned(self,s,id,owner):
        d=s.get(Document,id)
        return d if d and d.status=='processing' and d.owner==owner and d.lease_until>time.time() else None

    def renew(self,id,owner):
        with self.transaction() as s:
            d=self._owned(s,id,owner)
            if not d:return False
            d.lease_until=time.time()+self.lease_seconds;return True

    def publish(self,id,owner,pages):
        if not isinstance(pages,list) or not 0<len(pages)<=self.max_pages:raise ValueError('PDF must contain 1–200 pages')
        chunks=[]
        for page,value in enumerate(pages,1):
            if not isinstance(value,str):raise ValueError(f'Invalid text on page {page}')
            if not value.strip():continue
            for start in range(0,len(value),1000):
                chunks.append((page,value[start:start+1200]))
                if len(chunks)>self.max_chunks:raise ValueError('Workspace chunk limit exceeded')
        if not chunks:raise ValueError('PDF has no readable text')
        with self.transaction() as s:
            d=self._owned(s,id,owner)
            if not d:return False
            self._base(s,d.base_id,d.tenant_id)
            count=s.scalar(select(func.count()).select_from(Chunk).where(Chunk.tenant_id==d.tenant_id))
            if count+len(chunks)>self.max_chunks:raise ValueError('Workspace limit: 50,000 indexed chunks')
            s.add_all([Chunk(id=new_id(),tenant_id=d.tenant_id,base_id=d.base_id,document_id=d.id,page=p,text=t) for p,t in chunks])
            d.status='ready';d.pages=len(pages);d.error='';d.owner='';d.lease_until=0
            return True

    def fail(self,id,owner,error):
        with self.transaction() as s:
            d=self._owned(s,id,owner)
            if d:d.status='failed';d.error=str(error)[:500];d.owner='';d.lease_until=0

    async def process_once(self):
        claim=self.claim()
        if not claim:return False
        id,owner=claim;process=None
        try:
            with Session(self.store.engine) as s:
                d=self._owned(s,id,owner)
                if not d:return True
                content=d.content
            with tempfile.TemporaryDirectory(prefix='relay-pdf-') as temp:
                source=Path(temp)/'input.pdf';output=Path(temp)/'output.json';source.write_bytes(content)
                process=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).with_name('pdf_extract.py')),str(source),str(output),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
                started=time.monotonic()
                while process.returncode is None:
                    try:await asyncio.wait_for(asyncio.shield(process.wait()),timeout=min(5,self.lease_seconds/3))
                    except asyncio.TimeoutError:pass
                    if time.monotonic()-started>self.extraction_timeout:raise ValueError('PDF extraction timed out; try a smaller document')
                    if not self.renew(id,owner):raise ValueError('Indexing lease expired or document was removed')
                if not output.exists():raise ValueError('PDF extraction failed; check the PDF and local OCR installation')
                if output.stat().st_size>64*1024*1024:raise ValueError('Extracted PDF text is too large')
                result=json.loads(output.read_text())
                if result.get('error'):raise ValueError(result['error'])
                self.publish(id,owner,result['pages'])
        except asyncio.CancelledError:
            # Persisted lease makes interrupted work recoverable.
            raise
        except Exception as exc:self.fail(id,owner,exc)
        finally:
            if process:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()
        return True

    async def serve(self):
        while True:
            try:
                if not await self.process_once():await asyncio.sleep(1)
            except asyncio.CancelledError:raise
            except Exception:await asyncio.sleep(2)

    def retrieve(self,base_id,query,limit=4,tenant_id='local'):
        if not isinstance(query,str) or len(query)>20000:raise ValueError('Query must be text up to 20,000 characters')
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=8:raise ValueError('Passage count must be 1–8')
        with Session(self.store.engine) as s:
            self._base(s,base_id,tenant_id)
            rows=s.execute(select(Chunk,Document).join(Document,Document.id==Chunk.document_id).where(Chunk.base_id==base_id,Chunk.tenant_id==tenant_id,Document.tenant_id==tenant_id,Document.status=='ready')).all()
            if not rows:return []
            terms=set(tokens(query));counts=[Counter(tokens(c.text)) for c,d in rows];avg=sum(sum(c.values()) for c in counts)/len(counts) or 1
            df={t:sum(t in c for c in counts) for t in terms};ranked=[]
            for (chunk,doc),freq in zip(rows,counts):
                score=sum(math.log(1+(len(rows)-df[t]+.5)/(df[t]+.5))*freq[t]*2.5/(freq[t]+1.5*(.25+.75*sum(freq.values())/avg)) for t in terms if freq[t])
                if score>0:ranked.append(self._source(chunk,doc,score))
            return sorted(ranked,key=lambda r:(-r['score'],r['id']))[:limit]

    def _source(self,c,d,score):return {'id':c.id,'document_id':d.id,'filename':d.filename,'page':c.page,'text':c.text,'score':score}

    def verify_sources(self,sources,tenant_id='local'):
        if not isinstance(sources,list) or len(sources)>8:raise ValueError('Invalid sources')
        canonical=[];seen=set()
        with Session(self.store.engine) as s:
            for item in sources:
                if not isinstance(item,dict) or not isinstance(item.get('id'),str) or item['id'] in seen:raise ValueError('Invalid source metadata')
                seen.add(item['id']);chunk=s.get(Chunk,item['id'])
                if not chunk or chunk.tenant_id!=tenant_id:raise ValueError('Source is unavailable or removed')
                doc=self._document(s,chunk.document_id,tenant_id)
                if doc.status!='ready':raise ValueError('Source is not ready')
                score=item.get('score',0)
                if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or score<0:raise ValueError('Invalid retrieval score')
                value=self._source(chunk,doc,score)
                if any(item.get(k)!=value[k] for k in ('id','document_id','filename','page','text')):raise ValueError('Source metadata does not match stored evidence')
                canonical.append(value)
        return canonical
