"""One attempt policy for graph nodes, loop bodies and agent-selected callables.

The policy layer owns attempts and retry events. Its callers own final outcome events
and decide whether an ordinary failure means fail, route, continue or a failed item.
Approval, uncertainty and cancellation never become ordinary failure outcomes.
"""
from dataclasses import dataclass
from copy import deepcopy
import asyncio
import random

from .models import RetryPolicy
from .approvals import ApprovalPause
from .tool_service import TransientToolError, UncertainWriteError
from . import token_budget


MAX_NODE_RETRY_DELAY=30.0


def retry_delay(attempt,base_delay):
    """Full jitter prevents synchronized workers retrying together."""
    return random.uniform(0,min(MAX_NODE_RETRY_DELAY,base_delay*2**min(attempt,30)))


@dataclass(frozen=True)
class ExecutionIdentity:
    node_id: str
    checkpoint_owner: str
    invocation_id: str
    parent_node_id: str | None = None
    item_index: int | None = None

    def event_fields(self,include_invocation=True):
        fields={'node_id':self.node_id}
        if include_invocation:fields['invocation_id']=self.invocation_id
        if self.parent_node_id is not None:fields['parent_node_id']=self.parent_node_id
        if self.item_index is not None:fields['item_index']=self.item_index
        return fields


@dataclass
class ExecutionOutcome:
    status: str
    outputs: dict | None
    error: str | None
    attempts: int
    usage: dict | None


def usage_for(run,identity):
    state=run or {}
    usage=state.get('usage_by_invocation',{}).get(identity.invocation_id)
    if usage is None:usage=state.get('usage',{}).get(identity.checkpoint_owner)
    return deepcopy(usage) if usage else None


def _error(exc,generic):
    return str(exc) if isinstance(exc,ValueError) else generic


def _debit_action(budget):
    if budget is None:return
    if budget['remaining']<=0:
        ceiling=budget.get('ceiling',budget.get('counter',0))
        raise ValueError(f'Run-wide agent action budget exhausted ({ceiling} actions; raise AGENT_RUN_BUDGET).')
    budget['remaining']-=1;budget['counter']+=1


async def execute_with_policy(operation,identity:ExecutionIdentity,retry:RetryPolicy,run,emit,
                              generic_error='Node execution failed.',action_budget=None,
                              charge_first_attempt=False,check_tokens=False,
                              include_invocation_in_retry=True,delay_for=None):
    """Invoke a resolved callable with common retry and budget rules.

    Only retrying events are emitted here. The caller receives a structured terminal
    outcome and is the sole owner of success/failed/paused events.
    """
    attempts=retry.attempts+1
    delay_for=delay_for or retry_delay
    for attempt in range(attempts):
        try:
            if check_tokens and token_budget.exhausted(run):raise ValueError(token_budget.reason(run))
            if charge_first_attempt or attempt>0:_debit_action(action_budget)
            outputs=await operation()
            return ExecutionOutcome('success',outputs,None,attempt+1,usage_for(run,identity))
        except (ApprovalPause,UncertainWriteError,asyncio.CancelledError):
            raise
        except Exception as exc:
            error=_error(exc,generic_error)
            transient=isinstance(exc,TransientToolError)
            if transient and attempt<attempts-1:
                delay=delay_for(attempt,retry.base_delay)
                event={**identity.event_fields(include_invocation_in_retry),'status':'retrying',
                       'transient':True,'error':error,'attempt':attempt+1,
                       'delay_seconds':round(delay,3)}
                usage=usage_for(run,identity)
                if usage:event['usage']=usage
                await emit(event)
                from .observability import journal
                journal(event='node.retry',node_id=identity.node_id,attempt=attempt+1,
                        delay_seconds=delay,error_type=type(exc).__name__)
                await asyncio.sleep(delay)
                continue
            return ExecutionOutcome('failed',None,error,attempt+1,usage_for(run,identity))


async def invoke_attached(node,input_text,ctx,identity:ExecutionIdentity,emit,depth=0,
                          action_budget=None):
    """Dispatch an attached callable from resolved input and one immutable identity."""
    from dataclasses import replace
    from .registry import REGISTRY
    from .agent_runtime import execute_agent,tool_outputs

    definition=REGISTRY[node.type]
    config=definition.config_model.model_validate(node.config)
    child=replace(ctx,node_id=node.id,node_type=node.type,
                  checkpoint_owner=identity.checkpoint_owner,
                  execution_identity=identity)
    if node.type=='agent':
        return await execute_agent(node.id,input_text,ctx.workflow,child,emit,depth,
                                   action_budget,identity.checkpoint_owner)
    if node.type.startswith('tool_'):
        if ctx.platform is None:raise ValueError('Tool execution unavailable.')
        raw=await ctx.platform('tool',node.type,config.model_dump(),input_text,identity)
        return await tool_outputs(child,node.type,config,raw,node.id)
    return await definition.handler({'query':input_text},config,child)
