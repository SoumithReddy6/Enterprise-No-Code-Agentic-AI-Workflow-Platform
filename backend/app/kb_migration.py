"""Explicit, resumable copies of legacy resources; old workflows remain intact."""
import base64
from sqlalchemy import Column,String,select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .storage import Base
from .knowledge import Knowledge
from .vector_service import VectorService
from .tool_service import ToolService

class LegacyImport(Base):
    __tablename__='kb_legacy_imports'
    tenant_id=Column(String(64),primary_key=True)
    kind=Column(String(16),primary_key=True)
    legacy_id=Column(String(64),primary_key=True)
    kb_id=Column(String(64),nullable=False)

def legacy_resources(store,tenant):
    pdf=Knowledge(store);vector=VectorService(store)
    return [{'kind':'pdf','id':k['id'],'name':k['name'],'document_count':len(pdf.documents(k['id'],tenant))} for k in pdf.bases(tenant)]+[{'kind':'vector','id':k['id'],'name':k['name'],'document_count':len(vector.files(k['id'],tenant))} for k in vector.resources(tenant)]

async def import_legacy(store,services,tenant,kind,resource_id):
    if kind not in ('pdf','vector'):raise ValueError('Select a legacy PDF or vector resource')
    # Always check source ownership, even on an idempotent replay.
    if kind=='pdf':
        old=Knowledge(store);base=next((x for x in old.bases(tenant) if x['id']==resource_id),None)
        if not base:raise KeyError('Legacy knowledge unavailable')
        files=old.documents(resource_id,tenant)
        config={'backend':'faiss','storage_path':'import-'+resource_id[:32],'embedding_model':'','search_defaults':{'mode':'keyword'}};connection={}
    else:
        old=VectorService(store);base=old.resource(resource_id,tenant);files=old.files(resource_id,tenant)
        keys={'backend','storage_path','embedding_model','chunking','chunk_size','chunk_overlap','index_method','connection_id','index_name'}
        config={k:v for k,v in base.items() if k in keys};config['storage_path']='import-'+resource_id[:32]
        connection=ToolService(store).resolve(config['connection_id'],tenant) if config['backend'] in ('elasticsearch','pinecone') else {}
    LegacyImport.__table__.create(store.engine,checkfirst=True)
    identity=(tenant,kind,resource_id)
    with Session(store.engine) as session:row=session.get(LegacyImport,identity);kb_id=row.kb_id if row else None
    if kb_id is None:
        result=await services.management.call('create',tenant,{'name':base['name'],'description':'Imported copy of a legacy '+kind+' knowledge resource.','config':config,'connection':connection,'idempotency_key':f'legacy:{kind}:{resource_id}'})
        kb_id=result['id']
        with Session(store.engine) as session:
            session.add(LegacyImport(tenant_id=tenant,kind=kind,legacy_id=resource_id,kb_id=kb_id))
            try:session.commit()
            except IntegrityError:session.rollback();kb_id=session.get(LegacyImport,identity).kb_id
    await services.management.call('get',tenant,{'kb_id':kb_id})
    for file in files:
        filename,content=old.download(file['id'],tenant)
        await services.management.call('upload',tenant,{'kb_id':kb_id,'filename':filename,'content_b64':base64.b64encode(content).decode(),'idempotency_key':f'legacy:{kind}:{file["id"]}'})
    return await services.management.call('get',tenant,{'kb_id':kb_id})
