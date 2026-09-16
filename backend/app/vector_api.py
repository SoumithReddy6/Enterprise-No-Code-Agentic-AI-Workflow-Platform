"""Tenant-authorized vector resource and upload endpoints."""
from typing import Literal
from urllib.parse import quote
from fastapi import Depends,HTTPException,Request
from fastapi.responses import Response
from pydantic import Field,field_validator
from .models import StrictModel

class ResourceCreate(StrictModel):
    name:str=Field(min_length=1,max_length=120)
    backend:Literal['faiss','chroma','elasticsearch','pinecone']='faiss'
    storage_path:str=Field(default='default',min_length=1,max_length=64)
    embedding_model:str=Field(min_length=1,max_length=100)
    chunk_size:int=Field(default=1200,ge=100,le=8000)
    chunk_overlap:int=Field(default=200,ge=0,le=4000)
    chunking:Literal['fixed','paragraph']='fixed'
    index_method:str=Field(default='',max_length=40)
    connection_id:str=Field(default='',max_length=64)
    index_name:str=Field(default='',max_length=101)
    @field_validator('name','embedding_model')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():raise ValueError('Name and embedding model cannot be blank')
        return value.strip()

class RetrievalOptions(StrictModel):
    mode:Literal['similarity','keyword','hybrid','rrf']='similarity'
    top_k:int=Field(default=4,ge=1,le=20)
    candidate_k:int=Field(default=20,ge=1,le=100)
    score_threshold:float|None=Field(default=None,allow_inf_nan=False)
    rrf_k:int=Field(default=60,ge=1,le=1000)
    vector_weight:float=Field(default=.5,ge=0,le=1,allow_inf_nan=False)
    filter:dict[str,str]=Field(default_factory=dict)
    @field_validator('filter')
    @classmethod
    def valid_filter(cls,value):
        if set(value)-{'filename','document_id'} or any(len(v)>240 for v in value.values()):raise ValueError('Supported metadata filters: filename, document_id')
        # Files use id internally; keep the external source field document_id.
        return value

def install_vector_routes(app,service,tenant_dependency):
    def invoke(fn,*args):
        try:return fn(*args)
        except KeyError as exc:raise HTTPException(404,'Vector resource unavailable') from exc
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    @app.get('/api/vector-resources')
    def resources(tenant=Depends(tenant_dependency)):return invoke(service.resources,tenant)
    @app.post('/api/vector-resources')
    def create(body:ResourceCreate,tenant=Depends(tenant_dependency)):return invoke(service.create,body.model_dump(),tenant)
    @app.get('/api/vector-resources/{id}/files')
    def files(id:str,tenant=Depends(tenant_dependency)):return invoke(service.files,id,tenant)
    @app.post('/api/vector-resources/{id}/files',status_code=202)
    async def upload(id:str,request:Request,filename:str='document.txt',tenant=Depends(tenant_dependency)):
        invoke(service.resource,id,tenant);content=bytearray()
        async for part in request.stream():
            if len(content)+len(part)>service.max_bytes:raise HTTPException(413,'File limit is 25 MB')
            content.extend(part)
        return invoke(service.upload,id,filename,bytes(content),tenant)
    @app.get('/api/vector-files/{id}/file')
    def download(id:str,tenant=Depends(tenant_dependency)):
        filename,content=invoke(service.download,id,tenant)
        return Response(content,media_type='application/pdf' if filename.lower().endswith('.pdf') else 'application/octet-stream',headers={'Content-Disposition':('inline' if filename.lower().endswith('.pdf') else 'attachment')+"; filename*=UTF-8''"+quote(filename,safe=''),'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':'sandbox'})
    @app.post('/api/vector-files/{id}/remove')
    def remove(id:str,tenant=Depends(tenant_dependency)):return invoke(service.remove,id,tenant)
    @app.post('/api/vector-files/{id}/retry')
    def retry(id:str,tenant=Depends(tenant_dependency)):return invoke(service.retry,id,tenant)
    @app.get('/api/vector-capabilities')
    async def capabilities(tenant=Depends(tenant_dependency)):return await service.capabilities()
