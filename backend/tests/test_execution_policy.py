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

    calls=0;events=[];run={'usage':{'each:0':{'calls':1,'prompt_tokens':2,'completion_tokens':3}}}
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
    assert outcome.usage==run['usage']['each:0']
    assert events==[{
        **identity.event_fields(),'status':'retrying','transient':True,'error':'busy',
        'attempt':1,'delay_seconds':0.25,'usage':run['usage']['each:0'],
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
    assert budget=={'remaining':0,'counter':2,'ceiling':2}
