"""Independent TA01 recheck; real workers/SQLite/adapters, synthetic HTTP only."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import httpx
from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.app import providers,registry,token_budget
from backend.app.execution_policy import ExecutionIdentity
from backend.app.operator_metrics import RunTokenRecord
from backend.app.registry import Context,LLMConfig
from backend.app.storage import Store
from backend.app.worker import Worker

OUT=Path(__file__).parent
SOURCES=[ROOT/'backend/app'/name for name in ['providers.py','registry.py','compiler.py','worker.py','operator_metrics.py']]
PRESERVE=[ROOT/'evals/results/token-accounting-review-2026-10-25'/name for name in ['probe.py','results.json']]
PRESERVE += [ROOT/'evals/results/ta01-fix-2026-10-30'/name for name in ['rejected-response.json','production/report.json']]
def digest(paths):return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

def body(provider,rejected):
    text='' if rejected else 'fixture answer'
    if provider=='ollama':return {'message':{'content':text},'prompt_eval_count':100,'eval_count':50}
    if provider=='claude':return {'content':[] if rejected else [{'type':'text','text':text}],
        'stop_reason':'refusal' if rejected else 'end_turn','usage':{'input_tokens':100,'output_tokens':50}}
    return {'choices':[{'message':{'role':'assistant','content':text},'finish_reason':'length' if rejected else 'stop'}],
        'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150,
                 'completion_tokens_details':{'reasoning_tokens':50 if rejected else 0}}}

async def worker_case(provider,rejected,busy=False):
    with tempfile.TemporaryDirectory(prefix='relay-ta01-recheck-') as directory,MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','1000')
        store=Store('sqlite:///'+str(Path(directory)/'review.db'),Fernet.generate_key())
        try:
            credential=store.credential('fixture','not-a-real-key',provider=provider)['id'] if provider!='ollama' else ''
            store.allow_model(provider,'fixture',credential)
            count=0
            def handler(request):
                nonlocal count
                count+=1
                if busy and count==1:return httpx.Response(429,headers={'Retry-After':'0'})
                return httpx.Response(200,json=body(provider,rejected))
            client=httpx.AsyncClient
            mp.setattr(providers,'client',lambda timeout:client(transport=httpx.MockTransport(handler),trust_env=False))
            config={'provider':provider,'model':'fixture'}
            if credential:config['credential_id']=credential
            workflow={'version':1,'name':'Independent TA01 check','nodes':[
                {'id':'input','type':'chat_input'},
                {'id':'model','type':'llm','inputs':{'prompt':'input.message'},'config':config,
                 'retry':{'attempts':2,'base_delay':0}},
                {'id':'out','type':'response','inputs':{'text':'model.text'}}],
                'edges':[{'id':'a','source':'input','target':'model'},{'id':'b','source':'model','target':'out'}]}
            row=store.create_run(workflow,'synthetic input');worker=Worker(store)
            await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)),10)
            saved=store.run(row['id'])
            with Session(store.engine) as session:
                rows=list(session.scalars(select(RunTokenRecord).where(RunTokenRecord.run_id==row['id'])))
                sql_tokens=sum(r.prompt_tokens+r.completion_tokens for r in rows);sql_calls=sum(r.calls for r in rows)
            assert saved['status']==('failed' if rejected else 'success'),saved.get('error')
            assert saved['accounting']['tokens_spent']==sql_tokens==150
            assert sql_calls==1 and count==(2 if busy else 1)
            event=next(e for e in saved['events'] if e.get('node_id')=='model' and e['status'] in ('failed','success'))
            assert event['usage']['prompt_tokens']==100 and event['usage']['completion_tokens']==50
            assert not any(e['status']=='retrying' for e in saved['events'])
            return {'provider':provider,'rejected':rejected,'busy_first':busy,'status':saved['status'],
                    'http_attempts':count,'accounted_tokens':150,'sql_tokens':sql_tokens,'sql_calls':sql_calls}
        finally:store.engine.dispose()

async def cancellation_case():
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','1000');closing=asyncio.Event();snapshots=[]
        client=httpx.AsyncClient
        class ClosingBarrier(client):
            async def __aexit__(self,*args):
                await super().__aexit__(*args)
                closing.set()
                await asyncio.Event().wait()
        mp.setattr(providers,'client',lambda timeout:ClosingBarrier(transport=httpx.MockTransport(
            lambda request:httpx.Response(200,json=body('openai',False))),trust_env=False))
        async def persist(snapshot):snapshots.append(snapshot)
        run={'persist_spend':persist};ctx=Context('',lambda _:'fixture',node_id='model',checkpoint_owner='model',
            execution_identity=ExecutionIdentity('model','model','model'),run=run)
        task=asyncio.create_task(registry.llm_node({'prompt':'synthetic'},LLMConfig(provider='openai',model='fixture',credential_id='fixture'),ctx))
        try:
            await asyncio.wait_for(closing.wait(),5);task.cancel()
            result=await asyncio.wait_for(asyncio.gather(task,return_exceptions=True),5)
            assert isinstance(result[0],asyncio.CancelledError)
            assert token_budget.tokens_spent(run)==150 and run['usage']['model']['calls']==1 and snapshots==[]
            return {'reported_tokens':150,'accounted_tokens':150,'calls':1,'persistence_callbacks':0,'cancelled':True}
        finally:
            if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)

async def no_usage_case():
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','1000');snapshots=[];client=httpx.AsyncClient
        mp.setattr(providers,'client',lambda timeout:client(transport=httpx.MockTransport(
            lambda request:httpx.Response(200,json={'choices':[]})),trust_env=False))
        async def persist(snapshot):snapshots.append(snapshot)
        run={'persist_spend':persist};ctx=Context('',lambda _:'fixture',node_id='model',run=run)
        try:await registry.llm_node({'prompt':'synthetic'},LLMConfig(provider='openai',model='fixture',credential_id='fixture'),ctx)
        except providers.ProviderResponseError:pass
        else:raise AssertionError('Expected rejection')
        assert token_budget.tokens_spent(run)==0 and snapshots==[]
        return {'reported_usage':'unavailable','accounted_tokens':0,'persistence_callbacks':0,'scope':'unchanged TA06 policy'}

async def main():
    original=digest(PRESERVE);source=digest(SOURCES);rows=[]
    for provider in ['ollama','claude','openai']:
        for rejected in [False,True]:rows.append(await worker_case(provider,rejected))
    rows.append(await worker_case('openai',True,True))
    rows.append(await cancellation_case());rows.append(await no_usage_case())
    result={'reviewed_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'rows':rows,'source_sha256':source,'source_unchanged':source==digest(SOURCES),
            'original_evidence_unchanged':original==digest(PRESERVE),'checks_passed':len(rows)}
    assert result['source_unchanged'] and result['original_evidence_unchanged']
    (OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':asyncio.run(main())
