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
from .storage import LeaseLost

# Outcomes that are not failures of the attempted work, and so must never be retried,
# recovered, or turned into an observation an agent can plan around. Every handler near
# the policy seam re-raises these before any generic clause. It is one exported tuple
# because four defects so far came from a hand-maintained list missing a member.
CONTROL_FLOW=(ApprovalPause,UncertainWriteError,asyncio.CancelledError,LeaseLost)
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
    # The run-wide action budget refused an attempt, so that attempt made no call.
    # attempts counts only the attempts that actually executed before the refusal.
    budget_exhausted: bool = False


class ActionBudgetExhausted(ValueError):
    """The run-wide action budget refused an attempt before it could execute."""


# A node reports that the run did not deliver all its work under this key in its graph
# value. It is not an output: registry.check_port_names rejects any port starting with
# registry.RESERVED_PORT_PREFIX, and binding validation refuses one, so it can never be
# declared or bound. It is read only when the run's completeness is decided.
TRUNCATION_KEY='_truncation'


def budget_truncation(node_id,outcome):
    """The confirmed truncation a node recovering from budget exhaustion reports."""
    if outcome.attempts:
        reason=f'{node_id} stopped after {outcome.attempts} attempt(s): {outcome.error}'
    else:
        reason=f'{node_id} did not run: {outcome.error}'
    return {'truncated':True,'truncation_reason':reason,'truncation_source':'confirmed'}


def accounting_snapshot(run):
    """Run-wide spend, for durable persistence beside checkpoints and loop progress.

    Recorded as it is spent rather than at the end, so an interrupted run resumes with
    its budget already consumed instead of silently replenished.
    """
    state=run or {}
    from .token_budget import tokens_spent
    return {'action_budget':deepcopy(state.get('action_budget') or {}),
            'tokens_spent':tokens_spent(state),
            'usage':deepcopy(state.get('usage') or {}),
            'usage_by_invocation':deepcopy(state.get('usage_by_invocation') or {})}


def usage_for(run,identity):
    """Tokens this invocation spent, or None.

    There is deliberately no fallback to the checkpoint owner's total. Absent usage is
    the normal case for a tool that calls no model, and inheriting the parent's running
    total would report the parent's spend twice: once on the tool event and again on
    the parent's own outcome.
    """
    usage=(run or {}).get('usage_by_invocation',{}).get(identity.invocation_id)
    return deepcopy(usage) if usage else None


def _error(exc,generic):
    return str(exc) if isinstance(exc,ValueError) else generic


ACTION_BEARING={'tool_http','tool_email','tool_jira','tool_confluence','tool_github',
                'tool_python','retrieve','query'}


def charges_action(node_type):
    """Whether running this node costs the run-wide action budget.

    Two budgets, two kinds of spend, no overlap:

    * AGENT_RUN_BUDGET counts actions - tool calls, knowledge-base retrieval and query,
      and specialist delegation. First attempts and retries are both charged; replaying
      an attempt already paid for is not.
    * RELAY_RUN_TOKEN_LIMIT counts model spend - every model call, wherever it comes
      from: a standalone LLM node, an agent's planning step, a structured-output repair,
      query generation.

    So an LLM node is not an action, and neither is an agent: the agent is a container
    whose tool calls and delegations are charged individually, and whose planning calls
    are model spend. Charging model calls as actions as well would bill the same call
    twice and make a standalone LLM node cost something an agent's identical call did not.
    """
    return node_type in ACTION_BEARING


def _debit_action(budget,key=None):
    """Charge one action. Always charges: whether an attempt is free is decided by the
    caller from what actually happens, not from whether its key was seen before.

    A key already seen does not mean the attempt is free. A read that ran, then crashed
    before its result was recorded, executes again for real on resume and must be charged
    again. Only an attempt served from a stored result, or one whose reservation was
    deferred by an approval pause, is free - and execute_with_policy decides that.
    """
    if budget is None:return False
    if budget['remaining']<=0:
        ceiling=budget.get('ceiling',budget.get('counter',0))
        raise ActionBudgetExhausted(f'Run-wide agent action budget exhausted ({ceiling} actions; raise AGENT_RUN_BUDGET).')
    budget['remaining']-=1;budget['counter']+=1
    if key is not None:budget.setdefault('charged',[]).append(key)
    return True


async def execute_with_policy(operation,identity:ExecutionIdentity,retry:RetryPolicy,run,emit,
                              generic_error='Node execution failed.',action_budget=None,
                              charge_first_attempt=False,
                              include_invocation_in_retry=True,delay_for=None,free_outcome=None):
    """Invoke a resolved callable with common retry and budget rules.

    Only retrying events are emitted here. The caller receives a structured terminal
    outcome and is the sole owner of success/failed/paused events.

    free_outcome resolves results that cost nothing - a guard refusal, an abstention with
    no evidence - before any budget check. Refusing to answer must not require budget the
    run has already spent, or an exhausted run could not even decline.

    The token ceiling is deliberately not enforced here. It is checked where spending
    happens - before a provider call in llm_node, and before each agent step - which is
    after every free path has had its chance to resolve. Checking it at this seam would
    block an abstention that needs no model call at all.
    """
    if free_outcome is not None:
        free=await free_outcome()
        if free is not None:return ExecutionOutcome('success',free,None,0,usage_for(run,identity))
    attempts=retry.attempts+1
    delay_for=delay_for or retry_delay
    for attempt in range(attempts):
        try:
            if charge_first_attempt or attempt>0:
                key=f'{identity.invocation_id}#{attempt}'
                settled=(run or {}).get('action_settled')
                deferred=(action_budget or {}).get('deferred',[])
                if settled is not None and await settled(identity.invocation_id):
                    # Served from its stored result: nothing executes, nothing is charged.
                    debited=False
                elif key in deferred:
                    # Reserved before an approval pause and never executed; the approved
                    # execution uses that reservation rather than taking a second one.
                    deferred.remove(key);debited=False
                else:
                    debited=_debit_action(action_budget,key)
                # Reserve before spending: the debit is durable before the external call
                # runs, so a crash in between leaves the budget consumed rather than
                # letting a resumed run spend it again. The same discipline as login
                # budgets, which reserve a slot before the expensive hash.
                persist=(run or {}).get('persist_accounting')
                if debited and persist is not None:await persist(accounting_snapshot(run))
            outputs=await operation()
            return ExecutionOutcome('success',outputs,None,attempt+1,usage_for(run,identity))
        except CONTROL_FLOW:
            raise
        except ActionBudgetExhausted as exc:
            return ExecutionOutcome('failed',None,str(exc),attempt,usage_for(run,identity),budget_exhausted=True)
        except Exception as exc:
            error=_error(exc,generic_error)
            transient=isinstance(exc,TransientToolError)
            if transient and attempt<attempts-1:
                delay=delay_for(attempt,retry.base_delay)
                # Attempt metadata only. Spend is reported once, on the terminal
                # outcome; repeating a cumulative total here counts it twice.
                event={**identity.event_fields(include_invocation_in_retry),'status':'retrying',
                       'transient':True,'error':error,'attempt':attempt+1,
                       'delay_seconds':round(delay,3)}
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
