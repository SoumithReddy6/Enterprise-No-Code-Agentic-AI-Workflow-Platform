"""Immutable write approvals, serialized against the same job lease as execution."""
import hashlib
import hmac
import json
import os
import time
from sqlalchemy import Column,String,Text,JSON,Float,Integer,UniqueConstraint,CheckConstraint,select,update
from sqlalchemy.orm import Session
from .storage import Base,RunRecord,JobRecord,new_id,now
from .observability import journal

class ApprovalPause(Exception):
    def __init__(self,node_id,checkpoint_owner,invocation,prepared):
        super().__init__('Human approval required before this external write.')
        self.node_id=node_id;self.checkpoint_owner=checkpoint_owner;self.invocation=invocation;self.prepared=prepared;self.frames={}

class ReviewPause(ApprovalPause):
    def __init__(self,identity,kind,payload):
        if kind not in ('decision','verification'):raise ValueError('Invalid review kind.')
        if len(preview(payload).encode())>1_000_000:raise ValueError('Review payload exceeds its exact display size limit.')
        super().__init__(identity.node_id,identity.checkpoint_owner,identity.invocation_id+':review:'+kind,
                         {'kind':kind,'payload':payload})
        self.kind=kind;self.identity=identity

class ApprovalRecord(Base):
    __tablename__='run_approvals'
    __table_args__=(UniqueConstraint('run_id','invocation',name='uq_run_approval_invocation'),
                   CheckConstraint("kind IN ('write','decision','verification')",name='ck_run_approval_kind'))
    id=Column(String(64),primary_key=True)
    run_id=Column(String(64),nullable=False,index=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    node_id=Column(String(128),nullable=False)
    checkpoint_owner=Column(String(128),nullable=False)
    invocation=Column(String(512),nullable=False)
    kind=Column(String(20),nullable=False,default='write',server_default='write')
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
def public(row):return {**{key:getattr(row,key) for key in ('id','run_id','node_id','checkpoint_owner','kind','payload','digest','status','created_at','expires_at')},'payload_json':preview(row.payload)}
def listing(session,run_id,tenant):return [public(r) for r in session.scalars(select(ApprovalRecord).where(ApprovalRecord.run_id==run_id,ApprovalRecord.tenant_id==tenant).order_by(ApprovalRecord.created_at))]

def pause(store,run_id,owner,exc):
    ttl=int(os.getenv('APPROVAL_TTL_SECONDS','86400'))
    if not 1<=ttl<=604800:raise ValueError('APPROVAL_TTL_SECONDS must be between 1 and 604800')
    with Session(store.engine) as s:
        if not store._fence(s,run_id,owner):raise ValueError('Execution lease is no longer owned.')
        run=s.get(RunRecord,run_id);job=s.get(JobRecord,run_id)
        if job.cancel_requested:raise ValueError('Run was cancelled before approval could be requested.')
        payload=exc.prepared['payload'];stamp=time.time()
        kind=exc.kind if isinstance(exc,ReviewPause) else 'write'
        row=s.scalar(select(ApprovalRecord).where(ApprovalRecord.run_id==run_id,ApprovalRecord.invocation==exc.invocation))
        if row is not None:
            if row.status!='pending' or row.kind!=kind or row.digest!=digest(payload):raise ValueError('Existing review differs from this request.')
        else:
            row=ApprovalRecord(id=new_id(),run_id=run_id,tenant_id=run.tenant_id,node_id=exc.node_id,checkpoint_owner=exc.checkpoint_owner,invocation=exc.invocation,kind=kind,payload=payload,digest=digest(payload),execution=store.cipher.encrypt(json.dumps(exc.prepared).encode()).decode(),status='pending',created_at=stamp,expires_at=stamp+ttl)
            s.add(row)
        # The action was reserved before the pause but never executed. Record that here,
        # in the transaction that releases the lease, so the approved execution reuses the
        # reservation instead of being charged twice. The seam cannot do it: by the time
        # the pause reaches it, this worker no longer holds the lease.
        accounting=dict(store._accounting(run) or {})
        budget=dict(accounting.get('action_budget') or {})
        if budget and kind=='write':
            budget['deferred']=[*budget.get('deferred',[]),f'{exc.invocation}#0']
            accounting['action_budget']=budget
        run.data={**run.data,'status':'awaiting_approval','agent_frames':exc.frames};run.status='awaiting_approval'
        if budget:run.accounting=accounting
        job.status='awaiting_approval';job.owner='';job.lease_until=0
        store._event(s,run,{'kind':'approval','approval_kind':kind,'node_id':exc.node_id,'status':'awaiting_approval','approval_id':row.id})
        s.commit();journal(event='approval.pending',run_id=run_id,tenant=run.tenant_id,node_id=exc.node_id,approval_kind=kind)

def _close(store,s,row,run,job,status):
    row.status=status;job.status='cancelled';job.owner='';job.lease_until=0
    message='Approval '+status+'. No external request was sent.' if row.kind=='write' else 'Review '+status+'. No later action was authorized.'
    run.status='cancelled';run.data={**run.data,'status':'cancelled','finished_at':now(),'approval_terminal':True,'error':message}
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

def decide(store,run_id,tenant,approval_id,payload_digest,action,choice=None):
    _rate_limit(store,tenant)
    if action not in ('approve','reject'):raise ValueError('Invalid approval decision')
    with Session(store.engine) as s:
        s.execute(update(JobRecord).where(JobRecord.run_id==run_id).values(run_id=run_id))
        row=s.get(ApprovalRecord,approval_id);run=s.get(RunRecord,run_id);job=s.get(JobRecord,run_id)
        if not row or not run or row.run_id!=run_id or row.tenant_id!=tenant or run.tenant_id!=tenant:raise KeyError('Approval not found')
        if not hmac.compare_digest(row.digest,digest(row.payload)):raise ValueError('Stored review payload no longer matches its digest.')
        if not hmac.compare_digest(row.digest,payload_digest):raise ValueError('The approval payload changed or the digest does not match. Reload the request before deciding.')
        if action=='approve':
            if row.kind=='decision' and type(choice) is not bool:raise ValueError('Decision review requires an explicit true/false choice.')
            if row.kind!='decision' and choice is not None:raise ValueError('Only a decision review accepts a true/false choice.')
            if row.kind=='decision' and row.status in ('approved','completed') and row.result!=json.dumps(choice):raise ValueError('This decision was already confirmed with a different choice.')
        if action=='reject' and row.status=='rejected':return public(row)
        if job.cancel_requested or run.data.get('approval_terminal'):raise ValueError('This run is cancelled or its review is terminal.')
        if row.status in ('pending','approved') and row.expires_at<=time.time():
            _close(store,s,row,run,job,'expired');s.commit();raise ValueError('This approval has expired')
        if action=='approve' and row.status in ('approved','executing','completed'):return public(row)
        if action=='reject' and row.status=='rejected':return public(row)
        if row.status!='pending' or run.status!='awaiting_approval':raise ValueError('This approval is no longer pending: '+row.status)
        if row.expires_at<=time.time():
            _close(store,s,row,run,job,'expired');s.commit();raise ValueError('This approval has expired')
        if action=='reject':_close(store,s,row,run,job,'rejected')
        else:
            if row.kind=='decision':row.result=json.dumps(choice)
            elif row.kind=='verification':row.result='accepted'
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
        value={'id':row.id,'kind':row.kind,'status':row.status,'prepared':json.loads(store.cipher.decrypt(row.execution.encode())),'result':row.result,'expires_at':row.expires_at}
        s.commit();return value

def begin(store,run_id,owner,approval_id):
    from .tool_service import UncertainWriteError
    with Session(store.engine) as s:
        if not store._fence(s,run_id,owner):raise ValueError('Execution lease is no longer owned.')
        run=s.get(RunRecord,run_id);row=s.get(ApprovalRecord,approval_id);job=s.get(JobRecord,run_id)
        if not row or row.run_id!=run_id or row.tenant_id!=run.tenant_id:raise ValueError('Approval unavailable')
        if row.kind!='write':raise ValueError('This approval is not an external write.')
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


def consume_review(store,run_id,owner,invocation,kind,payload):
    """Validate and settle a typed confirmation under the execution fence, with no I/O."""
    from .storage import LeaseLost
    with Session(store.engine) as s:
        if not store._fence(s,run_id,owner):raise LeaseLost('Execution lease is no longer owned.')
        run=s.get(RunRecord,run_id);job=s.get(JobRecord,run_id)
        row=s.scalar(select(ApprovalRecord).where(ApprovalRecord.run_id==run_id,ApprovalRecord.invocation==invocation,ApprovalRecord.tenant_id==run.tenant_id))
        if row is None:return None
        if row.kind!=kind or row.payload!=payload or not hmac.compare_digest(row.digest,digest(payload)):
            raise ValueError('The reviewed interpretation no longer matches its input, value or policy.')
        if job.cancel_requested or run.data.get('approval_terminal'):raise ValueError('This review was cancelled.')
        if row.expires_at<=time.time():
            _close(store,s,row,run,job,'expired');s.commit();raise ValueError('This review has expired.')
        if row.status not in ('approved','completed'):raise ValueError('This review has not been confirmed.')
        if kind=='decision':
            choice=json.loads(row.result or 'null')
            if type(choice) is not bool:raise ValueError('Review has no true/false choice.')
        elif row.result=='accepted':choice=True
        else:raise ValueError('Review has no confirmation.')
        newly_confirmed=row.status=='approved'
        if newly_confirmed:
            row.status='completed';store._event(s,run,{'kind':'approval','approval_kind':kind,'node_id':row.node_id,'status':'completed','approval_id':row.id})
        s.commit()
        if newly_confirmed:journal(event='review.confirmed',run_id=run_id,tenant=run.tenant_id,node_id=row.node_id,approval_kind=kind,manual=True)
        return {'id':row.id,'choice':choice}


def checked_review(store,run_id,owner,review_id,kind,payload):
    """Restore only a real confirmation, never a review id supplied by a handler."""
    from .storage import LeaseLost
    with Session(store.engine) as s:
        if not store._fence(s,run_id,owner):raise LeaseLost('Execution lease is no longer owned.')
        run=s.get(RunRecord,run_id);job=s.get(JobRecord,run_id);row=s.get(ApprovalRecord,review_id)
        if not row or row.run_id!=run_id or row.tenant_id!=run.tenant_id or row.kind!=kind or row.status!='completed' or row.expires_at<=time.time() or job.cancel_requested:
            raise ValueError('Verification review is no longer eligible.')
        if row.payload!=payload or not hmac.compare_digest(row.digest,digest(payload)):raise ValueError('Verification review payload changed.')
        choice=json.loads(row.result or 'null') if kind=='decision' else row.result=='accepted'
        if type(choice) is not bool or kind=='verification' and choice is not True:raise ValueError('Review has no valid confirmation.')
        s.commit();return {'id':row.id,'choice':choice}
