"""Audit probes: assertions describe required behavior; failures are findings."""
import json
import pytest
from backend.app.compiler import compile_workflow
from backend.app.models import RetryPolicy
from backend.tests.test_agent_tools import calc_flow
from backend.tests.test_iteration import loop_flow,issues,platform
from backend.tests.test_token_budget import spending


@pytest.mark.asyncio
async def test_tool_events_do_not_duplicate_parent_tokens(monkeypatch):
    spending(monkeypatch,['{"action":"call","target":"calc","input":"x"}',
                          '{"action":"final","text":"done"}'],per_call=10)
    events=[]
    async def emit(e):events.append(e)
    async def resolver(*_):return 'ok'
    await compile_workflow(calc_flow(),emit=emit,platform_resolver=resolver).graph.ainvoke({'values':{}})
    totals=[(e['node_id'],e['status'],e['usage']['prompt_tokens']) for e in events if e.get('usage')]
    assert sum(x[2] for x in totals)==20, totals


@pytest.mark.asyncio
async def test_loop_attempts_respect_action_ceiling(monkeypatch):
    monkeypatch.setenv('AGENT_RUN_BUDGET','1')
    resolver,calls=platform(issues(3))
    await compile_workflow(loop_flow(),platform_resolver=resolver).graph.ainvoke({'values':{}})
    assert len(calls)<=1, f'{len(calls)} body actions executed under budget 1'


@pytest.mark.asyncio
async def test_resumed_graph_restores_spent_action_budget(monkeypatch):
    import asyncio
    from langgraph.errors import NodeCancelledError
    monkeypatch.setenv('AGENT_RUN_BUDGET','1')
    workflow=calc_flow();events=[];calls=[]
    async def emit(e):
        events.append(e)
        if e.get('node_id')=='agent' and e['status']=='success':raise asyncio.CancelledError()
    async def resolver(*args):calls.append(args);return 'ok'
    # Interrupt after the first checkpoint, then resume this identical workflow.
    other=workflow.nodes[1].model_copy(deep=True);other.id='second'
    workflow.nodes.insert(2,other)
    workflow.edges[1].target='second'
    workflow.edges.append(workflow.edges[0].model_copy(update={'id':'end','source':'second','target':'out'}))
    workflow.edges.append(workflow.edges[-2].model_copy(update={'id':'attach2','source':'calc','target':'second'}))
    workflow.nodes[-1].inputs={'text':'second.text'}
    spending(monkeypatch,['{"action":"call","target":"calc","input":"first"}'],per_call=1)
    with pytest.raises((asyncio.CancelledError,NodeCancelledError)):
        await compile_workflow(workflow,emit=emit,platform_resolver=resolver).graph.ainvoke({'values':{}})
    completed={e['node_id']:e['outputs'] for e in events if e['status']=='success' and not e.get('transient')}
    completed.pop('out',None)
    spending(monkeypatch,['{"action":"call","target":"calc","input":"second"}'],per_call=1)
    await compile_workflow(workflow,completed=completed,platform_resolver=resolver).graph.ainvoke({'values':{}})
    assert len(calls)==1, f'{len(calls)} actions executed across resume with budget 1'


@pytest.mark.asyncio
async def test_retry_usage_is_not_added_twice(monkeypatch):
    from backend.app.execution_policy import ExecutionIdentity,execute_with_policy
    from backend.app.tool_service import TransientToolError
    from backend.app.registry import Context,LLMConfig,account_usage
    identity=ExecutionIdentity('query','query','query');run={};events=[];calls=0
    ctx=Context('',lambda _:'',node_id='query',checkpoint_owner='query',execution_identity=identity,run=run)
    async def operation():
        nonlocal calls
        calls+=1;account_usage(ctx,{'prompt_tokens':10},LLMConfig())
        if calls==1:raise TransientToolError('busy')
        return {'text':'ok'}
    async def emit(e):events.append(e)
    outcome=await execute_with_policy(operation,identity,RetryPolicy(attempts=1,base_delay=0),run,emit)
    reported=sum(e.get('usage',{}).get('prompt_tokens',0) for e in events)+outcome.usage['prompt_tokens']
    assert reported==20, f'{reported} reported for 20 spent tokens'


@pytest.mark.asyncio
async def test_exhausted_tokens_still_allow_no_evidence_abstention(monkeypatch):
    from backend.app.models import Workflow
    from backend.tests.test_agent_grounding import platform_with
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT','10')
    spending(monkeypatch,['{"action":"final","text":"question"}'],per_call=10)
    workflow=Workflow.model_validate({'name':'Guard after spend','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'first','type':'agent','inputs':{'input':'input.message'},'config':{'provider':'demo'}},
        {'id':'retrieve','type':'retrieve','inputs':{'query':'first.text'},'config':{'knowledge_base_id':'kb1','mode':'keyword'}},
        {'id':'second','type':'agent','inputs':{'input':'retrieve.context'},'config':{'provider':'demo'}},
        {'id':'out','type':'response','inputs':{'text':'second.text'}}],
        'edges':[{'id':str(i),'source':a,'target':b} for i,(a,b) in enumerate(zip(
            ['input','first','retrieve','second'],['first','retrieve','second','out']))]})
    result=await compile_workflow(workflow,platform_resolver=platform_with([],[])).graph.ainvoke({'values':{}})
    assert json.loads(result['values']['second']['grounding'])['abstain']
