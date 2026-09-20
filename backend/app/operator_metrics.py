"""Indexed, SQL-computed operator summaries; no prompts or outputs are returned."""
import json
from datetime import datetime,timezone,timedelta
from sqlalchemy import Column,String,Integer,select,func,case
from sqlalchemy.orm import Session
from .storage import Base,RunRecord,RunEventRecord,SchemaMigrationRecord

class RunTokenRecord(Base):
    __tablename__='run_token_usage'
    run_id=Column(String(64),primary_key=True)
    seq=Column(Integer,primary_key=True)
    provider=Column(String(24),primary_key=True)
    model=Column(String(100),primary_key=True)
    prompt_tokens=Column(Integer,nullable=False)
    completion_tokens=Column(Integer,nullable=False)
    calls=Column(Integer,nullable=False)

def usage_rows(event,workflow):
    if event.get('cached'):return []
    usage=event.get('usage') or {}
    if not isinstance(usage,dict):return []
    rows=usage.get('by_model')
    if rows is None:
        config=next((n.get('config',{}) for n in workflow.get('nodes',[]) if n.get('id')==event.get('node_id')), {})
        rows=[{**usage,'provider':config.get('provider','unknown'),'model':config.get('model','unknown')}] if usage else []
    grouped={}
    for row in rows:
        provider=str(row.get('provider','unknown'))[:24];model=str(row.get('model','unknown'))[:100]
        key=(provider,model);total=grouped.setdefault(key,{'provider':provider,'model':model,'prompt_tokens':0,'completion_tokens':0,'calls':0})
        for field in ('prompt_tokens','completion_tokens','calls'):
            value=row.get(field,0)
            if isinstance(value,int) and 0<=value<=10**12:total[field]+=value
    return list(grouped.values())

def run_values(data):
    duration=None
    if data.get('finished_at') and data.get('created_at'):
        try:duration=max(0,(datetime.fromisoformat(data['finished_at'])-datetime.fromisoformat(data['created_at'])).total_seconds())
        except (ValueError,TypeError):pass
    grounding=[]
    for output in data.get('checkpoints',{}).values():
        try:value=json.loads(output.get('grounding','{}'))
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and isinstance(value.get('abstain'),bool):grounding.append(value['abstain'])
    return {'duration_seconds':duration,'grounded':bool(grounding),'abstained':any(grounding),'truncated':bool(data.get('truncated'))}

def migrate(conn):
    version='20260920_operator_metrics'
    if conn.scalar(select(SchemaMigrationRecord.version).where(SchemaMigrationRecord.version==version)):return
    for row in conn.execute(select(RunRecord.__table__)).mappings().yield_per(100):
        conn.execute(RunRecord.__table__.update().where(RunRecord.id==row['id']).values(**run_values(row['data'])))
    query=select(RunEventRecord.run_id,RunEventRecord.seq,RunEventRecord.payload,RunRecord.data).join(RunRecord,RunRecord.id==RunEventRecord.run_id)
    for row in conn.execute(query).yield_per(100):
        for usage in usage_rows(row.payload,row.data.get('workflow',{})):
            conn.execute(RunTokenRecord.__table__.insert().values(run_id=row.run_id,seq=row.seq,**usage))
    conn.execute(SchemaMigrationRecord.__table__.insert().values(version=version))

def metrics(store,hours=168):
    end=datetime.now(timezone.utc);start=end-timedelta(hours=hours)
    window=(RunRecord.created_at>=start.isoformat(),RunRecord.created_at<end.isoformat())
    with Session(store.engine) as session:
        counts=dict(session.execute(select(RunRecord.status,func.count()).where(*window).group_by(RunRecord.status)).all())
        total,grounded,abstained,truncated=session.execute(select(func.count(),func.sum(case((RunRecord.grounded,1),else_=0)),func.sum(case((RunRecord.abstained,1),else_=0)),func.sum(case((RunRecord.truncated,1),else_=0))).where(*window,RunRecord.status=='success')).one()
        # SQL window ranks implement portable nearest-rank percentiles on SQLite/PostgreSQL.
        ranked=select(RunRecord.duration_seconds.label('duration'),func.row_number().over(order_by=RunRecord.duration_seconds).label('rank'),func.count().over().label('n')).where(*window,RunRecord.duration_seconds.is_not(None),RunRecord.status.in_(('success','failed','cancelled'))).subquery()
        percentiles={}
        for name,p in (('p50',.5),('p95',.95)):
            percentiles[name]=session.scalar(select(func.min(ranked.c.duration)).where(ranked.c.rank>=ranked.c.n*p))
        tokens=[dict(row._mapping) for row in session.execute(select(RunTokenRecord.provider,RunTokenRecord.model,func.sum(RunTokenRecord.prompt_tokens).label('prompt_tokens'),func.sum(RunTokenRecord.completion_tokens).label('completion_tokens'),func.sum(RunTokenRecord.calls).label('calls')).join(RunRecord,RunRecord.id==RunTokenRecord.run_id).where(*window).group_by(RunTokenRecord.provider,RunTokenRecord.model).order_by(RunTokenRecord.provider,RunTokenRecord.model))]
    return {'window':{'start':start.isoformat(),'end':end.isoformat(),'basis':'run_created_at'},'runs_by_status':counts,'duration_seconds':percentiles,'tokens':tokens,
            'abstention_rate':abstained/grounded if grounded else None,'truncation_rate':truncated/total if total else None,
            'denominators':{'successful_runs':total,'successful_grounded_runs':grounded or 0},
            'definitions':{'abstention':'Any grounded node abstained in a successful run','duration':'Creation to final completion, including queue and resumed attempts','tokens':'Provider-reported tokens; not monetary cost; missing usage is not estimated'}}
