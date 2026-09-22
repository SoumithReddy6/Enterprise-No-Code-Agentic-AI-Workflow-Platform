"""Immutable write approvals, serialized against the same job lease as execution."""
import hashlib
import hmac
import json
import os
import time
from sqlalchemy import Column,String,Text,JSON,Float,Integer,UniqueConstraint,select,update
from sqlalchemy.orm import Session
from .storage import Base,RunRecord,JobRecord,new_id,now
from .observability import journal

class ApprovalPause(Exception):
    def __init__(self,node_id,checkpoint_owner,invocation,prepared):
        super().__init__('Human approval required before this external write.')
        self.node_id=node_id;self.checkpoint_owner=checkpoint_owner;self.invocation=invocation;self.prepared=prepared;self.frames={}

class ApprovalRecord(Base):
    __tablename__='run_approvals'
    __table_args__=(UniqueConstraint('run_id','invocation',name='uq_run_approval_invocation'),)
    id=Column(String(64),primary_key=True)
    run_id=Column(String(64),nullable=False,index=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    node_id=Column(String(128),nullable=False)
    checkpoint_owner=Column(String(128),nullable=False)
    invocation=Column(String(300),nullable=False)
    payload=Column(JSON,nullable=False)
    digest=Column(String(64),nullable=False)
    execution=Column(Text,nullable=False)
    status=Column(String(24),nullable=False,index=True)
    created_at=Column(Float,nullable=False)
    expires_at=Column(Float,nullable=False,index=True)
    result=Column(Text,nullable=True)
    owns_marker=Column(Integer,nullable=False,default=0)

class ApprovalRate(Base):
    __tablename__='approval_rate_limits'
    tenant_id=Column(String(64),primary_key=True)
    window=Column(Integer,nullable=False)
    attempts=Column(Integer,nullable=False)

TENANT_MODELS=[ApprovalRecord,ApprovalRate]

def preview(payload):return json.dumps(payload,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False)
def digest(payload):return hashlib.sha256(preview(payload).encode()).hexdigest()
def public(row):return {**{key:getattr(row,key) for key in ('id','run_id','node_id','checkpoint_owner','payload','digest','status','created_at','expires_at')},'payload_json':preview(row.payload)}
def listing(session,run_id,tenant):return [public(r) for r in session.scalars(select(ApprovalRecord).where(ApprovalRecord.run_id==run_id,ApprovalRecord.tenant_id==tenant).order_by(ApprovalRecord.created_at))]

def pause(store,run_id,owner,exc):
    ttl=int(os.getenv('APPROVAL_TTL_SECONDS','86400'))
    if not 1<=ttl<=604800:raise ValueError('APPROVAL_TTL_SECONDS must be between 1 and 604800')
    with Session(store.engine) as s:
        if not store._fence(s,run_id,owner):raise ValueError('Execution lease is no longer owned.')
        run=s.get(RunRecord,run_id);job=s.get(JobRecord,run_id)
        if job.cancel_requested:raise ValueError('Run was cancelled before approval could be requested.')
        payload=exc.prepared['payload'];stamp=time.time()
        row=ApprovalRecord(id=new_id(),run_id=run_id,tenant_id=run.tenant_id,node_id=exc.node_id,checkpoint_owner=exc.checkpoint_owner,invocation=exc.invocation,payload=payload,digest=digest(payload),execution=store.cipher.encrypt(json.dumps(exc.prepared).encode()).decode(),status='pending',created_at=stamp,expires_at=stamp+ttl)
        s.add(row)
        run.data={**run.data,'status':'awaiting_approval','agent_frames':exc.frames};run.status='awaiting_approval'
        job.status='awaiting_approval';job.owner='';job.lease_until=0
        store._event(s,run,{'kind':'approval','node_id':exc.node_id,'status':'awaiting_approval','approval_id':row.id})
        s.commit();journal(event='approval.pending',run_id=run_id,tenant=run.tenant_id,node_id=exc.node_id)

def _close(store,s,row,run,job,status):
    row.status=status;job.status='cancelled';job.owner='';job.lease_until=0
    run.status='cancelled';run.data={**run.data,'status':'cancelled','finished_at':now(),'approval_terminal':True,'error':'Approval '+status+'. No external request was sent.'}
    from .operator_metrics import run_values
    for key,value in run_values(run.data).items():setattr(run,key,value)
    store._event(s,run,{'kind':'approval','node_id':row.node_id,'status':status,'approval_id':row.id})

def expire_pending(store,run_id=None):
    stamp=time.time()
    with Session(store.engine) as s:
        query=select(ApprovalRecord.id,ApprovalRecord.run_id).where(ApprovalRecord.status.in_(('pending','approved')),ApprovalRecord.expires_at<=stamp).limit(100)
        if run_id:query=query.where(ApprovalRecord.run_id==run_id)
        ids=s.execute(query).all()
    for approval_id,id in ids:
        with Session(store.engine) as s:
            s.execute(update(JobRecord).where(JobRecord.run_id==id).values(run_id=id))
            row=s.get(ApprovalRecord,approval_id);run=s.get(RunRecord,id);job=s.get(JobRecord,id)
            if row and row.status in ('pending','approved') and row.expires_at<=time.time() and job.status in ('awaiting_approval','queued'):
                _close(store,s,row,run,job,'expired');s.commit()
                journal(event='approval.expired',run_id=id,tenant=run.tenant_id)

def _rate_limit(store,tenant):
    from .auth_security import security_transaction
    from fastapi import HTTPException
    window=int(time.time()//60)
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    insert=pg_insert if store.engine.dialect.name=='postgresql' else sqlite_insert
    with security_transaction(store.engine) as s:
        s.execute(insert(ApprovalRate).values(tenant_id=tenant,window=window,attempts=0).on_conflict_do_nothing(index_elements=['tenant_id']))
        row=s.scalar(select(ApprovalRate).where(ApprovalRate.tenant_id==tenant).with_for_update())
        if row.window!=window:row.window=window;row.attempts=0
        row.attempts+=1;blocked=row.attempts>30
    if blocked:raise HTTPException(429,'Too many approval decisions. Try again shortly.',headers={'Retry-After':'60'})

def decide(store,run_id,tenant,approval_id,payload_digest,action):
    _rate_limit(store,tenant)
    if action not in ('approve','reject'):raise ValueError('Invalid approval decision')
    with Session(store.engine) as s:
        s.execute(update(JobRecord).where(JobRecord.run_id==run_id).values(run_id=run_id))
        row=s.get(ApprovalRecord,approval_id);run=s.get(RunRecord,run_id);job=s.get(JobRecord,run_id)
        if not row or not run or row.run_id!=run_id or row.tenant_id!=tenant or run.tenant_id!=tenant:raise KeyError('Approval not found')
        if not hmac.compare_digest(row.digest,payload_digest):raise ValueError('The approval payload changed or the digest does not match. Reload the request before deciding.')
        if row.status in ('pending','approved') and row.expires_at<=time.time():
            _close(store,s,row,run,job,'expired');s.commit();raise ValueError('This approval has expired')
        if action=='approve' and row.status in ('approved','executing','completed'):return public(row)
        if action=='reject' and row.status=='rejected':return public(row)
        if row.status!='pending' or run.status!='awaiting_approval':raise ValueError('This approval is no longer pending: '+row.status)
        if row.expires_at<=time.time():
            _close(store,s,row,run,job,'expired');s.commit();raise ValueError('This approval has expired')
        if action=='reject':_close(store,s,row,run,job,'rejected')
        else:
            row.status='approved';job.status='queued';job.owner='';job.lease_until=0
            run.status='queued';run.data={**run.data,'status':'queued'}
            store._event(s,run,{'kind':'approval','node_id':row.node_id,'status':'approved','approval_id':row.id})
        s.commit();journal(event='approval.'+row.status,run_id=run_id,tenant=tenant,node_id=row.node_id)
        return public(row)

def resolve(store,run_id,owner,invocation):
    with Session(store.engine) as s:
        if not store._fence(s,run_id,owner):raise ValueError('Execution lease is no longer owned.')
        run=s.get(RunRecord,run_id)
        row=s.scalar(select(ApprovalRecord).where(ApprovalRecord.run_id==run_id,ApprovalRecord.invocation==invocation,ApprovalRecord.tenant_id==run.tenant_id))
        if row is None:return None
        value={'id':row.id,'status':row.status,'prepared':json.loads(store.cipher.decrypt(row.execution.encode())),'result':row.result,'expires_at':row.expires_at}
        s.commit();return value

def begin(store,run_id,owner,approval_id):
    from .tool_service import UncertainWriteError
    with Session(store.engine) as s:
        if not store._fence(s,run_id,owner):raise ValueError('Execution lease is no longer owned.')
        run=s.get(RunRecord,run_id);row=s.get(ApprovalRecord,approval_id);job=s.get(JobRecord,run_id)
        if not row or row.run_id!=run_id or row.tenant_id!=run.tenant_id:raise ValueError('Approval unavailable')
        if row.status=='executing':raise UncertainWriteError('Approved write outcome is uncertain; reconcile before starting another run.')
        if row.status=='approved' and row.expires_at<=time.time():
            _close(store,s,row,run,job,'expired');s.commit();raise ValueError('This approval has expired')
        if row.status!='approved' or job.cancel_requested:raise ValueError('Approval is cancelled or unavailable')
        writes=set(run.data.get('write_nodes',[]));row.owns_marker=int(row.checkpoint_owner not in writes);writes.add(row.checkpoint_owner)
        run.data={**run.data,'write_nodes':list(writes)};row.status='executing';s.commit()

def complete(store,run_id,owner,approval_id,result):
    with Session(store.engine) as s:
        if not store._fence(s,run_id,owner):raise ValueError('Execution lease lost after external write; reconciliation required')
        row=s.get(ApprovalRecord,approval_id);run=s.get(RunRecord,run_id)
        if not row or row.run_id!=run_id or row.status!='executing':raise ValueError('Approval execution receipt unavailable')
        row.status='completed';row.result=result
        if row.owns_marker:run.data={**run.data,'write_nodes':[n for n in run.data.get('write_nodes',[]) if n!=row.checkpoint_owner]}
        store._event(s,run,{'kind':'approval','node_id':row.node_id,'status':'completed','approval_id':row.id});s.commit()
