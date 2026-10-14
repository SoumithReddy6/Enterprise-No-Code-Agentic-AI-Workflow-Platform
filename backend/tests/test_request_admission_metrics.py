import json
from cryptography.fernet import Fernet
from sqlalchemy import event

from backend.app.storage import Store
from backend.app.operator_metrics import metrics
from backend.tests.test_compiler import sample
import pytest


def test_admission_metrics_count_codes_without_reading_or_returning_run_content(tmp_path):
    store = Store(f'sqlite:///{tmp_path}/metrics.db', Fernet.generate_key())
    run = store.create_run(sample(), 'PRIVATE INPUT'); store.claim_next('worker')
    events = [
        {'request_stage':'greeting_cache','status':'success','outputs':{'receipt':'{"outcome":"local_fallback"}','reply':'PRIVATE REPLY'}},
        {'request_stage':'request_router','status':'success','outputs':{'decision':'{"route":"proceed","reason":"PRIVATE REASON"}'}},
        {'request_stage':'verify_extraction','status':'paused'},
        {'request_stage':'request_router','status':'failed','error':'PRIVATE ERROR'},
        {'request_stage':'request_router','status':'success','cached':True,'outputs':{'decision':'{"route":"proceed"}'}},
    ]
    for item in events:store.worker_event(run['id'],'worker',{'node_id':'custom-name',**item})
    store.finish_run(run['id'],'worker','success')
    queries=[]
    event.listen(store.engine,'before_cursor_execute',lambda conn,cursor,statement,*args:queries.append(statement))
    report=metrics(store)
    assert report['request_admission']['outcomes'] == [
        {'stage':'greeting_cache','status':'success','outcome':'local_fallback','count':1},
        {'stage':'request_router','status':'failed','outcome':'error','count':1},
        {'stage':'request_router','status':'success','outcome':'proceed','count':1},
        {'stage':'verify_extraction','status':'paused','outcome':'needs_review','count':1},
    ]
    assert report['request_admission']['checkpoint_replays']==1
    assert 'PRIVATE' not in json.dumps(report)
    assert all('runs.data' not in s and 'payload' not in s for s in queries)
    store.engine.dispose()


@pytest.mark.asyncio
async def test_real_worker_tags_admission_events_for_metrics(tmp_path):
    from backend.app.worker import Worker
    from backend.tests.test_request_examples import load
    store=Store(f'sqlite:///{tmp_path}/worker.db',Fernet.generate_key());store.allow_model('ollama','fixture')
    worker=Worker(store,common_reply_cache=None)
    row=store.create_run(load('qa').model_dump(mode='json'),'Hello!')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(row['id'])['status']=='success'
    assert metrics(store)['request_admission']['outcomes']==[
        {'stage':'greeting_cache','status':'success','outcome':'local_fallback','count':1}]
    store.engine.dispose()
