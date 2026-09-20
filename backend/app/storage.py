"""Tenant-scoped relational storage and a leased, fenced execution queue."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import time
import uuid
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, Column, String, Text, JSON, Float, Boolean, select, update, or_, and_, inspect, text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.exc import IntegrityError

class Base(DeclarativeBase):pass

class WorkflowRecord(Base):
    __tablename__='workflows'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,default='local',index=True)
    document=Column(JSON,nullable=False)
    updated_at=Column(String(64),nullable=False)

class RunRecord(Base):
    __tablename__='runs'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,default='local',index=True)
    data=Column(JSON,nullable=False)

class CredentialRecord(Base):
    __tablename__='credentials'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,default='local',index=True)
    name=Column(String(120),nullable=False)
    provider=Column(String(24),nullable=False,default='openai')
    encrypted=Column(Text,nullable=False)

class ModelRecord(Base):
    __tablename__='allowed_models'
    __table_args__=(UniqueConstraint('tenant_id','provider','model','credential_id',name='uq_workspace_model_credential'),)
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,default='local',index=True)
    provider=Column(String(24),nullable=False)
    model=Column(String(100),nullable=False)
    credential_id=Column(String(128),nullable=False,default='')
    enabled=Column(Boolean,nullable=False,default=True)

class JobRecord(Base):
    __tablename__='execution_jobs'
    run_id=Column(String(64),primary_key=True)
    status=Column(String(24),nullable=False,default='queued',index=True)
    owner=Column(String(64),nullable=False,default='')
    lease_until=Column(Float,nullable=False,default=0)
    cancel_requested=Column(Boolean,nullable=False,default=False)
    created=Column(Float,nullable=False,default=time.time)


def now():return datetime.now(timezone.utc).isoformat()
def new_id():return uuid.uuid4().hex

class WorkflowConflict(Exception):
    def __init__(self,current):
        super().__init__('This workflow changed elsewhere. Reload the server version before saving.')
        self.current=current

def local_key(data_dir:Path):
    env_key=os.environ.get('CREDENTIAL_ENCRYPTION_KEY')
    if env_key:return env_key.encode()
    file=data_dir/'credential.key'
    try:fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:return file.read_bytes()
    with os.fdopen(fd,'wb') as stream:
        key=Fernet.generate_key();stream.write(key)
    return key

class Store:
    def __init__(self,database_url,encryption_key):
        self.engine=create_engine(database_url,connect_args={'check_same_thread':False,'timeout':30} if database_url.startswith('sqlite') else {})
        self.cipher=Fernet(encryption_key)
        from . import tool_service, agent_memory  # Register feature tables before schema creation.
        # Additive v1 -> v2 migration. Existing records stay in local until account setup.
        with self.engine.begin() as conn:
            if conn.dialect.name=='postgresql':conn.execute(text('SELECT pg_advisory_xact_lock(19482026)'))
            tables=inspect(conn).get_table_names()
            for table in ('workflows','runs','credentials'):
                if table in tables and 'tenant_id' not in {c['name'] for c in inspect(conn).get_columns(table)}:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN tenant_id VARCHAR(64) NOT NULL DEFAULT 'local'"))
            if 'credentials' in tables and 'provider' not in {c['name'] for c in inspect(conn).get_columns('credentials')}:
                conn.execute(text("ALTER TABLE credentials ADD COLUMN provider VARCHAR(24) NOT NULL DEFAULT 'openai'"))
            Base.metadata.create_all(conn)
        # Old in-flight runs did not have jobs; make them recoverable without losing history.
        with Session(self.engine) as session:
            for row in session.scalars(select(RunRecord)):
                if row.data['status'] in ('queued','running') and session.get(JobRecord,row.id) is None:
                    session.add(JobRecord(run_id=row.id,status='queued'))
                    row.data={**row.data,'status':'queued','checkpoints':row.data.get('checkpoints',{})}
            session.commit()

    def save_workflow(self,document,id=None,tenant_id='local',expected_updated_at=None):
        with Session(self.engine) as s:
            if id:
                stamp=now()
                if expected_updated_at and stamp<=expected_updated_at:
                    try:stamp=(datetime.fromisoformat(expected_updated_at)+timedelta(microseconds=1)).isoformat()
                    except (ValueError,OverflowError):pass  # An invalid token cannot match a stored timestamp.
                result=s.execute(update(WorkflowRecord).where(WorkflowRecord.id==id,WorkflowRecord.tenant_id==tenant_id,WorkflowRecord.updated_at==expected_updated_at).values(document=document,updated_at=stamp))
                if result.rowcount!=1:
                    s.rollback()
                    current=self.workflow(id,tenant_id)
                    raise WorkflowConflict(current)
                s.commit()
                return {'id':id,'workflow':document,'updated_at':stamp}
            row=WorkflowRecord(id=new_id(),tenant_id=tenant_id);s.add(row)
            row.document,row.updated_at=document,now();s.commit()
            return {'id':row.id,'workflow':row.document,'updated_at':row.updated_at}

    def workflows(self,tenant_id='local'):
        with Session(self.engine) as s:
            return [{'id':r.id,'name':r.document['name'],'updated_at':r.updated_at} for r in s.scalars(select(WorkflowRecord).where(WorkflowRecord.tenant_id==tenant_id).order_by(WorkflowRecord.updated_at.desc()))]

    def workflow(self,id,tenant_id='local'):
        with Session(self.engine) as s:
            row=s.get(WorkflowRecord,id)
            if not row or row.tenant_id!=tenant_id:raise KeyError(id)
            return {'id':row.id,'workflow':row.document,'updated_at':row.updated_at}

    def credential(self,name,secret,tenant_id='local',provider='openai'):
        with Session(self.engine) as s:
            row=CredentialRecord(id=new_id(),tenant_id=tenant_id,name=name,provider=provider,encrypted=self.cipher.encrypt(secret.encode()).decode())
            s.add(row);s.commit();return {'id':row.id,'name':row.name,'provider':row.provider}

    def credentials(self,tenant_id='local'):
        with Session(self.engine) as s:
            return [{'id':r.id,'name':r.name,'provider':r.provider} for r in s.scalars(select(CredentialRecord).where(CredentialRecord.tenant_id==tenant_id))]

    @staticmethod
    def model_public(row):
        return {key:getattr(row,key) for key in ('id','provider','model','credential_id','enabled')}

    def models(self,tenant_id='local'):
        with Session(self.engine) as s:
            return [self.model_public(row) for row in s.scalars(select(ModelRecord).where(ModelRecord.tenant_id==tenant_id))]

    @staticmethod
    def check_model_credential(s,provider,credential_id,tenant_id):
        if provider=='ollama':
            if credential_id:raise ValueError('Ollama does not use an API credential.')
            return
        row=s.get(CredentialRecord,credential_id)
        if not row or row.tenant_id!=tenant_id or row.provider!=provider:
            raise ValueError('Choose a credential belonging to this workspace and provider.')

    def allow_model(self,provider,model,credential_id='',tenant_id='local'):
        with Session(self.engine) as s:
            self.check_model_credential(s,provider,credential_id,tenant_id)
            row=s.scalar(select(ModelRecord).where(ModelRecord.tenant_id==tenant_id,ModelRecord.provider==provider,ModelRecord.model==model,ModelRecord.credential_id==credential_id))
            if not row:
                row=ModelRecord(id=new_id(),tenant_id=tenant_id,provider=provider,model=model,credential_id=credential_id);s.add(row)
            row.enabled=True
            try:s.commit()
            except IntegrityError:
                # Another API process enabled this exact permission concurrently.
                s.rollback()
                row=s.scalar(select(ModelRecord).where(ModelRecord.tenant_id==tenant_id,ModelRecord.provider==provider,ModelRecord.model==model,ModelRecord.credential_id==credential_id))
                if row is None:raise
                row.enabled=True;s.commit()
            return self.model_public(row)

    def set_model_enabled(self,id,enabled,tenant_id='local'):
        with Session(self.engine) as s:
            row=s.get(ModelRecord,id)
            if not row or row.tenant_id!=tenant_id:raise KeyError(id)
            if enabled:self.check_model_credential(s,row.provider,row.credential_id,tenant_id)
            row.enabled=enabled;s.commit();return self.model_public(row)

    def authorize_model(self,config,tenant_id='local'):
        if config.provider=='demo':return
        with Session(self.engine) as s:
            row=s.scalar(select(ModelRecord).where(ModelRecord.tenant_id==tenant_id,ModelRecord.provider==config.provider,ModelRecord.model==config.model,ModelRecord.credential_id==config.credential_id,ModelRecord.enabled.is_(True)))
            if not row:raise ValueError('Model is not enabled for this workspace. Configure it in the left Providers panel and select it in the node.')
            self.check_model_credential(s,config.provider,config.credential_id,tenant_id)

    def model_errors(self,workflow,tenant_id='local'):
        from .registry import LLMConfig
        errors=[]
        for node in workflow.nodes:
            if node.type not in ('llm','grounded_answer','agent','query'):continue
            try:
                from .registry import REGISTRY
                config=REGISTRY[node.type].config_model.model_validate(node.config)
            except ValueError:continue # Graph validation reports malformed configuration.
            try:self.authorize_model(config,tenant_id)
            except ValueError as exc:errors.append(f'{node.id}: {exc}')
        return errors

    def run_tenant(self,id,owner):
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):raise ValueError('Execution lease is no longer owned by this worker.')
            return s.get(RunRecord,id).tenant_id

    def authorize_run_model(self,id,owner,config):
        self.authorize_model(config,self.run_tenant(id,owner))

    def resolve_credential(self,id,tenant_id='local'):
        with Session(self.engine) as s:
            row=s.get(CredentialRecord,id)
            if not row or row.tenant_id!=tenant_id:raise ValueError('Credential not found. Choose an existing credential.')
            try:return self.cipher.decrypt(row.encrypted.encode()).decode()
            except InvalidToken:raise ValueError('Credential cannot be decrypted. Restore the original encryption key.') from None

    def resolve_run_credential(self,id,owner,credential_id):
        with Session(self.engine) as session:
            if not self._fence(session,id,owner):raise ValueError('Execution lease is no longer owned by this worker.')
            # Read both current tenant IDs in one SQL statement: first-account migration
            # may move a running local workflow and its credentials atomically.
            encrypted=session.scalar(select(CredentialRecord.encrypted).join(RunRecord,RunRecord.tenant_id==CredentialRecord.tenant_id).where(RunRecord.id==id,CredentialRecord.id==credential_id))
            if not encrypted:raise ValueError('Credential not found. Choose an existing credential.')
            try:return self.cipher.decrypt(encrypted.encode()).decode()
            except InvalidToken:raise ValueError('Credential cannot be decrypted. Restore the original encryption key.') from None

    def create_run(self,workflow,message,tenant_id='local'):
        data={'id':new_id(),'workflow':workflow,'message':message,'status':'queued','created_at':now(),'events':[],'output':'','error':'','checkpoints':{}}
        with Session(self.engine) as s:
            s.add(RunRecord(id=data['id'],tenant_id=tenant_id,data=data));s.add(JobRecord(run_id=data['id'],status='queued'));s.commit()
        return data

    def run(self,id,tenant_id='local'):
        with Session(self.engine) as s:
            row=s.get(RunRecord,id)
            if not row or row.tenant_id!=tenant_id:raise KeyError(id)
            return row.data

    def runs(self,tenant_id='local'):
        with Session(self.engine) as s:
            rows=s.scalars(select(RunRecord).where(RunRecord.tenant_id==tenant_id))
            data=[{k:v for k,v in r.data.items() if k not in ('workflow','message','events','checkpoints','vector_dependencies')}|{'name':r.data['workflow']['name']} for r in rows]
            return sorted(data,key=lambda r:r['created_at'],reverse=True)[:100]

    @staticmethod
    def _event(data,event):
        events=data['events'];return {**data,'events':[*events,{**event,'seq':len(events),'timestamp':now()}]}

    def claim_next(self,owner,lease_seconds=30):
        timestamp=time.time()
        eligible=or_(JobRecord.status=='queued',and_(JobRecord.status=='running',JobRecord.lease_until<timestamp))
        with Session(self.engine) as s:
            candidates=s.scalars(select(JobRecord.run_id).where(eligible).order_by(JobRecord.created).limit(20)).all()
            for id in candidates:
                changed=s.execute(update(JobRecord).where(JobRecord.run_id==id,eligible).values(status='running',owner=owner,lease_until=timestamp+lease_seconds))
                if not changed.rowcount:continue
                row=s.get(RunRecord,id)
                row.data=self._event({**row.data,'status':'running'}, {'kind':'run','status':'running','worker':owner})
                s.commit();return {**row.data,'tenant_id':row.tenant_id}
        return None

    def _fence(self,s,id,owner):
        # This UPDATE locks the job row and checks lease ownership in the same transaction.
        result=s.execute(update(JobRecord).where(JobRecord.run_id==id,JobRecord.owner==owner,JobRecord.status=='running',JobRecord.lease_until>time.time()).values(owner=owner))
        return bool(result.rowcount)

    def heartbeat(self,id,owner,lease_seconds=30):
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):return False
            job=s.get(JobRecord,id);job.lease_until=time.time()+lease_seconds;s.commit();return True

    def cancel_requested(self,id,owner):
        with Session(self.engine) as s:
            job=s.get(JobRecord,id)
            return not job or job.owner!=owner or job.status!='running' or job.cancel_requested

    def worker_event(self,id,owner,event):
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):return False
            row=s.get(RunRecord,id);data=self._event(row.data,event)
            if event.get('node_id') and event['status']=='running' and not event.get('transient'):
                dependencies=dict(data.get('vector_dependencies',{}))
                dependencies.pop(event['node_id'],None)
                data={**data,'vector_dependencies':dependencies}
            if event.get('node_id') and event['status']=='success' and 'outputs' in event and not event.get('transient'):
                data={**data,'checkpoints':{**data.get('checkpoints',{}),event['node_id']:event['outputs']}}
            row.data=data;s.commit();return True

    def record_vector_dependencies(self,id,owner,node_id,sources):
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):raise ValueError('Execution lease is no longer owned.')
            row=s.get(RunRecord,id)
            dependencies=dict(row.data.get('vector_dependencies',{}))
            merged={source['id']:source for source in dependencies.get(node_id,[])}
            merged.update({source['id']:source for source in sources})
            if len(merged)>120:raise ValueError('Agent evidence dependency limit exceeded.')
            dependencies[node_id]=list(merged.values())
            row.data={**row.data,'vector_dependencies':dependencies};s.commit()

    def finish_run(self,id,owner,status,**updates):
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):return False
            job=s.get(JobRecord,id)
            if job.cancel_requested:status='cancelled';updates.pop('output',None)
            row=s.get(RunRecord,id)
            row.data=self._event({**row.data,**updates,'status':status,'finished_at':now()},{'kind':'run','status':status})
            job.status=status;job.lease_until=0;s.commit();return True

    def release(self,id,owner):
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):return
            job=s.get(JobRecord,id);row=s.get(RunRecord,id)
            job.status='queued';job.lease_until=0;job.owner=''
            row.data={**row.data,'status':'queued'};s.commit()

    def request_cancel(self,id,tenant_id='local'):
        with Session(self.engine) as s:
            # Lock job first, same lock order as workers.
            s.execute(update(JobRecord).where(JobRecord.run_id==id).values(run_id=id))
            job=s.get(JobRecord,id)
            row=s.get(RunRecord,id)
            if not row or row.tenant_id!=tenant_id:raise KeyError(id)
            if not job or job.status not in ('queued','running'):return row.data['status']
            job.cancel_requested=True
            if job.status=='queued':
                job.status='cancelled';row.data=self._event({**row.data,'status':'cancelled','finished_at':now()},{'kind':'run','status':'cancelled'})
            s.commit();return row.data['status']

    def mark_write(self,id,owner,node_id):
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):raise ValueError('Execution lease is no longer owned.')
            row=s.get(RunRecord,id)
            row.data={**row.data,'write_nodes':list(set(row.data.get('write_nodes',[]))|{node_id})};s.commit()

    @staticmethod
    def check_resume_writes(data):
        if any(id not in data.get('checkpoints',{}) for id in data.get('write_nodes',[])):
            raise ValueError('This run may have performed an external write before its step completed. Check the external result, then start a new run instead of resuming.')

    def resume_run(self,id,tenant_id='local'):
        with Session(self.engine) as s:
            s.execute(update(JobRecord).where(JobRecord.run_id==id).values(run_id=id))
            job=s.get(JobRecord,id)
            row=s.get(RunRecord,id)
            if not row or row.tenant_id!=tenant_id:raise KeyError(id)
            if row.data['status'] not in ('failed','cancelled'):raise ValueError('Only failed or cancelled runs can be resumed.')
            self.check_resume_writes(row.data)
            if not job:job=JobRecord(run_id=id);s.add(job)
            job.status='queued';job.owner='';job.lease_until=0;job.cancel_requested=False
            row.data=self._event({**row.data,'status':'queued','error':'','output':'','finished_at':None},{'kind':'run','status':'queued','resumed':True})
            s.commit();return row.data
