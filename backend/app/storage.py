"""Tenant-scoped relational storage and a leased, fenced execution queue."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import base64
import json
import time
import uuid
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, Column, String, Text, JSON, Float, Boolean, select, update, or_, and_, inspect, text, UniqueConstraint, Integer, Index
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
    __table_args__=(Index('ix_runs_tenant_created_id','tenant_id','created_at','id'),)
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,default='local',index=True)
    data=Column(JSON,nullable=False)
    created_at=Column(String(64),nullable=False,default=lambda:now(),index=True)
    status=Column(String(24),nullable=False,default='queued',index=True)
    name=Column(String(240),nullable=False,default='')
    citation_counter=Column(Integer,nullable=False,default=0)
    duration_seconds=Column(Float,nullable=True)
    grounded=Column(Boolean,nullable=False,default=False)
    abstained=Column(Boolean,nullable=False,default=False)
    truncated=Column(Boolean,nullable=False,default=False)
    # 'confirmed', 'legacy_checkpoint_unverified' or '' when not truncated.
    truncation_source=Column(String(40),nullable=False,default='',server_default='')
    # Run-wide spend, reserved before every action. Kept out of data so a reservation
    # updates this small value instead of rewriting the whole document and every
    # checkpoint in it. None for runs whose accounting still lives in data.
    accounting=Column(JSON,nullable=True)

class RunEventRecord(Base):
    __tablename__='run_events'
    run_id=Column(String(64),primary_key=True)
    seq=Column(Integer,primary_key=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    timestamp=Column(String(64),nullable=False)
    type=Column(String(64),nullable=False)
    payload=Column(JSON,nullable=False)

class RunLoopItemRecord(Base):
    """One loop item's durable result, keyed by position.

    Items are written as rows rather than into the run's JSON document, which every event
    rewrites in full: a batch then costs one insert per item instead of rewriting all
    earlier progress each time. The row and its loop_item event commit in one fenced
    transaction. Runs checkpointed before this table still have progress in the run
    document; readers merge both, and a row takes precedence for the same index.
    """
    __tablename__='run_loop_items'
    run_id=Column(String(64),primary_key=True)
    node_id=Column(String(64),primary_key=True)
    item_index=Column(Integer,primary_key=True)
    entry=Column(JSON,nullable=False)

class SchemaMigrationRecord(Base):
    __tablename__='schema_migrations'
    version=Column(String(80),primary_key=True)

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

class LeaseLost(RuntimeError):
    """This worker no longer owns the run. Nothing may be spent or sent on its behalf.

    Deliberately not a ValueError: it must escape retry and recovery the way cancellation
    does, rather than becoming a node failure that on_error could route past.
    """


class ActionResultRecord(Base):
    """A completed external write, keyed by the invocation that performed it.

    Clearing a write marker and recording its outcome must be one transaction. Without
    that, a worker dying between them leaves resume permitted with nothing to show the
    call already happened, and the write is sent twice.
    """
    __tablename__='run_action_results'
    run_id=Column(String(64),primary_key=True)
    invocation=Column(String(300),primary_key=True)
    result=Column(Text,nullable=False)
    created_at=Column(String(64),nullable=False)

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

# Fields derived from events into their own records (token usage rows, guard decision
# rows) stay exact at their normal size. Only abnormally large ones are bounded, and then
# their structure is kept - those records read them as dicts.
EXACT_EVENT_FIELDS=frozenset({'usage','answerability','abstention_source'})
EXACT_FIELD_BYTES=4096
# Who and where an event is about; small by construction and never shortened.
IDENTITY_EVENT_FIELDS=frozenset({'kind','node_id','status','loop_node_id','item_index','invocation_id','parent_node_id',
                                 'transient','attempt','recovered','budget_exhausted','cached','seq','timestamp'})
MAX_PREVIEW_PATHS=20
# The absolute ceiling for a stored loop event, whatever its fields hold.
MAX_EVENT_BYTES=32_768
EVENT_FIELD_OMITTED='[omitted: event size ceiling]'


def observed(event):
    """The copy of an event kept for observability, with loop work bounded.

    Inside a loop an item's work is repeated for every item: the body, and every agent
    step, tool call, specialist, retry and failure beneath it. Each such event carries the
    loop's identity (loop_node_id, item_index), and is stored bounded:

    * large fields become previews of PREVIEW_CHARS, a field of many medium values is
      collapsed as a whole, and preview_of names up to MAX_PREVIEW_PATHS shortened paths;
    * exact fields stay exact at normal size; abnormally large ones keep their structure
      with their contents shortened, since other records read them as dicts;
    * a final ceiling, MAX_EVENT_BYTES, replaces the largest remaining fields - exact ones
      last, and removed rather than replaced - until the event fits. Identity fields are
      never touched.

    The durable copies resume and settlement read - item rows, checkpoints, approvals and
    write results - are recorded elsewhere and never shortened.
    """
    from .iteration import PREVIEW_CHARS
    if not (event.get('loop_node_id') or event.get('kind')=='loop_item'):return event
    cut=[]
    def text_of(value):
        return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,default=str)
    def size(value):return len(json.dumps(value,ensure_ascii=False,default=str).encode('utf-8'))
    def bound(value,path):
        if isinstance(value,dict):
            shortened={key:bound(item,f'{path}.{key}') for key,item in value.items()}
            if len(text_of(shortened))<=4*PREVIEW_CHARS:return shortened
            cut.append(path);return text_of(shortened)[:PREVIEW_CHARS]
        text=text_of(value)
        if len(text)<=PREVIEW_CHARS:return value
        cut.append(path);return text[:PREVIEW_CHARS]
    def bound_contents(value,path,depth=0):
        # Shorten what is inside without changing what kind of value it is.
        if isinstance(value,dict):
            items=list(value.items())
            if len(items)>50:cut.append(path);items=items[:50]
            return {key:bound_contents(item,f'{path}.{key}',depth+1) for key,item in items}
        if isinstance(value,list):
            if len(value)>20:cut.append(path);value=value[:20]
            return [bound_contents(item,f'{path}[{i}]',depth+1) for i,item in enumerate(value)]
        if isinstance(value,str) and len(value)>PREVIEW_CHARS:cut.append(path);return value[:PREVIEW_CHARS]
        return value
    stored={}
    for key,value in event.items():
        if key in IDENTITY_EVENT_FIELDS:stored[key]=value
        elif key in EXACT_EVENT_FIELDS:stored[key]=value if size(value)<=EXACT_FIELD_BYTES else bound_contents(value,key)
        else:stored[key]=bound(value,key)
    # Attach all observability metadata first, so the ceiling judges the event as stored.
    if cut:
        stored['preview_of']=[path[:200] for path in cut[:MAX_PREVIEW_PATHS]]
        if len(cut)>MAX_PREVIEW_PATHS:stored['preview_omitted']=len(cut)-MAX_PREVIEW_PATHS
    if size(stored)>MAX_EVENT_BYTES:
        omitted=[]
        def fits():return size({**stored,'omitted_fields':omitted})<=MAX_EVENT_BYTES
        ordinary=sorted((key for key in stored if key not in IDENTITY_EVENT_FIELDS|EXACT_EVENT_FIELDS
                         and key not in ('preview_of','preview_omitted')),key=lambda key:-size(stored[key]))
        exact=sorted((key for key in stored if key in EXACT_EVENT_FIELDS),key=lambda key:-size(stored[key]))
        # Ordinary fields go first, then the list of preview paths (kept as a count), and
        # exact fields last - removed rather than retyped, as other records read them as dicts.
        for key in [*ordinary,'preview_of',*exact]:
            if fits():break
            if key=='preview_of':
                if 'preview_of' in stored:stored['preview_omitted']=len(cut);del stored['preview_of']
                continue
            if key in EXACT_EVENT_FIELDS:del stored[key]
            else:stored[key]=EVENT_FIELD_OMITTED
            omitted.append(key)
        stored['omitted_fields']=[str(key)[:200] for key in omitted]
        if size(stored)>MAX_EVENT_BYTES:
            # Unreachable while identity fields stay small, as node ids and invocations do.
            stored={key:value for key,value in stored.items() if key in IDENTITY_EVENT_FIELDS}
            stored['omitted_fields']=['*']
    if size(stored)>MAX_EVENT_BYTES:
        raise ValueError('A loop event exceeds the stored event ceiling even with only its identity.')
    return event if stored==event else stored


LOOP_RESULTS_STORE='run_loop_items'


def results_digest(results):
    """A canonical hash of loop results, so a compact checkpoint can prove its rows."""
    import hashlib
    return hashlib.sha256(json.dumps(results,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


class Store:
    def __init__(self,database_url,encryption_key,auto_upgrade=True):
        self.engine=create_engine(database_url,connect_args={'check_same_thread':False,'timeout':30} if database_url.startswith('sqlite') else {})
        self.cipher=Fernet(encryption_key)
        from . import readiness, operator_metrics, approvals
        from . import tool_service, agent_memory  # Register feature tables before schema creation.
        from . import schema
        from .observability import journal
        started=time.perf_counter()
        journal(event='storage.backfill.start',scope='startup')
        try:
            with self.engine.begin() as conn:
                if conn.dialect.name=='sqlite':conn.execute(text('BEGIN IMMEDIATE'))
                if conn.dialect.name=='postgresql':conn.execute(text('SELECT pg_advisory_xact_lock(19482026)'))
                # Pre-adoption only: bring a database written before Alembic up to the
                # baseline shape so it can be verified and stamped. Skipped once
                # alembic_version exists, which is every run after the first.
                if schema.current_revision(conn) is None and not schema.is_empty(conn):
                    self._legacy_columns(conn)
                action=schema.prepare(conn,allow_upgrade=auto_upgrade)
                kept=schema.retired_tables(conn)
                # Data transformations keep their own schema_migrations records; Alembic
                # owns schema state only. The two answer different questions.
                self._migrate_run_events(conn)
                operator_metrics.migrate(conn)
            journal(event='storage.schema',scope='startup',status=action,reason_code=schema.head_revision())
            if kept:journal(event='storage.retired_tables_kept',scope='startup',reason_code=','.join(kept))
            # Old in-flight runs did not have jobs; make them recoverable without losing history.
            with Session(self.engine) as session:
                for row in session.scalars(select(RunRecord).where(RunRecord.status.in_(('queued','running')),~select(JobRecord.run_id).where(JobRecord.run_id==RunRecord.id).exists())):
                    if session.get(JobRecord,row.id) is None:
                        session.add(JobRecord(run_id=row.id,status='queued'))
                        row.data={**row.data,'status':'queued','checkpoints':row.data.get('checkpoints',{})};row.status='queued'
                session.commit()
        except Exception as exc:
            journal(event='storage.backfill.finish',scope='startup',status='failed',seconds=round(time.perf_counter()-started,3),error_type=type(exc).__name__)
            raise
        journal(event='storage.backfill.finish',scope='startup',status='success',seconds=round(time.perf_counter()-started,3))

    @staticmethod
    def _legacy_columns(conn):
        """Bring a pre-Alembic database up to the baseline shape so it can be adopted.

        This was the old startup migration. It runs once, only when no alembic_version
        exists, and only so structural verification has something to match. Every schema
        change after the baseline belongs in a revision instead.
        """
        tables=inspect(conn).get_table_names()
        for table in ('workflows','runs','credentials'):
            if table in tables and 'tenant_id' not in {c['name'] for c in inspect(conn).get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN tenant_id VARCHAR(64) NOT NULL DEFAULT 'local'"))
        if 'credentials' in tables and 'provider' not in {c['name'] for c in inspect(conn).get_columns('credentials')}:
            conn.execute(text("ALTER TABLE credentials ADD COLUMN provider VARCHAR(24) NOT NULL DEFAULT 'openai'"))
        if 'runs' in tables:
            columns={c['name'] for c in inspect(conn).get_columns('runs')}
            if 'citation_counter' not in columns:conn.execute(text('ALTER TABLE runs ADD COLUMN citation_counter INTEGER NOT NULL DEFAULT 0'))
            for name,definition in (('duration_seconds','FLOAT'),('grounded','BOOLEAN NOT NULL DEFAULT FALSE'),('abstained','BOOLEAN NOT NULL DEFAULT FALSE'),('truncated','BOOLEAN NOT NULL DEFAULT FALSE')):
                if name not in columns:conn.execute(text(f'ALTER TABLE runs ADD COLUMN {name} {definition}'))
            for name,size in (('created_at',64),('status',24),('name',240)):
                if name not in columns:conn.execute(text(f"ALTER TABLE runs ADD COLUMN {name} VARCHAR({size}) NOT NULL DEFAULT ''"))
        # Create only what the frozen 0001 baseline declares. Using live metadata here
        # would build tables at a later revision's shape and then stamp them as 0001,
        # so the migration that introduced them would collide on the next upgrade.
        from .schema import baseline_metadata
        baseline=baseline_metadata()
        baseline.create_all(conn)
        # Indexes are additive and lossless, so an older database that predates one gets
        # it here. Unique constraints are deliberately excluded: adding one to data that
        # already violates it is a decision for an operator, not for startup.
        inspector=inspect(conn)
        for table in baseline.tables.values():
            if table.name not in inspector.get_table_names():continue
            existing={index['name'] for index in inspector.get_indexes(table.name)}
            for index in table.indexes:
                if index.name not in existing and not index.unique:index.create(conn)

    @staticmethod
    def _migrate_run_events(conn):
        version='20260919_run_event_rows'
        if conn.scalar(select(SchemaMigrationRecord.version).where(SchemaMigrationRecord.version==version)):return
        for row in conn.execute(select(RunRecord.__table__)).mappings().yield_per(100):
            from .evidence_registry import source_high_water
            data=dict(row['data']);events=data.pop('events',[])
            citation_counter=max([row['citation_counter']]+[source_high_water(o) for o in data.get('checkpoints',{}).values()])
            if not isinstance(events,list):raise ValueError('Run event migration requires an event array; back up and repair run '+row['id'])
            created=data.get('created_at') or '1970-01-01T00:00:00+00:00'
            for seq,event in enumerate(events):
                if not isinstance(event,dict) or event.get('seq',seq)!=seq:
                    raise ValueError('Run event migration found invalid event ordering in '+row['id'])
                citation_counter=max(citation_counter,source_high_water(event.get('outputs',{})))
                stamp=event.get('timestamp',created)
                payload={k:v for k,v in event.items() if k not in ('seq','timestamp')}
                values=dict(run_id=row['id'],seq=seq,tenant_id=row['tenant_id'],timestamp=stamp,type=event.get('kind','node'),payload=payload)
                existing=conn.execute(select(RunEventRecord.__table__).where(RunEventRecord.run_id==row['id'],RunEventRecord.seq==seq)).mappings().first()
                if existing and dict(existing)!=values:raise ValueError('Run event migration conflicts with existing event '+row['id'])
                if not existing:conn.execute(RunEventRecord.__table__.insert().values(**values))
            conn.execute(update(RunRecord).where(RunRecord.id==row['id']).values(data=data,created_at=created,status=data.get('status','queued'),name=data.get('workflow',{}).get('name',''),citation_counter=citation_counter))
        conn.execute(SchemaMigrationRecord.__table__.insert().values(version=version))

    def worker_seen(self,identifier):
        from .readiness import worker_seen
        worker_seen(self,identifier)

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

    def create_run(self,workflow,message,tenant_id='local',request_id=None,approval_required=False):
        data={'approval_required':approval_required,'request_id':request_id,'id':new_id(),'workflow':workflow,'message':message,'status':'queued','created_at':now(),'output':'','error':'','checkpoints':{}}
        with Session(self.engine) as s:
            s.add(RunRecord(id=data['id'],tenant_id=tenant_id,data=data,created_at=data['created_at'],status='queued',name=workflow['name']))
            s.add(JobRecord(run_id=data['id'],status='queued'));s.commit()
        return {**data,'events':[]}

    @staticmethod
    def _events(s,id,tenant_id,after=-1,limit=None):
        query=select(RunEventRecord).where(RunEventRecord.run_id==id,RunEventRecord.tenant_id==tenant_id,RunEventRecord.seq>after).order_by(RunEventRecord.seq)
        if limit is not None:query=query.limit(limit)
        return [{**r.payload,'seq':r.seq,'timestamp':r.timestamp} for r in s.scalars(query)]

    def run(self,id,tenant_id='local'):
        with Session(self.engine) as s:
            row=s.get(RunRecord,id)
            if not row or row.tenant_id!=tenant_id:raise KeyError(id)
            from .approvals import listing
            approvals=listing(s,id,tenant_id)
            return {**self._document(s,row),'events':self._events(s,id,tenant_id),**({'approvals':approvals} if approvals else {})}

    def run_status(self,id,tenant_id='local'):
        with Session(self.engine) as s:
            status=s.scalar(select(RunRecord.status).where(RunRecord.id==id,RunRecord.tenant_id==tenant_id))
            if status is None:raise KeyError(id)
            return status

    def run_events(self,id,tenant_id='local',after=-1,limit=200):
        if type(after) is not int or after < -1 or type(limit) is not int or not 1<=limit<=1000:raise ValueError('Invalid event cursor or limit')
        self.run_status(id,tenant_id)
        with Session(self.engine) as s:return self._events(s,id,tenant_id,after,limit)

    def runs(self,tenant_id='local'):
        return self.runs_page(tenant_id)['items']

    def runs_page(self,tenant_id='local',cursor=None,limit=100):
        if type(limit) is not int or not 1<=limit<=100:raise ValueError('History page size must be 1–100')
        query=select(RunRecord.id,RunRecord.created_at,RunRecord.status,RunRecord.name,
            RunRecord.data['output'].as_string().label('output'),RunRecord.data['error'].as_string().label('error'),
            RunRecord.data['finished_at'].as_string().label('finished_at'),RunRecord.data['truncated'].as_boolean().label('truncated')).where(RunRecord.tenant_id==tenant_id)
        if cursor:
            try:
                if not isinstance(cursor,str) or len(cursor)>512:raise ValueError()
                value=json.loads(base64.b64decode(cursor+'='*(-len(cursor)%4),altchars=b'-_',validate=True))
                if not isinstance(value,dict) or set(value)!={'tenant','created','id'} or value['tenant']!=tenant_id:raise ValueError()
                if any(not isinstance(value[k],str) or not 1<=len(value[k])<=64 for k in ('created','id')):raise ValueError()
            except (ValueError,TypeError,KeyError):raise ValueError('Invalid history cursor') from None
            query=query.where(or_(RunRecord.created_at<value['created'],and_(RunRecord.created_at==value['created'],RunRecord.id<value['id'])))
        with Session(self.engine) as s:
            rows=s.execute(query.order_by(RunRecord.created_at.desc(),RunRecord.id.desc()).limit(limit+1)).mappings().all()
        items=[dict(row)|{'output':row['output'] or '', 'error':row['error'] or '', 'truncated':bool(row['truncated'])} for row in rows[:limit]]
        next_cursor=None
        if len(rows)>limit:
            last=items[-1];value={'tenant':tenant_id,'created':last['created_at'],'id':last['id']}
            next_cursor=base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).decode().rstrip('=')
        return {'items':items,'next_cursor':next_cursor}

    @staticmethod
    def _event(s,row,event):
        from .operator_metrics import GuardDecisionRecord
        decision=event.get('answerability') if event.get('kind')=='answerability' and not event.get('cached') else None
        if decision:event={**event,'answerability':decision,'abstention_source':decision.get('abstention_source')}
        # All callers hold the same job-row lock/fence. The composite PK lookup
        # allocates a sequence without loading prior payloads or rewriting state.
        last=s.scalar(select(RunEventRecord.seq).where(RunEventRecord.run_id==row.id).order_by(RunEventRecord.seq.desc()).limit(1))
        s.add(RunEventRecord(run_id=row.id,seq=0 if last is None else last+1,tenant_id=row.tenant_id,
            timestamp=now(),type=event.get('kind','node'),payload={k:v for k,v in event.items() if k not in ('seq','timestamp')}))
        if decision:
            s.add(GuardDecisionRecord(run_id=row.id,seq=0 if last is None else last+1,decision=decision['decision'],reason=decision.get('reason','')))
        from .operator_metrics import RunTokenRecord,usage_rows
        for usage in usage_rows(event,row.data.get('workflow',{})):
            s.add(RunTokenRecord(run_id=row.id,seq=0 if last is None else last+1,**usage))

    @staticmethod
    def _compact_loop(s,row,node_id,outputs):
        """A loop checkpoint that references its item rows instead of embedding results.

        Compacted only when the rows provably hold the results: indexed 0..n-1 and equal
        entry for entry. A loop resumed from document-era progress keeps its results
        embedded. The rows were each committed under the lease before this checkpoint,
        which is written in the same fenced transaction as the loop's success event.
        """
        kinds={n.get('id'):n.get('type') for n in row.data.get('workflow',{}).get('nodes',[])}
        results=outputs.get('results')
        if kinds.get(node_id)!='for_each' or not isinstance(results,list) or not results:return outputs
        if [entry.get('index') for entry in results]!=list(range(len(results))):return outputs
        stored={item.item_index:item.entry for item in s.scalars(select(RunLoopItemRecord).where(
            RunLoopItemRecord.run_id==row.id,RunLoopItemRecord.node_id==node_id))}
        # The rows must be exactly the results: the same indices, none extra, equal entries.
        if set(stored)!=set(range(len(results))) or any(stored[index]!=entry for index,entry in enumerate(results)):return outputs
        return {**outputs,'results':{'stored_in':LOOP_RESULTS_STORE,'count':len(results),'sha256':results_digest(results)}}

    @staticmethod
    def _hydrate_loop(node_id,outputs,rows):
        """The public checkpoint shape of a compact loop checkpoint, or a visible failure.

        Missing or altered rows never become an empty successful loop: the results are
        replaced by a marker whose 'unavailable' reason resume refuses to proceed past.
        """
        import re
        from .iteration import MAX_ITEMS_CEILING
        marker=outputs.get('results')
        if not (isinstance(marker,dict) and marker.get('stored_in')==LOOP_RESULTS_STORE):return outputs
        def unavailable(reason):
            return {**outputs,'results':{**marker,'unavailable':f'Loop {node_id} cannot be restored: {reason}. Start a new run.'}}
        # Validate the marker before using it: the count bounds what is read, so it is
        # never trusted to size anything until it is a real item count.
        count,digest=marker.get('count'),marker.get('sha256')
        if type(count) is not int or not 1<=count<=MAX_ITEMS_CEILING:
            return unavailable('its checkpoint marker has an invalid item count')
        if not (isinstance(digest,str) and re.fullmatch(r'[0-9a-f]{64}',digest)):
            return unavailable('its checkpoint marker has an invalid digest')
        indices={index for node,index in rows if node==node_id}
        missing=count-len(indices&set(range(count)));extra=len(indices-set(range(count)))
        if missing:return unavailable(f'{missing} of {count} stored item results are missing')
        if extra:return unavailable(f'{extra} stored item result(s) exist beyond the {count} it recorded')
        entries=[rows[(node_id,index)] for index in range(count)]
        if results_digest(entries)!=digest:return unavailable('stored item results do not match the checkpoint')
        return {**outputs,'results':entries}

    @staticmethod
    def _accounting(row):
        """Run-wide spend: the column, or the document copy for runs from before it."""
        return row.accounting if row.accounting is not None else row.data.get('accounting')

    @staticmethod
    def _document(s,row):
        """The run document with state kept outside it merged back in.

        Loop progress: legacy progress lives in the document, new progress in
        run_loop_items, and for the same item the row wins. Loop checkpoints that
        reference their rows are rehydrated. Accounting: the column wins
        over a legacy document copy. Every reader that decides what to skip, whether a
        write settled or what budget remains goes through here, so no store is consulted
        alone.
        """
        document=dict(row.data)
        progress={node:dict(entries) for node,entries in row.data.get('loop_progress',{}).items()}
        rows={}
        for item in s.scalars(select(RunLoopItemRecord).where(RunLoopItemRecord.run_id==row.id)):
            progress.setdefault(item.node_id,{})[str(item.item_index)]=item.entry
            rows[(item.node_id,item.item_index)]=item.entry
        if progress:document['loop_progress']=progress
        # Compact loop checkpoints are rehydrated to their public shape: {results, failed,
        # summary}, with results read from the rows they reference.
        checkpoints=row.data.get('checkpoints')
        if checkpoints:
            document['checkpoints']={node:Store._hydrate_loop(node,outputs,rows) if isinstance(outputs,dict) else outputs
                                     for node,outputs in checkpoints.items()}
        accounting=Store._accounting(row)
        if accounting is not None:document['accounting']=accounting
        return document

    def claim_next(self,owner,lease_seconds=30):
        timestamp=time.time()
        eligible=or_(JobRecord.status=='queued',and_(JobRecord.status=='running',JobRecord.lease_until<timestamp))
        with Session(self.engine) as s:
            candidates=s.scalars(select(JobRecord.run_id).where(eligible).order_by(JobRecord.created).limit(20)).all()
            for id in candidates:
                previous=s.get(JobRecord,id);expired=previous.status=='running'
                changed=s.execute(update(JobRecord).where(JobRecord.run_id==id,eligible).values(status='running',owner=owner,lease_until=timestamp+lease_seconds))
                if not changed.rowcount:continue
                row=s.get(RunRecord,id)
                row.data={**row.data,'status':'running'};row.status='running'
                self._event(s,row,{'kind':'run','status':'running','worker':owner})
                s.commit()
                from .observability import journal
                if expired:journal(event='job.lease_expired',run_id=id,tenant=row.tenant_id,request_id=row.data.get('request_id'))
                journal(event='job.lease_acquired',run_id=id,tenant=row.tenant_id,request_id=row.data.get('request_id'),worker_id=owner)
                return {**self._document(s,row),'tenant_id':row.tenant_id,'citation_counter':row.citation_counter}
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
            row=s.get(RunRecord,id);data=row.data
            checkpoint=None
            if event.get('node_id') and event['status']=='success' and 'outputs' in event and not event.get('transient'):
                checkpoint=self._compact_loop(s,row,event['node_id'],event['outputs'])
            stored=observed(event)
            if checkpoint is not None and checkpoint is not event['outputs']:
                # The event would otherwise hold a second full copy of the results.
                stored={**stored,'outputs':checkpoint}
            self._event(s,row,stored)
            from .evidence_registry import source_high_water
            high=source_high_water(event.get('outputs',{}))
            if high>row.citation_counter:row.citation_counter=high
            if event.get('node_id') and event['status']=='running' and not event.get('transient'):
                dependencies=dict(data.get('vector_dependencies',{}))
                dependencies.pop(event['node_id'],None)
                data={**data,'vector_dependencies':dependencies}
            if event.get('kind')=='loop_item' and event.get('node_id') and isinstance(event.get('item_result'),dict):
                # Per-item progress is durable so a resumed run skips finished items
                # instead of repeating paid work or an external write. merge makes a
                # repeated event for the same item an update, not a duplicate.
                s.merge(RunLoopItemRecord(run_id=id,node_id=event['node_id'],item_index=int(event['item_index']),entry=event['item_result']))
            if checkpoint is not None:
                data={**data,'checkpoints':{**data.get('checkpoints',{}),event['node_id']:checkpoint},'agent_frames':{k:v for k,v in data.get('agent_frames',{}).items() if v.get('checkpoint_owner')!=event['node_id']}}
            if data is not row.data:row.data=data
            s.commit();return True

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
            row.data={**row.data,**updates,'status':status,'finished_at':now()};row.status=status
            from .operator_metrics import run_values
            for key,value in run_values(row.data).items():setattr(row,key,value)
            self._event(s,row,{'kind':'run','status':status})
            job.status=status;job.lease_until=0;s.commit();return True

    def release(self,id,owner):
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):return
            job=s.get(JobRecord,id);row=s.get(RunRecord,id)
            job.status='queued';job.lease_until=0;job.owner=''
            row.data={**row.data,'status':'queued'};row.status='queued';s.commit()

    def request_cancel(self,id,tenant_id='local'):
        with Session(self.engine) as s:
            # Lock job first, same lock order as workers.
            s.execute(update(JobRecord).where(JobRecord.run_id==id).values(run_id=id))
            job=s.get(JobRecord,id)
            row=s.get(RunRecord,id)
            if not row or row.tenant_id!=tenant_id:raise KeyError(id)
            if not job or job.status not in ('queued','running','awaiting_approval'):return row.data['status']
            job.cancel_requested=True
            if job.status in ('queued','awaiting_approval'):
                from .approvals import ApprovalRecord
                changed=s.execute(update(ApprovalRecord).where(ApprovalRecord.run_id==id,ApprovalRecord.status.in_(('pending','approved'))).values(status='cancelled'))
                if changed.rowcount:row.data={**row.data,'approval_terminal':True}
                job.status='cancelled';row.status='cancelled';row.data={**row.data,'status':'cancelled','finished_at':now()}
                from .operator_metrics import run_values
                for key,value in run_values(row.data).items():setattr(row,key,value)
                self._event(s,row,{'kind':'run','status':'cancelled'})
            s.commit();return row.data['status']

    def reserve_accounting(self,id,owner,snapshot):
        """Durably reserve spend before an external call. Raises if it cannot.

        Unlike record_accounting this never degrades silently: a reservation that quietly
        does nothing would let the call run on a lease this worker no longer holds.
        """
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):
                raise LeaseLost('Execution lease is no longer owned; the action was not started.')
            row=s.get(RunRecord,id)
            row.accounting=snapshot;s.commit()

    def record_accounting(self,id,owner,snapshot):
        """Persist run-wide spend so a resumed run continues from it.

        Budgets and token totals are run-wide, so an interrupted run that came back with
        a fresh ceiling could spend it again simply by being resumed.
        """
        if not isinstance(snapshot,dict) or not snapshot:return
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):return
            row=s.get(RunRecord,id)
            if self._accounting(row)!=snapshot:
                row.accounting=snapshot;s.commit()

    def mark_write(self,id,owner,node_id):
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):raise ValueError('Execution lease is no longer owned.')
            row=s.get(RunRecord,id)
            row.data={**row.data,'write_nodes':list(set(row.data.get('write_nodes',[]))|{node_id})};s.commit()

    def completed_action(self,id,identifier):
        """The stored result of an external write that already ran, or None."""
        with Session(self.engine) as s:
            row=s.get(ActionResultRecord,(id,identifier))
            return row.result if row else None

    def settle_write(self,id,owner,identifier,result):
        """Record a completed external write and clear its marker in one transaction.

        mark_write records intent; this records the resolved outcome, so a finished write
        stops looking unresolved once its owner has not yet checkpointed. The outcome is
        required rather than optional: a marker cleared without a stored result reopens
        the replay window this record exists to close. A tool that genuinely returns
        nothing passes the empty string, which is still a recorded outcome.
        """
        if not isinstance(result,str):
            raise ValueError('A settled write must record its outcome as text.')
        with Session(self.engine) as s:
            if not self._fence(s,id,owner):raise ValueError('Execution lease is no longer owned.')
            row=s.get(RunRecord,id)
            if s.get(ActionResultRecord,(id,identifier)) is None:
                s.add(ActionResultRecord(run_id=id,invocation=identifier,result=result,created_at=now()))
            remaining=[n for n in row.data.get('write_nodes',[]) if n!=identifier]
            if len(remaining)!=len(row.data.get('write_nodes',[])):
                row.data={**row.data,'write_nodes':remaining}
            # One commit: the marker never clears without the outcome beside it.
            s.commit()

    @staticmethod
    def _write_settled(data,identifier):
        """A write is settled when its owner produced a durable result.

        For a plain node that is its checkpoint. For a loop item ('<node>:<index>') it is
        the persisted item result: the enclosing loop has no checkpoint until every item
        finishes, so requiring one would make a completed item block the whole batch.
        """
        if identifier in data.get('checkpoints',{}):return True
        node,separator,index=identifier.rpartition(':')
        if not separator:return False
        return index in data.get('loop_progress',{}).get(node,{})

    @staticmethod
    def check_resume_writes(data):
        if any(not Store._write_settled(data,id) for id in data.get('write_nodes',[])):
            raise ValueError('This run may have performed an external write before its step completed. Check the external result, then start a new run instead of resuming.')

    def resume_run(self,id,tenant_id='local'):
        with Session(self.engine) as s:
            s.execute(update(JobRecord).where(JobRecord.run_id==id).values(run_id=id))
            job=s.get(JobRecord,id)
            row=s.get(RunRecord,id)
            if not row or row.tenant_id!=tenant_id:raise KeyError(id)
            if row.data.get('approval_terminal'):raise ValueError('Rejected or expired approvals cannot be resumed. Start a new run.')
            if row.data['status'] not in ('failed','cancelled'):raise ValueError('Only failed or cancelled runs can be resumed.')
            self.check_resume_writes(self._document(s,row))
            if not job:job=JobRecord(run_id=id);s.add(job)
            job.status='queued';job.owner='';job.lease_until=0;job.cancel_requested=False
            row.data={**row.data,'status':'queued','error':'','output':'','finished_at':None,'truncated':False,'truncation_reason':'','truncation_source':'','truncation_causes':[]};row.status='queued'
            row.duration_seconds=None;row.grounded=False;row.abstained=False;row.truncated=False;row.truncation_source=''
            self._event(s,row,{'kind':'run','status':'queued','resumed':True})
            s.commit();return {**row.data,'events':self._events(s,id,tenant_id)}
