"""Transactional KB manifests, original blobs and fenced durable work queue.

Originals live in the management database so registering an upload and its build
is one durable transaction. All processes serialize manifest changes using the
same database lock (SQLite immediate transaction / PostgreSQL advisory lock).
"""
from ..observability import log_failures,request_id
import base64
import copy
import hashlib
import json
import time
import uuid
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select, text, delete, inspect
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from .rpc import KnowledgeConflict
from .management_models import Base, KnowledgeBase, Original
from ..vector_adapters import CAPABILITIES
from .options import RetrievalOptions


def uid():
    return uuid.uuid4().hex


class Management:
    ACTIONS = {'create','list','get','update','rebuild','upload','retry','remove_document',
               'delete','cancel','resolve','verify','claim','artifact','progress','renew',
               'fail','publish','cleanup_list','cleanup_result','build_status','download'}

    def __init__(self, database_url, root, secret_key):
        self.database_url, self.root, self.secret_key = database_url, Path(root), secret_key
        self.cipher = Fernet(secret_key.encode() if isinstance(secret_key, str) else secret_key)
        self.engine = create_engine(database_url, connect_args={'check_same_thread':False,'timeout':30} if database_url.startswith('sqlite') else {})
        self._migrate_names()

    @staticmethod
    def _name_key(name):
        return ' '.join(name.split()).casefold()

    def _migrate_names(self):
        # The same lock as manifest writes serializes multi-process startup.
        with self.engine.begin() as connection:
            if self.engine.dialect.name == 'sqlite':
                connection.execute(text('BEGIN IMMEDIATE'))
            elif self.engine.dialect.name == 'postgresql':
                connection.execute(text('SELECT pg_advisory_xact_lock(716293846)'))
            Base.metadata.create_all(connection)
            columns = {c['name'] for c in inspect(connection).get_columns('kb_management_bases')}
            records = connection.execute(text('SELECT id, tenant_id, state FROM kb_management_bases')).all()
            keys, updates = {}, []
            for ident, tenant, state in records:
                state = json.loads(state) if isinstance(state, str) else state
                key = None if state['status'] == 'deleted' else self._name_key(state['name'])
                if key is not None:
                    keys.setdefault((tenant, key), []).append(ident)
                updates.append({'id': ident, 'key': key})
            duplicates = [(tenant, key, sorted(ids)) for (tenant, key), ids in keys.items() if len(ids) > 1]
            if duplicates:
                raise RuntimeError('Knowledge base name migration blocked. Back up the database, then rename conflicting active bases in their stored state and restart. Conflicts (tenant, normalized name, IDs): ' + repr(sorted(duplicates)))
            if 'name_key' not in columns:
                connection.execute(text('ALTER TABLE kb_management_bases ADD COLUMN name_key VARCHAR(600)'))
            if updates:
                connection.execute(text('UPDATE kb_management_bases SET name_key = :key WHERE id = :id'), updates)
            connection.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_management_tenant_name ON kb_management_bases (tenant_id, name_key)'))

    def _name_conflict(self, submitted):
        return KnowledgeConflict({'code': 'knowledge_base_name_conflict', 'message': 'A knowledge base with this name already exists. Choose another name.', 'name': submitted})

    def _config(self, raw):
        if not isinstance(raw, dict): raise ValueError('config must be an object')
        allowed={'backend','storage_path','embedding_model','embedding_digest','chunking','chunk_size','chunk_overlap','index_method','connection_id','index_name','search_defaults'}
        if set(raw)-allowed: raise ValueError('Unknown configuration fields')
        c={'backend':'faiss','storage_path':'default','embedding_model':'','embedding_digest':'','chunking':'fixed','chunk_size':1200,'chunk_overlap':200,'index_method':'','connection_id':'','index_name':'','search_defaults':{}}
        c.update(copy.deepcopy(raw))
        if c['backend']=='chroma':
            from ..vector_adapters import RETIRED_CHROMA
            raise ValueError(RETIRED_CHROMA)
        if c['backend'] not in {'faiss','elasticsearch','pinecone'}: raise ValueError('Unsupported backend')
        if c['chunking'] not in {'fixed','paragraph','section'}: raise ValueError('Unsupported chunking')
        if not isinstance(c['chunk_size'],int) or not 100<=c['chunk_size']<=8000: raise ValueError('Invalid chunk size')
        if not isinstance(c['chunk_overlap'],int) or not 0<=c['chunk_overlap']<=4000 or c['chunk_overlap']>=c['chunk_size']: raise ValueError('Invalid overlap')
        if not isinstance(c['storage_path'],str) or not c['storage_path'] or len(c['storage_path'])>64 or any(x not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for x in c['storage_path']): raise ValueError('storage_path must be a logical slug')
        caps=CAPABILITIES[c['backend']]
        c['index_method']=c['index_method'] or caps['index_methods'][0]
        if c['index_method'] not in caps['index_methods']: raise ValueError('Unsupported index method')
        c['search_defaults']=RetrievalOptions.model_validate(c['search_defaults']).model_dump()
        for key,limit in [('embedding_model',100),('embedding_digest',255),('connection_id',64),('index_name',101)]:
            if not isinstance(c[key],str) or len(c[key])>limit: raise ValueError('Invalid '+key)
        return c

    def _encrypt(self, connection):
        return self.cipher.encrypt(json.dumps(connection or {}).encode()).decode()

    def _connection(self, version):
        return json.loads(self.cipher.decrypt(version['connection'].encode()))

    def _public(self, k, detail=False):
        live=self._desired(k)
        out={a:copy.deepcopy(k[a]) for a in ('id','name','description','config','status','active_version','pending_version','created_at','updated_at')}
        active=next((v for v in k['versions'] if v['version']==k['active_version']),None)
        out.update(document_count=len(live),chunk_count=active.get('chunk_count',0) if active else 0)
        if detail:
            out['documents']=copy.deepcopy(k['documents'])
            out['jobs']=[{a:copy.deepcopy(b) for a,b in j.items() if a not in {'connection','documents','config'}} for j in k['jobs']]
            out['versions']=[{a:copy.deepcopy(b) for a,b in v.items() if a!='connection'} for v in k['versions']]
        return out

    def _retire(self,k,j,status):
        j.update(status=status,cleanup_status='pending',lease_until=0,next_cleanup_at=0,cleanup_failures=0)
        v=next(v for v in k['versions'] if v['version']==j['version'])
        v['status']=status

    def _desired(self,k):
        live=[d for d in k['documents'] if d['status']!='removed']
        superseded={d.get('replaces_document_id') for d in live}
        return [d for d in live if d['id'] not in superseded]

    def _restore_after_failure(self,k,error):
        active=next((v for v in k['versions'] if v['version']==k['active_version']),{'documents':[]})
        ids={d['id'] for d in active['documents']}
        for d in k['documents']:
            if d['status']!='removed':
                d.update(status='ready' if d['id'] in ids else 'failed',error='' if d['id'] in ids else error)

    def _schedule(self,k,config=None,connection=None):
        pending=next((v for v in k['versions'] if v['version']==k['pending_version']),None)
        if config is None and pending:
            config,connection=pending['config'],pending['connection']
        for j in k['jobs']:
            if j['status'] in {'queued','processing'}: self._retire(k,j,'cancelled')
        version=max([v['version'] for v in k['versions']],default=0)+1
        docs=[{a:d[a] for a in ('id','filename','content_hash')} for d in self._desired(k)]
        attempt=uid()
        v={'version':version,'config':copy.deepcopy(config or k['config']),'connection':connection or k['connection'],'documents':docs,'status':'pending','segment_id':attempt}
        k['versions'].append(v)
        k['jobs'].append({'request_id':request_id.get(),'id':uid(),'attempt_id':attempt,'version':version,'status':'queued','stage':'queued','error':'','cleanup_status':'none','cleanup_attempts':0,'next_cleanup_at':0,'cleanup_failures':0,'lease_until':0,'created_at':time.time(),'retries':0})
        k.update(pending_version=version,status='indexing',updated_at=time.time())
        for d in self._desired(k): d.update(status='queued',error='')

    @log_failures('management')
    def call(self, action, tenant, payload):
        if action not in self.ACTIONS: raise ValueError('Unsupported management action')
        p=payload
        with Session(self.engine) as s:
            if self.engine.dialect.name=='sqlite': s.execute(text('BEGIN IMMEDIATE'))
            elif self.engine.dialect.name=='postgresql': s.execute(text('SELECT pg_advisory_xact_lock(716293846)'))
            query=select(KnowledgeBase)
            if action not in {'claim','cleanup_list','cleanup_result','build_status'}:
                query=query.where(KnowledgeBase.tenant_id==str(tenant))
            rows=list(s.scalars(query))
            states={r.id:copy.deepcopy(r.state) for r in rows}
            result=self._dispatch(s,states,action,str(tenant),p)
            for row in rows:
                if row.state!=states[row.id]: row.state=states[row.id]
                row.name_key = None if row.state['status']=='deleted' else self._name_key(row.state['name'])
            try:
                s.commit()
            except IntegrityError as exc:
                s.rollback()
                # Keep unrelated integrity failures visible as service failures.
                if action in {'create','update'} and 'name' in p and ('uq_kb_management_tenant_name' in str(exc.orig) or 'kb_management_bases.tenant_id, kb_management_bases.name_key' in str(exc.orig)):
                    raise self._name_conflict(p['name']) from None
                raise
            return result

    def _dispatch(self,s,states,action,tenant,p):
        now=time.time()
        def owned():
            k=states.get(p.get('kb_id'))
            if not k or k['tenant_id']!=tenant or k['status']=='deleted': raise KeyError('Knowledge base not found')
            return k
        def document(k):
            d=next((d for d in k['documents'] if d['id']==p.get('document_id') and d['status']!='removed'),None)
            if not d: raise KeyError('Document not found')
            return d
        def job():
            for k in states.values():
                for j in k['jobs']:
                    if j['id']==p.get('job_id'):
                        if k['tenant_id']!=tenant: raise KeyError('Job not found')
                        if j['attempt_id']!=p.get('attempt_id'): raise ValueError('Stale attempt')
                        return k,j,next(v for v in k['versions'] if v['version']==j['version'])
            raise KeyError('Job not found')
        def live(k,j):
            if k['status']=='deleted' or j['status']!='processing' or j['lease_until']<=now or k['pending_version']!=j['version']: raise ValueError('Expired or cancelled lease')
        if action=='create':
            name=str(p.get('name','')).strip()
            if not name or len(name)>200: raise ValueError('Name required, maximum 200 characters')
            config=self._config(p.get('config',{})); key=p.get('idempotency_key')
            fingerprint=hashlib.sha256(json.dumps(p,sort_keys=True).encode()).hexdigest()
            for k in states.values():
                if key and k['tenant_id']==tenant and k.get('create_key')==key:
                    if k['create_fingerprint']!=fingerprint: raise ValueError('Idempotency conflict')
                    return self._public(k)
            if any(k['status']!='deleted' and self._name_key(k['name'])==self._name_key(name) for k in states.values()):
                raise self._name_conflict(p.get('name',''))
            k={'id':uid(),'tenant_id':tenant,'name':name,'description':str(p.get('description','')),'config':config,'connection':self._encrypt(p.get('connection')),'status':'empty','active_version':None,'pending_version':None,'created_at':now,'updated_at':now,'documents':[],'versions':[],'jobs':[],'uploads':{},'create_key':key,'create_fingerprint':fingerprint}
            s.add(KnowledgeBase(id=k['id'],tenant_id=tenant,state=k,name_key=self._name_key(name))); return self._public(k)
        if action=='list': return [self._public(k) for k in states.values() if k['tenant_id']==tenant and k['status']!='deleted']
        if action=='claim':
            for k in states.values():
                if k['status']=='deleted': continue
                for j in list(k['jobs']):
                    if j['status']=='processing' and j['lease_until']<=now:
                        self._retire(k,j,'expired')
                        v=next(v for v in k['versions'] if v['version']==j['version'])
                        if j['retries']>=2:
                            k.update(status='degraded' if k['active_version'] else 'failed',pending_version=None)
                            self._restore_after_failure(k,'Worker lease expired after retries')
                            continue
                        self._schedule(k,v['config'],v['connection']); k['jobs'][-1]['retries']=j['retries']+1
                j=next((j for j in k['jobs'] if j['status']=='queued'),None)
                if j:
                    j.update(status='processing',stage='extracting',lease_until=now+30)
                    for d in self._desired(k): d['status']='processing'
                    v=next(v for v in k['versions'] if v['version']==j['version'])
                    return {**copy.deepcopy(j),'kb_id':k['id'],'tenant_id':k['tenant_id'],'config':v['config'],'connection':self._connection(v),'documents':v['documents'],'lease_seconds':30}
            return None
        if action in {'artifact','renew','progress','fail','publish'}:
            k,j,v=job()
            if action=='publish' and j['status']=='succeeded':
                if v['segment_id']!=p.get('segment_id') or v['chunk_count']!=p.get('chunk_count'): raise ValueError('Publish conflict')
                if k['status']=='deleted': raise ValueError('Deleted knowledge base')
                return {'ok':True}
            live(k,j)
            if action=='artifact':
                if not any(d['id']==p.get('document_id') for d in v['documents']): raise KeyError('Document not in build')
                blob=s.get(Original,p['document_id']); d=next(d for d in v['documents'] if d['id']==p['document_id'])
                if blob is None: raise KeyError('Original not found')
                return {'filename':d['filename'],'content_b64':base64.b64encode(blob.data).decode()}
            if action in {'renew','progress'}:
                j['lease_until']=now+30
                if action=='progress':
                    j['stage']=str(p.get('stage','processing'))[:100]
                    for a in ('completed','total'):
                        if a in p: j[a]=max(0,int(p[a]))
            if action=='fail':
                self._retire(k,j,'failed'); j['error']=str(p.get('error','Build failed'))[:2000]
                k.update(pending_version=None,status='degraded' if k['active_version'] else 'failed')
                self._restore_after_failure(k,j['error'])
            if action=='publish':
                if p.get('segment_id')!=j['attempt_id']: raise ValueError('Segment must match attempt')
                count=int(p['chunk_count'])
                if count<0 or count>50000: raise ValueError('Chunk limit exceeded')
                others=sum(vv.get('chunk_count',0) for kk in states.values() if kk['tenant_id']==k['tenant_id'] and kk['id']!=k['id'] and kk['status']!='deleted' for vv in kk['versions'] if vv['version']==kk['active_version'])
                if count+others>50000: raise ValueError('Workspace chunk limit exceeded')
                for old in k['jobs']:
                    if old['status']=='succeeded' and old['version']==k['active_version']:
                        old.update(cleanup_status='pending',next_cleanup_at=0,cleanup_failures=0)
                v.update(status='active',chunk_count=count,embedding_digest=p.get('embedding_digest',''),dimensions=p.get('dimensions'))
                if p.get('embedding_digest'): v['config']['embedding_digest']=p['embedding_digest']
                k.update(active_version=j['version'],pending_version=None,config=copy.deepcopy(v['config']),connection=v['connection'],status='ready')
                j.update(status='succeeded',stage='ready',lease_until=0)
                published={d['id'] for d in v['documents']}
                superseded={d.get('replaces_document_id') for d in k['documents'] if d['id'] in published}
                for d in k['documents']:
                    if d['id'] in superseded:
                        d['status']='removed'; s.execute(delete(Original).where(Original.id==d['id']))
                    elif d['id'] in published: d.update(status='ready',error='')
            k['updated_at']=now
            return {'ok':True}
        if action=='cleanup_list':
            return [{'kb_id':k['id'],'tenant_id':k['tenant_id'],'segment_id':j['attempt_id'],'retain_provenance':j['status']=='succeeded' and k['status']!='deleted'} for k in states.values() for j in k['jobs'] if j['cleanup_status']!='none' and j.get('next_cleanup_at',0)<=now and (k['status']=='deleted' or j['version']!=k['active_version']) and j['status'] not in {'queued','processing'}]
        if action=='cleanup_result':
            for k in states.values():
                for j in k['jobs']:
                    if j['attempt_id']==p.get('segment_id'):
                        if j['cleanup_status']=='none' or (k['status']!='deleted' and j['version']==k['active_version']): raise ValueError('Segment not eligible for cleanup')
                        failures=0 if p.get('ok') else j.get('cleanup_failures',0)+1
                        delay=300 if p.get('ok') else min(300,5*(2**min(failures-1,6)))
                        j.update(cleanup_status='done' if p.get('ok') else 'failed',cleanup_attempts=j['cleanup_attempts']+1,cleanup_error=str(p.get('error',''))[:2000],cleanup_failures=failures,next_cleanup_at=now+delay)
                        return {'ok':True}
            raise KeyError('Segment not found')
        if action=='build_status':
            k=states.get(p.get('kb_id'))
            return {'allowed':bool(k and k['status']!='deleted' and any(j['attempt_id']==p.get('segment_id') and ((j['status']=='processing' and j['lease_until']>now and j['version']==k['pending_version']) or (j['status']=='succeeded' and j['version']==k['active_version'])) for j in k['jobs']))}
        k=owned()
        if action=='get': return self._public(k,True)
        if action=='update':
            if 'name' in p:
                name=str(p['name']).strip()
                if not name or len(name)>200: raise ValueError('Invalid name')
                if any(other['id']!=k['id'] and other['status']!='deleted' and self._name_key(other['name'])==self._name_key(name) for other in states.values()):
                    raise self._name_conflict(p['name'])
                k['name']=name
            if 'description' in p: k['description']=str(p['description'])
        if action=='upload':
            name=str(p.get('filename',''))
            if not name or len(name)>240 or '/' in name or '\\' in name or '\x00' in name: raise ValueError('Invalid filename')
            if Path(name).suffix.lower() not in {'.pdf','.txt','.md','.markdown','.csv','.json','.html','.htm','.docx'}: raise ValueError('Unsupported file type')
            encoded=p.get('content_b64','')
            if not isinstance(encoded,str) or len(encoded)>34952536: raise ValueError('File exceeds 25 MB')
            try: data=base64.b64decode(encoded,validate=True)
            except Exception as e: raise ValueError('Invalid base64') from e
            if not data or len(data)>25*1024*1024: raise ValueError('File must contain 1 byte to 25 MB')
            key=p.get('idempotency_key')
            if not isinstance(key,str) or not key or len(key)>200: raise ValueError('Idempotency key required')
            digest=hashlib.sha256(data).hexdigest()
            fingerprint=hashlib.sha256(json.dumps([name,digest,p.get('replace_document_id')]).encode()).hexdigest()
            if key in k['uploads']:
                old=k['uploads'][key]
                if old['fingerprint']!=fingerprint: raise ValueError('Idempotency conflict')
                return copy.deepcopy(next(d for d in k['documents'] if d['id']==old['id']))
            replaced=None
            if p.get('replace_document_id'):
                replaced=next((d for d in k['documents'] if d['id']==p['replace_document_id'] and d['status']!='removed'),None)
                if not replaced: raise KeyError('Replacement document not found')
            if len(self._desired(k))-(1 if replaced else 0)>=20: raise ValueError('Maximum 20 documents')
            replacement_id=replaced['id'] if replaced else None
            if replaced:
                ancestor=next((d for d in k['documents'] if d['id']==replaced.get('replaces_document_id') and d['status']!='removed'),None)
                if ancestor: replacement_id=ancestor['id']
                # A new replacement supersedes a previous uncommitted replacement,
                # while the published original remains authorized until promotion.
                for prior in k['documents']:
                    if prior['status']!='removed' and prior.get('replaces_document_id')==replacement_id:
                        prior['status']='removed'; s.execute(delete(Original).where(Original.id==prior['id']))
            d={'id':uid(),'filename':name,'status':'queued','content_hash':digest,'bytes':len(data),'error':'','created_at':now}
            if replacement_id: d['replaces_document_id']=replacement_id
            k['documents'].append(d); k['uploads'][key]={'id':d['id'],'fingerprint':fingerprint}
            s.add(Original(id=d['id'],kb_id=k['id'],data=data)); self._schedule(k)
            return copy.deepcopy(d)
        if action=='rebuild': self._schedule(k,self._config(p['config']),self._encrypt(p['connection']) if 'connection' in p else None)
        if action=='retry':
            document(k)
            last=k['versions'][-1] if k['versions'] else None
            self._schedule(k,last['config'] if last else None,last['connection'] if last else None)
        if action=='remove_document':
            d=document(k)
            for removed in k['documents']:
                if removed['id']==d['id'] or removed.get('replaces_document_id')==d['id']:
                    removed['status']='removed'; s.execute(delete(Original).where(Original.id==removed['id']))
            self._schedule(k)
        if action=='download':
            d=document(k); blob=s.get(Original,d['id'])
            if blob is None: raise KeyError('Original not found')
            return {'filename':d['filename'],'content_b64':base64.b64encode(blob.data).decode()}
        if action=='cancel':
            for j in k['jobs']:
                if j['status'] in {'queued','processing'}: self._retire(k,j,'cancelled')
            k.update(pending_version=None,status='ready' if k['active_version'] else ('failed' if k['documents'] else 'empty'))
            active=next((v for v in k['versions'] if v['version']==k['active_version']),{'documents':[]})
            for d in k['documents']:
                if d['status']!='removed': d.update(status='ready' if any(x['id']==d['id'] for x in active['documents']) else 'failed',error='' if any(x['id']==d['id'] for x in active['documents']) else 'Build cancelled')
        if action=='delete':
            k.update(status='deleted',pending_version=None,updated_at=now)
            for j in k['jobs']: self._retire(k,j,'cancelled')
            for d in k['documents']: d['status']='removed'
            s.execute(delete(Original).where(Original.kb_id==k['id']))
            return {'status':'deleted'}
        if action in {'resolve','verify'}:
            version=k['active_version'] if action=='resolve' else p.get('version')
            v=next((v for v in k['versions'] if v['version']==version and v['status']=='active'),None)
            if not v: raise ValueError('No searchable version')
            live_docs={d['id']:d for d in k['documents'] if d['status']!='removed'}
            docs=[d for d in v['documents'] if d['id'] in live_docs and d['content_hash']==live_docs[d['id']]['content_hash']]
            if action=='verify':
                if any(d not in {x['id'] for x in docs} for d in p.get('document_ids',[])): raise KeyError('Source document unavailable')
                return {'valid':True}
            return {'kb_id':k['id'],'version':version,'segment_id':v['segment_id'],'config':v['config'],'connection':self._connection(v),'documents':docs,'status':k['status']}
        k['updated_at']=now
        return self._public(k,True)
