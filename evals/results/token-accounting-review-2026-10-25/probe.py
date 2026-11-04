"""Token-accounting review: real adapters/workers/storage, synthetic HTTP, no paid calls.

Exit zero means the observations reproduce, including defects. It is not a product gate.
"""
import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import httpx
from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app import providers, registry, token_budget
from backend.app.execution_policy import ExecutionIdentity
from backend.app.operator_metrics import RunTokenRecord
from backend.app.registry import Context, LLMConfig
from backend.app.storage import Store
from backend.app.worker import Worker

OUT=Path(__file__).parent
MODEL='token-review-fixture'
SOURCES=['token_budget.py','providers.py','registry.py','execution_policy.py','compiler.py',
         'worker.py','storage.py','agent_runtime.py','operator_metrics.py']

def hashes():return {name:hashlib.sha256((ROOT/'backend/app'/name).read_bytes()).hexdigest() for name in SOURCES}

def model_node(name,source='input.message',**extra):
    return {'id':name,'type':'llm','inputs':{'prompt':source},
            'config':{'provider':'ollama','model':MODEL,'max_tokens':128},**extra}

def flow(count=1):
    nodes=[{'id':'input','type':'chat_input'}]
    for i in range(count):nodes.append(model_node(f'm{i}','input.message' if i==0 else f'm{i-1}.text'))
    nodes.append({'id':'out','type':'response','inputs':{'text':f'm{count-1}.text'}})
    return {'version':1,'name':'Synthetic token review','nodes':nodes,
            'edges':[{'id':str(i),'source':a['id'],'target':b['id']} for i,(a,b) in enumerate(zip(nodes,nodes[1:]))]}

@contextmanager
def database():
    with tempfile.TemporaryDirectory(prefix='relay-token-review-') as directory:
        store=Store('sqlite:///'+str(Path(directory)/'review.db'),Fernet.generate_key())
        store.allow_model('ollama',MODEL)
        try:yield store
        finally:store.engine.dispose()

def transport(mp,handler):
    original=httpx.AsyncClient
    mp.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(handler),trust_env=False))

def totals(store,id):
    with Session(store.engine) as s:
        rows=list(s.scalars(select(RunTokenRecord).where(RunTokenRecord.run_id==id)))
        return {'tokens':sum(r.prompt_tokens+r.completion_tokens for r in rows),'calls':sum(r.calls for r in rows)}

async def execute(store,workflow):
    row=store.create_run(workflow,'synthetic input');worker=Worker(store)
    await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)),10)
    return store.run(row['id'])

async def sequential_overshoot():
    with MonkeyPatch.context() as mp,database() as store:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','100');calls=[]
        def handler(request):
            calls.append(1);p,c=(70,10) if len(calls)==1 else (25,5)
            return httpx.Response(200,json={'message':{'content':'answer'},'prompt_eval_count':p,'eval_count':c})
        transport(mp,handler);saved=await execute(store,flow(2))
        assert saved['status']=='success' and saved['accounting']['tokens_spent']==110
        return {'limit':100,'provider_calls':len(calls),'accounted_tokens':110,'status':saved['status'],'truncated':saved.get('truncated',False)}

async def missing_usage():
    with MonkeyPatch.context() as mp,database() as store:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','1');calls=[]
        def handler(request):calls.append(1);return httpx.Response(200,json={'message':{'content':'answer'}})
        transport(mp,handler);saved=await execute(store,flow(3));reported=totals(store,saved['id'])
        assert len(calls)==3 and reported=={'tokens':0,'calls':0}
        return {'limit':1,'provider_calls':3,'reported':reported,'status':saved['status'],
                'truncated':saved.get('truncated',False),'accounting':saved['accounting']}

async def rejected_paid_response():
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','1000')
        transport(mp,lambda request:httpx.Response(200,json={
            'choices':[{'message':{'role':'assistant','content':''},'finish_reason':'length'}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150,
                     'completion_tokens_details':{'reasoning_tokens':50}}}))
        run={};ctx=Context('',lambda _:'fixture-secret',node_id='model',checkpoint_owner='model',
                           execution_identity=ExecutionIdentity('model','model','model'),run=run)
        try:await registry.llm_node({'prompt':'fixture'},LLMConfig(provider='openai',model=MODEL,credential_id='key'),ctx)
        except providers.ProviderResponseError as exc:error=str(exc)
        else:raise AssertionError('expected unsupported empty text')
        assert token_budget.tokens_spent(run)==0
        return {'response_reported_tokens':150,'accounted_tokens':0,'error':error,'run':run}

async def provider_usage_shapes():
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','10000')
        transport(mp,lambda request:httpx.Response(200,json={'content':[{'type':'text','text':'answer'}],
            'usage':{'input_tokens':10,'cache_creation_input_tokens':400,'cache_read_input_tokens':600,'output_tokens':5}}))
        run={};ctx=Context('',lambda _:'fixture',node_id='claude',run=run)
        await registry.llm_node({'prompt':'fixture'},LLMConfig(provider='claude',model=MODEL,credential_id='key'),ctx)
        claude={'provider_total_including_cache':1015,'recorded_tokens':token_budget.tokens_spent(run)}
        assert claude['recorded_tokens']==15
        transport(mp,lambda request:httpx.Response(200,json={'choices':[{'message':{'content':'answer'}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150,
                     'prompt_tokens_details':{'cached_tokens':80},'completion_tokens_details':{'reasoning_tokens':20}}}))
        run={};ctx=Context('',lambda _:'fixture',node_id='openai',run=run)
        await registry.llm_node({'prompt':'fixture'},LLMConfig(provider='openai',model=MODEL,credential_id='key'),ctx)
        assert token_budget.tokens_spent(run)==150
        return {'claude_cache_response':claude,'openai_inclusive_control':{'reported_tokens':150,'recorded_tokens':150,'stored_usage':run['usage']}}

def recovery_flow(policy):
    w=flow(2);w['nodes'][2]['on_error']=policy
    w['nodes'][-1]['inputs']={'text':'input.message'}
    if policy=='route':
        w['nodes'].append({'id':'fallback','type':'response','inputs':{'text':'input.message'}})
        w['edges'][-1]['sourceHandle']='output'
        w['edges'].append({'id':'error','source':'m1','target':'fallback','sourceHandle':'error'})
    return w

async def recovered_token_refusal():
    results={}
    for policy in ['fail','continue','route']:
        with MonkeyPatch.context() as mp,database() as store:
            mp.setenv('RELAY_RUN_TOKEN_LIMIT','10');calls=[]
            def handler(request):calls.append(1);return httpx.Response(200,json={'message':{'content':'answer'},'prompt_eval_count':8,'eval_count':2})
            transport(mp,handler);saved=await execute(store,recovery_flow(policy))
            failure=next(e for e in saved['events'] if e.get('node_id')=='m1' and e['status']=='failed')
            assert len(calls)==1 and 'Run token budget exhausted' in failure['error']
            assert saved['status']==('failed' if policy=='fail' else 'success')
            results[policy]={'status':saved['status'],'provider_calls':len(calls),
                'truncated':saved.get('truncated',False),'failure':failure}
    assert not results['continue']['truncated'] and not results['route']['truncated']
    return results

def team_flow():
    return {'version':1,'name':'Nested usage review','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'agent','type':'agent','inputs':{'input':'input.message'},'config':{'provider':'ollama','model':MODEL}},
        {'id':'helper','type':'agent','config':{'provider':'ollama','model':MODEL}},
        {'id':'out','type':'response','inputs':{'text':'agent.text'}}],
        'edges':[{'id':'a','source':'input','target':'agent'},{'id':'b','source':'agent','target':'out'},
                 {'id':'helper','source':'agent','target':'helper','kind':'agent'}]}

async def nested_usage(cancel):
    with MonkeyPatch.context() as mp,database() as store:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','10000');entered=asyncio.Event();calls=[]
        replies=[json.dumps({'action':'call','target':'helper','input':'task'}),'helper answer',json.dumps({'action':'final','text':'combined answer'})]
        async def handler(request):
            index=len(calls);calls.append(1)
            if cancel and index==2:
                entered.set();await asyncio.Event().wait()
            return httpx.Response(200,json={'message':{'content':replies[index]},'prompt_eval_count':10,'eval_count':5})
        transport(mp,handler);row=store.create_run(team_flow(),'synthetic task');worker=Worker(store)
        task=asyncio.create_task(worker.execute(store.claim_next(worker.owner)))
        if cancel:
            await asyncio.wait_for(entered.wait(),5);store.request_cancel(row['id'])
        await asyncio.wait_for(task,10)
        saved=store.run(row['id']);reported=totals(store,row['id']);known=30 if cancel else 45
        assert saved['accounting']['tokens_spent']==known
        assert reported['tokens']==(45 if cancel else 45)
        return {'status':saved['status'],'known_provider_response_tokens':known,
                'persisted_accounting_tokens':saved['accounting']['tokens_spent'],
                'operator_metrics':reported,
                'usage_events':[{'node_id':e.get('node_id'),'status':e['status'],'usage':e['usage']} for e in saved['events'] if e.get('usage')]}

async def persistence_failure_resume():
    with MonkeyPatch.context() as mp,database() as store:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','15');calls=[];committed=threading.Event();release=threading.Event()
        def handler(request):calls.append(1);return httpx.Response(200,json={'message':{'content':'answer'},'prompt_eval_count':10,'eval_count':5})
        transport(mp,handler)
        original_record=store.record_accounting;original_event=store.worker_event
        def unavailable(*args):raise OSError('synthetic accounting outage')
        def barrier_event(id,owner,event):
            result=original_event(id,owner,event)
            if event.get('node_id')=='m0' and event['status']=='success' and not event.get('cached'):
                committed.set();assert release.wait(timeout=10)
            return result
        mp.setattr(store,'record_accounting',unavailable);mp.setattr(store,'worker_event',barrier_event)
        row=store.create_run(flow(2),'synthetic task');worker=Worker(store)
        task=asyncio.create_task(worker.execute(store.claim_next(worker.owner)))
        try:
            assert await asyncio.to_thread(committed.wait,5)
            task.cancel();await asyncio.sleep(0);release.set()
            await asyncio.wait_for(task,10)
        finally:
            release.set()
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        interrupted=store.run(row['id'])
        assert 'm0' in interrupted['checkpoints'] and not interrupted.get('accounting')
        mp.setattr(store,'record_accounting',original_record);mp.setattr(store,'worker_event',original_event)
        resumed=Worker(store)
        await asyncio.wait_for(resumed.execute(store.claim_next(resumed.owner)),10)
        saved=store.run(row['id'])
        assert saved['status']=='success' and len(calls)==2 and saved['accounting']['tokens_spent']==15
        return {'limit':15,'responses':2,'known_provider_response_tokens':30,
                'interrupted_had_checkpoint':True,'interrupted_accounting':interrupted.get('accounting'),
                'resumed_status':saved['status'],'final_accounting_tokens':saved['accounting']['tokens_spent'],
                'operator_metrics':totals(store,row['id'])}

def stale_snapshot():
    with database() as store:
        row=store.create_run(flow(),'synthetic input');owner='snapshot-review'
        assert store.claim_next(owner,lease_seconds=120)['id']==row['id']
        old={'tokens_spent':10,'action_budget':{'counter':1,'remaining':9},'usage':{},'usage_by_invocation':{}}
        new={'tokens_spent':30,'action_budget':{'counter':2,'remaining':8},'usage':{},'usage_by_invocation':{}}
        release=threading.Event();ready=threading.Event()
        def delayed():ready.set();assert release.wait(5);store.record_accounting(row['id'],owner,old)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(delayed);assert ready.wait(5)
            pool.submit(store.record_accounting,row['id'],owner,new).result(5)
            release.set();pending.result(5)
        saved=store.run(row['id'])['accounting'];assert saved['tokens_spent']==10
        return {'newer_tokens':30,'final_tokens':10,'scope':'latent parallel/out-of-order persistence risk; parallel graph branches are not implemented'}

async def main():
    before=hashes();rows={}
    cases=[('sequential_limit',sequential_overshoot),('missing_usage',missing_usage),
           ('paid_response_rejected',rejected_paid_response),('provider_usage_shapes',provider_usage_shapes),
           ('recovered_token_refusal',recovered_token_refusal),
           ('nested_success_control',lambda:nested_usage(False)),('nested_cancel',lambda:nested_usage(True)),
           ('accounting_outage_and_resume',persistence_failure_resume)]
    for name,fn in cases:
        try:rows[name]={'probe':'observed','details':await fn()}
        except Exception as exc:
            import traceback
            rows[name]={'probe':'harness_error','error':repr(exc),'traceback':traceback.format_exc()}
        print(name,rows[name]['probe'],flush=True)
    rows['stale_snapshot']={'probe':'observed','details':await asyncio.to_thread(stale_snapshot)}
    result={'reviewed_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'source_sha256':before,'source_unchanged':before==hashes(),'rows':rows,
            'all_observations_reproduced':all(v['probe']=='observed' for v in rows.values())}
    (OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)
    return 0 if result['all_observations_reproduced'] and result['source_unchanged'] else 1

if __name__=='__main__':raise SystemExit(asyncio.run(main()))
