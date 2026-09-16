"""User-authorized gateway to knowledge service APIs; no service-owned SQL access."""
import base64
import json
import time
from collections import defaultdict
from urllib.parse import quote
from fastapi import Depends,HTTPException,Request
from fastapi.responses import Response
from pydantic import Field
from .models import StrictModel
from .kb.rpc import Client,ServiceUnavailable
from .kb.embedding import Embeddings
from .kb.options import RetrievalOptions,KBSettings
from .providers import supports
from .vector_adapters import CAPABILITIES
from .tool_service import ToolService

class KBCreate(StrictModel):
    name:str=Field(min_length=1,max_length=120)
    description:str=Field(default='',max_length=2000)
    config:dict=Field(default_factory=dict)
class KBUpdate(StrictModel):
    name:str|None=Field(default=None,min_length=1,max_length=120)
    description:str|None=Field(default=None,max_length=2000)
class KBRebuild(StrictModel):config:dict
class KBSearch(StrictModel):
    query:str=Field(min_length=1,max_length=20000)
    options:RetrievalOptions|None=None

class KnowledgeServices:
    def __init__(self,management=None,search=None,embeddings=None):
        self.management=management or Client('management');self.index=search or Client('search');self.embeddings=embeddings or Embeddings(4)
    async def retrieve(self,kb_id,query,options,tenant):
        resolved=await self.management.call('resolve',tenant,{'kb_id':kb_id})
        config=resolved['config'];merged={**config.get('search_defaults',{}),**(options or {})}
        o=RetrievalOptions.model_validate(merged).model_dump()
        if o['candidate_k']<o['top_k']:raise ValueError('candidate_k must be at least top_k')
        vector=[]
        if o['mode']!='keyword' and not (o['mode']=='hybrid' and o['vector_weight']==0):
            vector=(await self.embeddings.embed(config.get('embedding_model',''),[query],config.get('embedding_digest','')))[0]
        sources=await self.index.call('search',tenant,{'kb_id':kb_id,'version':resolved['version'],'segment_id':resolved['segment_id'],'document_ids':[d['id'] for d in resolved['documents']],'query':query,'query_vector':vector,'options':o})
        await self.verify(sources,tenant)
        return {'query':query,'knowledge_base_id':kb_id,'version':resolved['version'],'sources':sources,'status':'results' if sources else 'no_matches'}
    async def verify(self,sources,tenant):
        if not isinstance(sources,list) or len(sources)>120:raise ValueError('Invalid knowledge sources')
        groups=defaultdict(set)
        for s in sources:
            if not isinstance(s,dict):raise ValueError('Invalid source metadata')
            groups[(s['knowledge_base_id'],s['version'])].add(s['document_id'])
        out=[]
        for offset in range(0,len(sources),20):out.extend(await self.index.call('verify',tenant,{'sources':sources[offset:offset+20]}))
        # Authoritative deletion check comes after potentially slow index IO.
        for (kb,version),docs in groups.items():await self.management.call('verify',tenant,{'kb_id':kb,'version':version,'document_ids':list(docs)})
        return out
    def verify_sync(self,sources,tenant):
        groups=defaultdict(set)
        for source in sources:groups[(source['knowledge_base_id'],source['version'])].add(source['document_id'])
        for (kb,version),docs in groups.items():self.management.call_sync('verify',tenant,{'kb_id':kb,'version':version,'document_ids':list(docs)})
        for offset in range(0,len(sources),20):self.index.call_sync('verify',tenant,{'sources':sources[offset:offset+20]})
        return sources

async def checked_config(raw,store,tenant,embeddings):
    if not isinstance(raw,dict):raise ValueError('Invalid knowledge configuration')
    allowed={'backend','storage_path','embedding_model','embedding_digest','chunking','chunk_size','chunk_overlap','index_method','connection_id','index_name','search_defaults'}
    if set(raw)-allowed:raise ValueError('Unknown knowledge configuration fields')
    settings={k:v for k,v in raw.items() if k not in ('search_defaults','embedding_digest')}
    # Keyword-only bases (blank embedding model) are valid and can be rebuilt with embeddings later.
    c=KBSettings.model_validate(settings).model_dump();model=c['embedding_model']
    import re
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',c['storage_path']):raise ValueError('Choose a logical storage path using letters, numbers, dash and underscore')
    if c['chunk_overlap']>=c['chunk_size']:raise ValueError('Chunk overlap must be smaller than chunk size')
    cap=CAPABILITIES[c['backend']];c['index_method']=c['index_method'] or cap['index_methods'][0]
    if c['index_method'] not in cap['index_methods']:raise ValueError('Unsupported index method for this backend')
    options=RetrievalOptions.model_validate(raw.get('search_defaults',{'mode':'similarity' if model else 'keyword'})).model_dump()
    if options['candidate_k']<options['top_k']:raise ValueError('candidate_k must be at least top_k')
    c['search_defaults']=options;c['embedding_digest']=await embeddings.fingerprint(model,require_embedding=True) if model else ''
    connection=None
    if not cap['local']:
        connection=ToolService(store).resolve(c['connection_id'],tenant)
        if connection['provider']!=c['backend']:raise ValueError('Connection provider must match the vector backend')
        if c['backend']=='elasticsearch' and not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,100}',c['index_name']):raise ValueError('Select an existing Elasticsearch index')
    return c,connection

def install_kb_routes(app,store,tenant_dependency,services=None):
    services=services or KnowledgeServices();app.state.knowledge_services=services
    async def invoke(action,tenant,payload):
        try:return await services.management.call(action,tenant,payload)
        except KeyError:raise HTTPException(404,'Knowledge base or document unavailable') from None
        except ServiceUnavailable as e:raise HTTPException(503,str(e)) from None
        except ValueError as e:raise HTTPException(400,str(e)) from None
    async def settings(config,tenant):
        try:return await checked_config(config,store,tenant,services.embeddings)
        except KeyError:raise HTTPException(404,'Connection unavailable') from None
        except ValueError as e:raise HTTPException(400,str(e)) from None
    @app.get('/api/knowledge-bases')
    async def listing(tenant=Depends(tenant_dependency)):return await invoke('list',tenant,{})
    @app.get('/api/knowledge-bases/capabilities')
    async def capabilities(tenant=Depends(tenant_dependency)):
        from .providers import ollama_models
        discovery=await ollama_models()
        # Only embedding-capable models are offered; a chat model would index silently and search badly.
        return {'backends':CAPABILITIES,'embedding_models':[m for m in discovery['models'] if supports(m,'embedding')],'embedding_connected':discovery['connected'],'extensions':['pdf','txt','md','markdown','csv','json','html','htm','docx'],'max_file_bytes':25*1024*1024}
    @app.post('/api/knowledge-bases',status_code=201)
    async def create(body:KBCreate,request:Request,tenant=Depends(tenant_dependency)):
        config,connection=await settings(body.config,tenant)
        return await invoke('create',tenant,{'name':body.name,'description':body.description,'config':config,'connection':connection,'idempotency_key':request.headers.get('Idempotency-Key')})
    @app.get('/api/knowledge-bases/{kb_id}')
    async def detail(kb_id:str,tenant=Depends(tenant_dependency)):return await invoke('get',tenant,{'kb_id':kb_id})
    @app.put('/api/knowledge-bases/{kb_id}')
    async def update(kb_id:str,body:KBUpdate,tenant=Depends(tenant_dependency)):return await invoke('update',tenant,{'kb_id':kb_id,**body.model_dump(exclude_none=True)})
    @app.post('/api/knowledge-bases/{kb_id}/rebuild')
    async def rebuild(kb_id:str,body:KBRebuild,tenant=Depends(tenant_dependency)):
        await invoke('get',tenant,{'kb_id':kb_id})
        config,connection=await settings(body.config,tenant)
        return await invoke('rebuild',tenant,{'kb_id':kb_id,'config':config,'connection':connection})
    @app.post('/api/knowledge-bases/{kb_id}/cancel')
    async def cancel(kb_id:str,tenant=Depends(tenant_dependency)):return await invoke('cancel',tenant,{'kb_id':kb_id})
    @app.post('/api/knowledge-bases/{kb_id}/delete')
    async def delete(kb_id:str,tenant=Depends(tenant_dependency)):return await invoke('delete',tenant,{'kb_id':kb_id})
    @app.post('/api/knowledge-bases/{kb_id}/documents',status_code=202)
    async def upload(kb_id:str,request:Request,filename:str='document.txt',replace_document_id:str|None=None,tenant=Depends(tenant_dependency)):
        await invoke('get',tenant,{'kb_id':kb_id});content=bytearray()
        async for part in request.stream():
            if len(content)+len(part)>25*1024*1024:raise HTTPException(413,'File limit is 25 MB')
            content.extend(part)
        import uuid
        return await invoke('upload',tenant,{'kb_id':kb_id,'filename':filename,'content_b64':base64.b64encode(content).decode(),'idempotency_key':request.headers.get('Idempotency-Key') or uuid.uuid4().hex,'replace_document_id':replace_document_id})
    @app.get('/api/knowledge-bases/{kb_id}/documents/{doc_id}/file')
    async def download(kb_id:str,doc_id:str,tenant=Depends(tenant_dependency)):
        result=await invoke('download',tenant,{'kb_id':kb_id,'document_id':doc_id});filename=result['filename'];pdf=filename.lower().endswith('.pdf')
        return Response(base64.b64decode(result['content_b64']),media_type='application/pdf' if pdf else 'application/octet-stream',headers={'Content-Disposition':('inline' if pdf else 'attachment')+"; filename*=UTF-8''"+quote(filename,safe=''),'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':'sandbox'})
    @app.post('/api/knowledge-bases/{kb_id}/documents/{doc_id}/retry')
    async def retry(kb_id:str,doc_id:str,tenant=Depends(tenant_dependency)):return await invoke('retry',tenant,{'kb_id':kb_id,'document_id':doc_id})
    @app.post('/api/knowledge-bases/{kb_id}/documents/{doc_id}/remove')
    async def remove(kb_id:str,doc_id:str,tenant=Depends(tenant_dependency)):return await invoke('remove_document',tenant,{'kb_id':kb_id,'document_id':doc_id})
    @app.post('/api/knowledge-bases/{kb_id}/search')
    async def search(kb_id:str,body:KBSearch,tenant=Depends(tenant_dependency)):
        try:
            start=time.monotonic();result=await services.retrieve(kb_id,body.query,body.options.model_dump(exclude_unset=True) if body.options else None,tenant)
            return {**result,'elapsed_ms':round((time.monotonic()-start)*1000,1)}
        except KeyError:raise HTTPException(404,'Knowledge base unavailable') from None
        except ServiceUnavailable as e:raise HTTPException(503,str(e)) from None
        except ValueError as e:raise HTTPException(400,str(e)) from None
    @app.get('/api/knowledge-bases/{kb_id}/documents/{doc_id}/preview')
    async def preview(kb_id:str,doc_id:str,tenant=Depends(tenant_dependency)):
        resolved=await invoke('resolve',tenant,{'kb_id':kb_id})
        await invoke('verify',tenant,{'kb_id':kb_id,'version':resolved['version'],'document_ids':[doc_id]})
        try:
            result=await services.index.call('preview',tenant,{'kb_id':kb_id,'segment_id':resolved['segment_id'],'document_id':doc_id,'limit':10})
            await services.verify(result['sources'],tenant)
            return result
        except KeyError:raise HTTPException(404,'Document preview unavailable') from None
        except ServiceUnavailable as e:raise HTTPException(503,str(e)) from None
        except ValueError as e:raise HTTPException(400,str(e)) from None
