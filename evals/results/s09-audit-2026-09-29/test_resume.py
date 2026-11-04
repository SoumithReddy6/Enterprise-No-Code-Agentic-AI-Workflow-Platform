import asyncio
import json
import pytest
from backend.tests.test_loop_completeness import durable_setup,jira_connection,loop_flow


def crash_after_item(store,monkeypatch,index):
    original=store.worker_event
    def record(id,owner,event):
        result=original(id,owner,event)
        if event.get('kind')=='loop_item' and event.get('item_index')==index:
            raise asyncio.CancelledError()
        return result
    monkeypatch.setattr(store,'worker_event',record)
    return original


@pytest.mark.asyncio
async def test_resume_preserves_stop_after_failed_item(tmp_path,monkeypatch):
    store,worker,_,_=durable_setup(tmp_path,monkeypatch)
    calls=[]
    async def execute(*args):
        calls.append(args[2])
        if len(calls)==1:raise ValueError('first item fails')
        return 'done'
    monkeypatch.setattr(worker.tools,'execute',execute)
    workflow=loop_flow(connection_id=jira_connection(store),on_item_error='stop')
    row=store.create_run(workflow.model_dump(mode='json'),'go')
    original=crash_after_item(store,monkeypatch,0)
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(row['id'])['loop_progress']['each']['0']['status']=='failed'
    monkeypatch.setattr(store,'worker_event',original)
    store.resume_run(row['id']);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(row['id'])
    assert len(calls)==1,f'stop policy ran {len(calls)} items after resume'
    assert saved['truncated'] is True


@pytest.mark.asyncio
async def test_agent_partial_item_survives_mid_loop_resume(tmp_path,monkeypatch):
    from backend.app import iteration
    store,worker,_,_=durable_setup(tmp_path,monkeypatch)
    calls=[]
    async def body(node,text,ctx,identity,emit,**kwargs):
        calls.append(identity.item_index)
        return {'text':'partial','grounding':json.dumps({'truncated':identity.item_index==0,'truncation_reason':'partial first item' if identity.item_index==0 else ''})}
    monkeypatch.setattr(iteration,'invoke_attached',body)
    row=store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'),'go')
    original=crash_after_item(store,monkeypatch,0)
    await worker.execute(store.claim_next(worker.owner))
    monkeypatch.setattr(store,'worker_event',original)
    store.resume_run(row['id']);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(row['id'])
    assert saved['status']=='success' and saved['truncated'] is True
    assert 'partial first item' in saved['truncation_reason']
    assert calls==[0,1,2]


@pytest.mark.asyncio
async def test_two_incomplete_loops_aggregate_reasons(tmp_path,monkeypatch):
    from backend.app.models import Workflow
    store,worker,_,_=durable_setup(tmp_path,monkeypatch)
    raw=loop_flow(connection_id=jira_connection(store),max_items=1).model_dump(mode='json')
    raw['nodes'] += [dict(raw['nodes'][2],id='second',config={'body':'other','max_items':2}),dict(raw['nodes'][3],id='other')]
    next(e for e in raw['edges'] if e['id']=='c')['target']='second'
    raw['edges'] += [{'id':'next','source':'second','target':'out'}, {'id':'body2','source':'second','target':'other','kind':'loop','sourceHandle':'body','targetHandle':'input'}]
    row=store.create_run(Workflow.model_validate(raw).model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(row['id'])
    assert saved['status']=='success' and saved['truncated'] is True
    assert 'each accepted 1 of 3' in saved['truncation_reason']
    assert 'second accepted 2 of 3' in saved['truncation_reason']


@pytest.mark.asyncio
async def test_legacy_limited_checkpoint_is_not_reported_complete(tmp_path,monkeypatch):
    from backend.tests.test_loop_completeness import loop_then_step
    from backend.app.storage import RunRecord
    from sqlalchemy.orm import Session
    store,worker,execute,_=durable_setup(tmp_path,monkeypatch)
    execute.interrupt=True
    row=store.create_run(loop_then_step(jira_connection(store),2).model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))
    with Session(store.engine) as session:
        record=session.get(RunRecord,row['id']);checkpoints=dict(record.data['checkpoints'])
        checkpoints['each']={k:v for k,v in checkpoints['each'].items() if k!='summary'}
        record.data={**record.data,'checkpoints':checkpoints};session.commit()
    store.resume_run(row['id']);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(row['id']);assert saved['status']=='success'
    assert saved['truncated'] is True,'known 2-of-3 legacy loop reported complete'


@pytest.mark.asyncio
async def test_resume_reapplies_result_bound_to_restored_items(tmp_path,monkeypatch):
    from backend.app import iteration
    monkeypatch.setattr(iteration,'MAX_RESULT_BYTES',150)
    store,worker,_,_=durable_setup(tmp_path,monkeypatch)
    row=store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'),'go')
    original=crash_after_item(store,monkeypatch,2)
    await worker.execute(store.claim_next(worker.owner))
    assert len(store.run(row['id'])['loop_progress']['each'])==3
    monkeypatch.setattr(store,'worker_event',original)
    store.resume_run(row['id']);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(row['id']);assert saved['status']=='success'
    summary=json.loads(saved['checkpoints']['each']['summary'])
    assert summary['values_dropped'] is True,summary
    assert saved['truncated'] is True
