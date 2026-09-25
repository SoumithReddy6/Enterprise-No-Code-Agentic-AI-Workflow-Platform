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
