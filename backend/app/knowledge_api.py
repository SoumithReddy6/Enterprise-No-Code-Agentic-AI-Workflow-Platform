"""HTTP endpoints for authorized PDF knowledge resources."""
from urllib.parse import quote
from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import Field
from .models import StrictModel

class BaseCreate(StrictModel):
    name:str=Field(min_length=1,max_length=120)

def install_knowledge_routes(app,knowledge,tenant_dependency):
    def invoke(fn,*args):
        try:return fn(*args)
        except KeyError as exc:raise HTTPException(404,'Knowledge resource unavailable') from exc
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @app.get('/api/knowledge')
    def bases(tenant=Depends(tenant_dependency)):return invoke(knowledge.bases,tenant)

    @app.post('/api/knowledge')
    def create(body:BaseCreate,tenant=Depends(tenant_dependency)):return invoke(knowledge.create,body.name,tenant)

    @app.get('/api/knowledge/{base_id}/documents')
    def documents(base_id:str,tenant=Depends(tenant_dependency)):return invoke(knowledge.documents,base_id,tenant)

    @app.post('/api/knowledge/{base_id}/documents',status_code=202)
    async def upload(base_id:str,request:Request,filename:str='document.pdf',tenant=Depends(tenant_dependency)):
        invoke(knowledge.check_base,base_id,tenant)
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/pdf':raise HTTPException(415,'Upload an application/pdf body')
        content=bytearray()
        async for part in request.stream():
            if len(content)+len(part)>knowledge.max_bytes:raise HTTPException(413,'PDF must be at most 25 MB')
            content.extend(part)
        return invoke(knowledge.upload,base_id,filename,bytes(content),tenant)

    @app.post('/api/documents/{document_id}/remove')
    def remove(document_id:str,tenant=Depends(tenant_dependency)):return invoke(knowledge.remove,document_id,tenant)

    @app.post('/api/documents/{document_id}/retry')
    def retry(document_id:str,tenant=Depends(tenant_dependency)):return invoke(knowledge.retry,document_id,tenant)

    @app.get('/api/documents/{document_id}/file')
    def download(document_id:str,tenant=Depends(tenant_dependency)):
        filename,content=invoke(knowledge.download,document_id,tenant)
        return Response(content,media_type='application/pdf',headers={'Content-Disposition':"inline; filename*=UTF-8''"+quote(filename,safe=''),'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox"})
