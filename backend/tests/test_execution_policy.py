import json
"""The common invocation seam owns attempts; callers own final outcomes."""
import asyncio
from dataclasses import FrozenInstanceError

import pytest

from backend.app.models import RetryPolicy
from backend.app.approvals import ApprovalPause
from backend.app.tool_service import TransientToolError, UncertainWriteError


def test_execution_identity_is_frozen_and_has_one_event_shape():
    from backend.app.execution_policy import ExecutionIdentity

    identity=ExecutionIdentity(node_id='send',checkpoint_owner='each:3',
                               invocation_id='each:3:send',parent_node_id='each',item_index=3)
    assert identity.event_fields()=={
        'node_id':'send','invocation_id':'each:3:send',
        'parent_node_id':'each','item_index':3,
    }
    with pytest.raises(FrozenInstanceError):identity.invocation_id='changed'


@pytest.mark.asyncio
async def test_shared_policy_emits_only_retry_events_and_returns_usage(monkeypatch):
    from backend.app import execution_policy
    from backend.app.execution_policy import ExecutionIdentity,execute_with_policy

    spend={'calls':1,'prompt_tokens':2,'completion_tokens':3}
    # Usage is read per invocation. The owner-keyed total is deliberately different here:
    # inheriting it would report the parent's spend on this child's event as well.
    calls=0;events=[];run={'usage_by_invocation':{'each:0:work':spend},
                           'usage':{'each:0':{'calls':9,'prompt_tokens':99,'completion_tokens':99}}}
    async def operation():
        nonlocal calls
        calls+=1
        if calls==1:raise TransientToolError('busy')
        return {'text':'ok'}
    async def emit(event):events.append(event)
    async def no_sleep(_):pass
    monkeypatch.setattr(execution_policy.asyncio,'sleep',no_sleep)
    monkeypatch.setattr(execution_policy,'retry_delay',lambda *_:0.25)

    identity=ExecutionIdentity('work','each:0','each:0:work','each',0)
    outcome=await execute_with_policy(operation,identity,RetryPolicy(attempts=2,base_delay=1),
                                      run,emit,generic_error='Item execution failed.')

    assert outcome.status=='success' and outcome.outputs=={'text':'ok'} and outcome.attempts==2
    assert outcome.usage==spend, 'usage comes from the invocation, never the owner total'
    # Retry events carry attempt metadata and no spend: the terminal outcome reports it once.
    assert all('usage' not in event for event in events)
    assert events==[{
        **identity.event_fields(),'status':'retrying','transient':True,'error':'busy',
        'attempt':1,'delay_seconds':0.25,
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize('raised',[
    asyncio.CancelledError(),UncertainWriteError('unknown'),
    ApprovalPause('send','each:0','each:0:send',{'payload':{}}),
])
async def test_shared_policy_never_turns_control_flow_into_a_failed_outcome(raised):
    from backend.app.execution_policy import ExecutionIdentity,execute_with_policy

    async def operation():raise raised
    with pytest.raises(type(raised)):
        await execute_with_policy(operation,ExecutionIdentity('work','work','work'),
                                  RetryPolicy(attempts=3,base_delay=0),{},lambda _:None)


@pytest.mark.asyncio
async def test_retry_attempts_debit_the_supplied_action_budget(monkeypatch):
    from backend.app import execution_policy
    from backend.app.execution_policy import ExecutionIdentity,execute_with_policy

    budget={'remaining':2,'counter':0,'ceiling':2};calls=0
    async def operation():
        nonlocal calls
        calls+=1
        if calls<3:raise TransientToolError('busy')
        return {'text':'ok'}
    async def no_sleep(_):pass
    monkeypatch.setattr(execution_policy.asyncio,'sleep',no_sleep)
    monkeypatch.setattr(execution_policy,'retry_delay',lambda *_:0)
    async def emit(_):pass
    outcome=await execute_with_policy(operation,ExecutionIdentity('work','work','work'),
                                      RetryPolicy(attempts=2,base_delay=0),{},emit,
                                      action_budget=budget,charge_first_attempt=False)
    assert outcome.status=='success' and calls==3
    assert {k:budget[k] for k in ('remaining','counter','ceiling')}=={'remaining':0,'counter':2,'ceiling':2}
    # The first attempt was free here; each retry is its own key, so replaying either
    # after a resume would not be charged again.
    assert budget['charged']==['work#1','work#2']


# --------------------------------------------------------------------------- S07-01

def two_agent_flow():
    """input -> agent -> second -> out, both agents sharing one attached tool."""
    from backend.tests.test_agent_tools import calc_flow
    workflow=calc_flow()
    other=workflow.nodes[1].model_copy(deep=True);other.id='second'
    workflow.nodes.insert(2,other)
    workflow.edges[1].target='second'
    workflow.edges.append(workflow.edges[0].model_copy(update={'id':'end','source':'second','target':'out'}))
    workflow.edges.append(workflow.edges[-2].model_copy(update={'id':'attach2','source':'calc','target':'second'}))
    workflow.nodes[-1].inputs={'text':'second.text'}
    return workflow


@pytest.mark.asyncio
async def test_resume_continues_the_run_budget_rather_than_replenishing_it(tmp_path,monkeypatch):
    """A resumed run must not get a fresh ceiling. Two agents share a budget of one; the
    run is killed after the first agent spends it; the resumed second agent must be
    refused rather than spending an action the run no longer has."""
    import asyncio
    from cryptography.fernet import Fernet
    from backend.app import registry
    from backend.app.storage import Store
    from backend.app.worker import Worker
    monkeypatch.setenv('AGENT_RUN_BUDGET','1')
    store=Store(f'sqlite:///{tmp_path}/resume.db',Fernet.generate_key());worker=Worker(store)
    calls=[]
    async def execute(node_type,settings,text,tenant_id='local'):calls.append(text);return 'ok'
    monkeypatch.setattr(worker.tools,'execute',execute)
    replies={'agent':iter(['{"action":"call","target":"calc","input":"first"}','{"action":"final","text":"a"}']),
             'second':iter(['{"action":"call","target":"calc","input":"second"}','{"action":"final","text":"b"}',
                            json.dumps({'answer':'','citations':[],'abstain':True,'reason':'budget'})])}
    async def model(inputs,config,ctx):
        return {'text':next(replies[ctx.checkpoint_owner]),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)

    original=store.worker_event
    def die_after_first_agent(run_id,owner,event):
        written=original(run_id,owner,event)
        if event.get('node_id')=='agent' and event.get('status')=='success':raise asyncio.CancelledError()
        return written
    monkeypatch.setattr(store,'worker_event',die_after_first_agent)

    row=store.create_run(two_agent_flow().model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))
    interrupted=store.run(row['id'])
    assert calls==['first']
    spent=interrupted['accounting']['action_budget']
    assert spent['counter']==1 and spent['remaining']==0, f'the debit must be durable before the call: {spent}'

    monkeypatch.setattr(store,'worker_event',original)
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    assert calls==['first'], f'the resumed run spent past its ceiling: {calls}'


# --------------------------------------------------------------------------- reservation boundaries

@pytest.mark.asyncio
async def test_a_failed_reservation_prevents_the_external_call(tmp_path,monkeypatch):
    """If the debit cannot be made durable - lease lost, database down - the action must
    never start. A reservation that silently no-ops is worse than none."""
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from backend.tests.test_node_error_policy import flow
    store=Store(f'sqlite:///{tmp_path}/lease.db',Fernet.generate_key());worker=Worker(store)
    calls=[]
    async def execute(node_type,settings,text,tenant_id='local'):calls.append(text);return 'ok'
    monkeypatch.setattr(worker.tools,'execute',execute)
    from backend.app.storage import LeaseLost
    def lost_lease(*args,**kwargs):raise LeaseLost('Execution lease is no longer owned.')
    monkeypatch.setattr(store,'reserve_accounting',lost_lease)
    row=store.create_run(flow().model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))
    assert calls==[], 'the external call ran without a durable reservation'
    assert store.run(row['id'])['status']=='failed'


def test_reservation_raises_when_the_lease_is_not_owned(tmp_path):
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    store=Store(f'sqlite:///{tmp_path}/fence.db',Fernet.generate_key())
    row=store.create_run({'version':1,'name':'x','nodes':[],'edges':[]},'go')
    from backend.app.storage import LeaseLost
    with pytest.raises(LeaseLost,match='lease'):
        store.reserve_accounting(row['id'],'somebody-else',{'action_budget':{'counter':1}})


@pytest.mark.asyncio
async def test_model_tokens_are_durable_before_the_node_finishes(tmp_path,monkeypatch):
    """Tokens are only known after the provider responds, so they cannot be reserved in
    advance - they must be recorded the moment the call returns. Otherwise a crash
    before the node's success event hands the resumed run back tokens it already spent."""
    import asyncio
    from cryptography.fernet import Fernet
    from backend.app import providers
    from backend.app.models import Workflow
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store=Store(f'sqlite:///{tmp_path}/tokens.db',Fernet.generate_key());worker=Worker(store)
    store.allow_model('ollama','m','','local')
    async def fake_chat(model,system,prompt,usage=None,**kwargs):
        providers.record_usage(usage,10,5);return 'answer'
    monkeypatch.setattr(providers,'ollama_chat',fake_chat)
    llm={'provider':'ollama','model':'m'}
    workflow=Workflow.model_validate({'version':1,'name':'Two calls','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'first','type':'llm','inputs':{'prompt':'input.message'},'config':llm},
        {'id':'second','type':'llm','inputs':{'prompt':'first.text'},'config':llm},
        {'id':'out','type':'response','inputs':{'text':'second.text'}}],
        'edges':[{'id':'a','source':'input','target':'first'},{'id':'b','source':'first','target':'second'},
                 {'id':'c','source':'second','target':'out'}]})
    original=store.worker_event
    def die_before_first_success(run_id,owner,event):
        if event.get('node_id')=='first' and event.get('status')=='success':raise asyncio.CancelledError()
        return original(run_id,owner,event)
    monkeypatch.setattr(store,'worker_event',die_before_first_success)
    row=store.create_run(workflow.model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))
    usage=store.run(row['id']).get('accounting',{}).get('usage',{})
    spent=sum(u.get('prompt_tokens',0)+u.get('completion_tokens',0) for u in usage.values())
    assert spent==15, f'the first call spent 15 tokens but only {spent} were made durable'


# --------------------------------------------------------------------------- budget policy

def test_actions_and_model_spend_are_separate_budgets():
    """Every paid call is governed by exactly one budget, by kind rather than by caller."""
    from backend.app.execution_policy import charges_action
    for tool in ('tool_http','tool_email','tool_jira','tool_confluence','tool_github','tool_python'):
        assert charges_action(tool),tool
    assert charges_action('retrieve') and charges_action('query')
    # Model calls are token spend. A standalone LLM node must not cost something an agent's
    # identical planning call does not.
    assert not charges_action('llm')
    # An agent is a container: its tool calls and delegations are charged individually.
    assert not charges_action('agent')
    for free in ('chat_input','manual_input','prompt','condition','response','for_each'):
        assert not charges_action(free),free


@pytest.mark.asyncio
async def test_an_agents_planning_calls_spend_tokens_not_actions(monkeypatch):
    """One tool call, two planning calls: one action, all tokens."""
    from backend.app import registry
    from backend.app.compiler import compile_workflow
    from backend.tests.test_agent_tools import calc_flow
    replies=iter(['{"action":"call","target":"calc","input":"2+2"}','{"action":"final","text":"4"}'])
    async def model(inputs,config,ctx):
        registry.account_usage(ctx,{'prompt_tokens':10,'completion_tokens':0},config)
        return {'text':next(replies),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    async def resolver(*args):return '4'
    sink={}
    await compile_workflow(calc_flow(),platform_resolver=resolver,accounting_sink=sink).graph.ainvoke({'values':{}})
    assert sink['action_budget']['counter']==1, 'only the tool call is an action'
    spent=sum(u.get('prompt_tokens',0) for u in sink['usage'].values())
    assert spent==20, 'both planning calls are model spend'
