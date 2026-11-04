import json
import pytest
from backend.app import token_budget
from backend.app.compiler import compile_workflow
from backend.app.token_budget import DEFAULT_RUN_TOKENS,MAX_RUN_TOKENS,run_token_limit,tokens_spent,exhausted
from backend.tests.test_agent_grounding import source,platform_with
from backend.tests.test_agent_team_budget import team_flow,grounded
from backend.tests.test_agent_tools import calc_flow


def agent_with_retrieval():
    """One agent with a retrieval tool attached directly; default max_steps of 6."""
    workflow=calc_flow()
    workflow.nodes[2].type='retrieve';workflow.nodes[2].config={'knowledge_base_id':'kb1','mode':'keyword'}
    return workflow


def spending(monkeypatch,responses,per_call=100):
    """A scripted model that also reports provider token usage, the way a real provider does."""
    from backend.app import registry
    replies=iter(responses)
    async def model(inputs,config,ctx):
        registry.account_usage(ctx,{'prompt_tokens':per_call,'completion_tokens':0},config)
        return {'text':next(replies),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)


def test_limit_setting_is_validated(monkeypatch):
    monkeypatch.delenv('RELAY_RUN_TOKEN_LIMIT',raising=False)
    assert run_token_limit()==DEFAULT_RUN_TOKENS==2_000_000
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','5000');assert run_token_limit()==5000
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','0');assert run_token_limit()==0
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','   ');assert run_token_limit()==DEFAULT_RUN_TOKENS
    for bad in ('-1','abc','1.5',str(MAX_RUN_TOKENS+1)):
        monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT',bad)
        with pytest.raises(ValueError) as exc:run_token_limit()
        assert 'RELAY_RUN_TOKEN_LIMIT' in str(exc.value)


def test_tokens_spent_sums_both_directions_and_ignores_rubbish():
    run={'usage':{'a':{'prompt_tokens':100,'completion_tokens':50},
                  'b':{'prompt_tokens':7,'completion_tokens':3},
                  'c':{'prompt_tokens':None,'completion_tokens':-5},
                  'd':'not a dict'}}
    assert tokens_spent(run)==160
    assert tokens_spent({})==0 and tokens_spent(None)==0 and tokens_spent({'usage':None})==0


def test_exhaustion_is_inclusive_and_zero_disables():
    run={'usage':{'a':{'prompt_tokens':160,'completion_tokens':0}}}
    assert exhausted(run,160) and not exhausted(run,161)
    assert not exhausted(run,0), 'zero must mean unlimited'
    assert not exhausted(None,1)


@pytest.mark.asyncio
async def test_agent_run_truncates_gracefully_when_tokens_run_out(monkeypatch):
    """Token exhaustion behaves like action exhaustion: a flagged partial answer, not a crash."""
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','100')
    spending(monkeypatch,[json.dumps({'action':'call','target':'calc','input':'q'}),
                          grounded('Finding [S1]',['S1'])],per_call=100)
    events=[]
    async def emit(event):events.append(event)
    result=await compile_workflow(agent_with_retrieval(),message='facts',platform_resolver=platform_with([source(1)],[]),emit=emit).graph.ainvoke({'values':{}})
    metadata=json.loads(result['values']['agent']['grounding'])
    assert metadata['truncated'] is True
    assert 'Run token budget exhausted' in metadata['truncation_reason']
    assert 'RELAY_RUN_TOKEN_LIMIT' in metadata['truncation_reason']
    assert 'not a monetary cost' in metadata['truncation_reason']
    warning=next(e for e in events if e.get('kind')=='agent_budget')
    assert warning['tokens_used']>=100


@pytest.mark.asyncio
async def test_token_exhaustion_is_reported_over_action_exhaustion(monkeypatch):
    """When both ceilings are hit, the operator is told about the money one."""
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','100')
    monkeypatch.setenv('AGENT_RUN_BUDGET','1')
    spending(monkeypatch,[json.dumps({'action':'call','target':'calc','input':'q'}),
                          grounded('Finding [S1]',['S1']),
                          grounded('Finding [S1]',['S1'])],per_call=100)
    result=await compile_workflow(agent_with_retrieval(),message='facts',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    metadata=json.loads(result['values']['agent']['grounding'])
    assert 'Run token budget exhausted' in metadata['truncation_reason']
    assert 'AGENT_RUN_BUDGET' not in metadata['truncation_reason']


@pytest.mark.asyncio
async def test_unlimited_setting_never_truncates_on_tokens(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','0')
    spending(monkeypatch,[json.dumps({'action':'call','target':'calc','input':'q'}),
                          grounded('Finding [S1]',['S1']),
                          grounded('Combined [S1]',['S1'])],per_call=100_000)
    result=await compile_workflow(agent_with_retrieval(),message='facts',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    metadata=json.loads(result['values']['agent']['grounding'])
    assert not metadata.get('truncated')


@pytest.mark.asyncio
async def test_plain_model_node_refuses_once_the_run_is_exhausted(monkeypatch):
    """A plain llm node has no partial-answer contract, so it refuses rather than spending more."""
    from backend.app.registry import llm_node,LLMConfig,Context
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','50')
    run={'usage':{'earlier':{'prompt_tokens':60,'completion_tokens':0}}}
    with pytest.raises(ValueError) as exc:
        await llm_node({'prompt':'Hello'},LLMConfig(provider='ollama',model='test'),Context('',lambda _:'',run=run))
    assert 'Run token budget exhausted' in str(exc.value)


@pytest.mark.asyncio
async def test_plain_model_node_runs_when_under_the_limit(monkeypatch):
    from backend.app import providers
    from backend.app.registry import llm_node,LLMConfig,Context
    import httpx
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','5000')
    original=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(
        lambda _:httpx.Response(200,json={'message':{'content':'Hi'},'prompt_eval_count':4,'eval_count':2}))))
    run={'usage':{'earlier':{'prompt_tokens':60,'completion_tokens':0}}}
    result=await llm_node({'prompt':'Hello'},LLMConfig(provider='ollama',model='test'),Context('',lambda _:'',run=run))
    assert result['text']=='Hi'
    assert tokens_spent(run)==66


# --------------------------------------------------------------------------- TA01
# A rejected answer was still paid for: llm_node counts the usage the provider reported
# whether the answer is used, rejected or interrupted, and counts it exactly once.

def scripted_chat(monkeypatch,outcomes,calls):
    """Substitute only the external chat. Each outcome reports 100 + 50 tokens, then
    returns its text or raises it; authorization, accounting and persistence stay real."""
    from backend.app import providers
    queue=list(outcomes)
    async def chat(model,system,prompt,usage=None,**params):
        calls.append(prompt);providers.record_usage(usage,100,50)
        outcome=queue.pop(0)
        if isinstance(outcome,BaseException):raise outcome
        return outcome
    monkeypatch.setattr(providers,'ollama_chat',chat)


def model_run(monkeypatch):
    from backend.app import observability
    from backend.app.execution_policy import ExecutionIdentity
    from backend.app.registry import Context
    snapshots,journal=[],[]
    async def persist(snapshot):snapshots.append(snapshot)
    monkeypatch.setattr(observability,'journal',lambda **fields:journal.append(fields))
    run={'usage':{},'usage_by_invocation':{},'persist_spend':persist}
    ctx=Context('',lambda _:'secret',node_id='model',checkpoint_owner='model',
                execution_identity=ExecutionIdentity('model','model','model'),run=run)
    return run,ctx,snapshots,journal


def counted(run):
    totals=run['usage']['model']
    return totals['calls'],totals['prompt_tokens'],totals['completion_tokens']


@pytest.mark.asyncio
async def test_a_rejected_answer_is_counted_once_and_made_durable(monkeypatch):
    from backend.app.providers import ProviderResponseError
    from backend.app.registry import llm_node,LLMConfig
    calls=[];scripted_chat(monkeypatch,[ProviderResponseError('Ollama returned no text.')],calls)
    run,ctx,snapshots,journal=model_run(monkeypatch)
    with pytest.raises(ProviderResponseError):
        await llm_node({'prompt':'Hello'},LLMConfig(provider='ollama',model='m'),ctx)
    assert counted(run)==(1,100,50)
    assert run['usage_by_invocation']['model']['calls']==1
    assert [s['tokens_spent'] for s in snapshots]==[150]
    usage=[e for e in journal if e.get('event')=='model.usage']
    assert len(usage)==1 and usage[0]['error_type']=='ProviderResponseError'


@pytest.mark.asyncio
async def test_a_cancelled_call_keeps_its_usage_in_memory_and_does_not_persist(monkeypatch):
    import asyncio
    from backend.app.registry import llm_node,LLMConfig
    calls=[];scripted_chat(monkeypatch,[asyncio.CancelledError()],calls)
    run,ctx,snapshots,journal=model_run(monkeypatch)
    with pytest.raises(asyncio.CancelledError):
        await llm_node({'prompt':'Hello'},LLMConfig(provider='ollama',model='m'),ctx)
    assert counted(run)==(1,100,50) and snapshots==[]
    usage=[e for e in journal if e.get('event')=='model.usage']
    assert len(usage)==1 and usage[0]['error_type']=='cancelled'


@pytest.mark.asyncio
async def test_a_used_answer_is_counted_exactly_once(monkeypatch):
    from backend.app.registry import llm_node,LLMConfig
    calls=[];scripted_chat(monkeypatch,['Hi'],calls)
    run,ctx,snapshots,journal=model_run(monkeypatch)
    assert (await llm_node({'prompt':'Hello'},LLMConfig(provider='ollama',model='m'),ctx))['text']=='Hi'
    assert counted(run)==(1,100,50)
    assert [s['tokens_spent'] for s in snapshots]==[150]
    usage=[e for e in journal if e.get('event')=='model.usage']
    assert len(usage)==1 and 'error_type' not in usage[0]


@pytest.mark.asyncio
async def test_a_rejected_answer_spends_the_budget_before_the_next_call(monkeypatch):
    from backend.app.providers import ProviderResponseError
    from backend.app.registry import llm_node,LLMConfig
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','150')
    calls=[];scripted_chat(monkeypatch,[ProviderResponseError('Ollama returned no text.'),'unreachable'],calls)
    run,ctx,_,_=model_run(monkeypatch)
    with pytest.raises(ProviderResponseError):
        await llm_node({'prompt':'first'},LLMConfig(provider='ollama',model='m'),ctx)
    with pytest.raises(ValueError) as exc:
        await llm_node({'prompt':'second'},LLMConfig(provider='ollama',model='m'),ctx)
    assert 'Run token budget exhausted (150 of 150' in str(exc.value)
    assert calls==['first'], 'the second call must be refused before any provider request'


@pytest.mark.asyncio
async def test_the_reviewed_openai_length_stop_counts_150_tokens(monkeypatch):
    """The exact response from the token-accounting review (TA01): HTTP 200, empty visible
    text, finish_reason length, 100 prompt and 50 completion tokens (all reasoning)."""
    import httpx
    from backend.app import providers
    from backend.app.registry import llm_node,LLMConfig
    original=httpx.AsyncClient
    body={'choices':[{'message':{'role':'assistant','content':''},'finish_reason':'length'}],
          'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150,
                   'completion_tokens_details':{'reasoning_tokens':50}}}
    monkeypatch.setattr(providers,'client',lambda timeout:original(
        transport=httpx.MockTransport(lambda _:httpx.Response(200,json=body)),trust_env=False))
    run,ctx,snapshots,_=model_run(monkeypatch)
    with pytest.raises(providers.ProviderResponseError,match='OpenAI returned no text'):
        await llm_node({'prompt':'Hello'},LLMConfig(provider='openai',model='m',credential_id='key'),ctx)
    assert tokens_spent(run)==150 and counted(run)==(1,100,50)
    assert [s['tokens_spent'] for s in snapshots]==[150]


@pytest.mark.asyncio
async def test_a_run_that_fails_on_a_rejected_answer_records_its_tokens_durably(tmp_path,monkeypatch):
    import httpx
    from cryptography.fernet import Fernet
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from backend.app import providers
    from backend.app.models import Workflow
    from backend.app.operator_metrics import RunTokenRecord
    from backend.app.storage import Store
    from backend.app.worker import Worker
    original=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(
        lambda _:httpx.Response(200,json={'message':{'role':'assistant','content':''},
                                          'prompt_eval_count':100,'eval_count':50})),trust_env=False))
    store=Store(f'sqlite:///{tmp_path}/rejected.db',Fernet.generate_key())
    try:
        store.allow_model('ollama','m','','local')
        workflow=Workflow.model_validate({'version':1,'name':'Rejected answer','nodes':[
            {'id':'input','type':'chat_input'},
            {'id':'model','type':'llm','inputs':{'prompt':'input.message'},'config':{'provider':'ollama','model':'m'}},
            {'id':'out','type':'response','inputs':{'text':'model.text'}}],
            'edges':[{'id':'a','source':'input','target':'model'},{'id':'b','source':'model','target':'out'}]})
        row=store.create_run(workflow.model_dump(mode='json'),'go')
        worker=Worker(store)
        await worker.execute(store.claim_next(worker.owner))
        saved=store.run(row['id'])
        assert saved['status']=='failed' and 'Ollama returned no text' in saved['error']
        assert saved['accounting']['tokens_spent']==150
        with Session(store.engine) as s:
            rows=s.execute(select(RunTokenRecord).where(RunTokenRecord.run_id==row['id'])).scalars().all()
        assert sum(r.prompt_tokens+r.completion_tokens for r in rows)==150 and sum(r.calls for r in rows)==1
    finally:store.engine.dispose()
