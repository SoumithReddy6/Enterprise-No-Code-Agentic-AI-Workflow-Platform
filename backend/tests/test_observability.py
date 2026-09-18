"""Provider retries, token accounting on node events, and structured run logs."""
import json
import logging
import httpx
import pytest
from cryptography.fernet import Fernet
from backend.app import providers
from backend.app.registry import llm_node,Context,LLMConfig
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_compiler import sample

def transport(monkeypatch,handler):
    real=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:real(transport=httpx.MockTransport(handler)))

@pytest.mark.asyncio
async def test_transient_provider_failures_are_retried_with_backoff(monkeypatch):
    attempts=[];delays=[]
    def handler(request):
        attempts.append(1)
        if len(attempts)<3:return httpx.Response(503,json={'error':'loading'})
        return httpx.Response(200,json={'message':{'content':'Ready'},'prompt_eval_count':12,'eval_count':3})
    transport(monkeypatch,handler)
    async def sleep(seconds):delays.append(seconds)
    monkeypatch.setattr(providers.asyncio,'sleep',sleep)
    ctx=Context('',lambda _:'',node_id='agent',run={'evidence':[]})
    result=await llm_node({'prompt':'Q'},LLMConfig(provider='ollama',model='tiny'),ctx)
    assert result['text']=='Ready' and len(attempts)==3 and delays==[.5,1.]
    assert ctx.run['usage']['agent']=={'calls':1,'prompt_tokens':12,'completion_tokens':3}

@pytest.mark.asyncio
async def test_persistent_failure_surfaces_after_bounded_attempts(monkeypatch):
    transport(monkeypatch,lambda request:httpx.Response(429,json={'error':'slow down'}))
    async def sleep(seconds):pass
    monkeypatch.setattr(providers.asyncio,'sleep',sleep)
    with pytest.raises(ValueError,match='HTTP 429 .*after 3 attempts'):
        await llm_node({'prompt':'Q'},LLMConfig(provider='ollama',model='tiny'),Context('',lambda _:''))

@pytest.mark.asyncio
async def test_configuration_errors_are_not_retried(monkeypatch):
    attempts=[]
    def handler(request):attempts.append(1);return httpx.Response(404,json={'error':'missing'})
    transport(monkeypatch,handler)
    with pytest.raises(ValueError,match='not installed'):
        await llm_node({'prompt':'Q'},LLMConfig(provider='ollama',model='absent'),Context('',lambda _:''))
    assert len(attempts)==1

@pytest.mark.asyncio
async def test_usage_reaches_the_node_event_and_the_run_journal(tmp_path,monkeypatch,caplog):
    def handler(request):return httpx.Response(200,json={'message':{'content':'Answer'},'prompt_eval_count':40,'eval_count':7})
    transport(monkeypatch,handler)
    store=Store(f'sqlite:///{tmp_path}/obs.db',Fernet.generate_key());store.allow_model('ollama','tiny','','local')
    raw=sample();raw['nodes'][1]={'id':'prompt','type':'llm','inputs':{'prompt':'input.message'},'config':{'provider':'ollama','model':'tiny'}}
    run=store.create_run(raw,'Hello');worker=Worker(store)
    with caplog.at_level(logging.INFO,logger='relay.run'):
        await worker.execute(store.claim_next(worker.owner))
    record=store.run(run['id'])
    assert record['status']=='success'
    event=next(e for e in record['events'] if e.get('node_id')=='prompt' and e['status']=='success')
    assert event['usage']=={'calls':1,'prompt_tokens':40,'completion_tokens':7}
    lines=[json.loads(r.message) for r in caplog.records if r.name=='relay.run']
    assert [l['event'] for l in lines][0]=='run.start' and [l['event'] for l in lines][-1]=='run.finish'
    assert all(l['run_id']==run['id'] for l in lines)
    node_line=next(l for l in lines if l['event']=='node.success' and l['node_id']=='prompt')
    assert node_line['usage']['completion_tokens']==7 and 'outputs' not in node_line
    assert lines[-1]['status']=='success' and lines[-1]['seconds']>=0
    store.engine.dispose()

@pytest.mark.asyncio
async def test_ollama_timeout_is_retried(monkeypatch):
    attempts=[]
    def handler(request):
        attempts.append(1)
        if len(attempts)<3:raise httpx.ReadTimeout('slow',request=request)
        return httpx.Response(200,json={'message':{'content':'Recovered'}})
    transport(monkeypatch,handler)
    async def sleep(seconds):pass
    monkeypatch.setattr(providers.asyncio,'sleep',sleep)
    result=await llm_node({'prompt':'Q'},LLMConfig(provider='ollama',model='mock'),Context('',lambda _:''))
    assert result['text']=='Recovered' and len(attempts)==3
