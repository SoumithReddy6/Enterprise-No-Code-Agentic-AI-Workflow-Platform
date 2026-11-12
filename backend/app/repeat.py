"""Repeat: run an attached body until a typed stop rule holds, with a fixed upper limit.

The loop is a node, not an edge that goes back, so the workflow graph stays acyclic. Like
For each, the body (and the optional checker) run inside this handler as attached callables,
and each iteration is one saved row in run_loop_items:

* a fully evaluated iteration: {index, status 'success', value, verdict, met};
* an iteration stopped by a run-wide budget: {index, status 'interrupted', budget_halt, calls};
* an iteration stopped by an answerability-guard abstention: {index, status 'abstained', value},
  where value is the refusal that the body or the checker returned.

A resumed run reuses the saved rows and checks each one again. One function,
repeat_outcome, derives the node's outputs from the rows, on the fresh path and when a
checkpointed Repeat is restored. Design: docs/superpowers/specs/2026-11-09-bounded-loops-design.md.
"""
import asyncio
import json
from decimal import Decimal
from typing import Literal

from pydantic import Field

from .models import StrictModel
from .conditions import ConditionConfig, ORDERING, evaluate, read_field

# The largest saved iteration entry, in serialized UTF-8 bytes. A value is never shortened:
# a shortened value would give a different next input after a resume. Ten entries stay
# within the 2 MB that For each allows for a results list.
MAX_ENTRY_BYTES = 200_000
MAX_ITERATIONS = 10
PREVIEW_CHARS = 2000


class RepeatConfig(StrictModel):
    body: str = Field(default='', max_length=64)
    check: str = Field(default='', max_length=64)
    max_iterations: int = Field(default=3, ge=1, le=MAX_ITERATIONS)
    until: ConditionConfig
    on_limit: Literal['fail', 'last'] = 'fail'


def restore_refused(node_id):
    return f'{node_id} cannot be restored: a saved iteration no longer matches its stop rule. Start a new run.'


def sources_refused(owner):
    return f'{owner}: saved knowledge sources are unavailable or removed. Start a new run with current evidence.'


def owner(node_id, index):
    """The checkpoint owner of one iteration, shared by its body and its checker."""
    return f'{node_id}:{index}'


# --------------------------------------------------------------------------- the typed verdict

def rule_type(until):
    """The JSON type a checker's verdict field must have for this stop rule, or None (empty)."""
    if until.operator == 'empty':
        return None
    if until.operator in ORDERING or until.compare_as == 'number':
        return 'number'
    if until.operator in ('eq', 'ne') and until.compare_to.strip().casefold() in ('true', 'false'):
        return 'boolean'
    return 'string'


SCHEMA_TYPES = {'boolean': ('boolean',), 'number': ('number', 'integer'), 'string': ('string',)}


def schema_requires(schema, path, kind):
    """Whether `schema` requires the dotted `path` with a property type that matches `kind`."""
    for key in path.split('.'):
        if not isinstance(schema, dict) or schema.get('type') != 'object':
            return False
        required, properties = schema.get('required'), schema.get('properties')
        if not isinstance(required, list) or key not in required or not isinstance(properties, dict) or key not in properties:
            return False
        schema = properties[key]
    return isinstance(schema, dict) and schema.get('type') in SCHEMA_TYPES[kind]


def json_kind(value):
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float, Decimal)):
        return 'number'
    if isinstance(value, str):
        return 'string'
    return None


class VerdictTypeError(ValueError):
    """A checker's verdict field has the wrong JSON type. On restore it keeps this message."""


def judge(node_id, number, verdict, config):
    """Apply the stop rule to one fully evaluated iteration's verdict text.

    With a checker, the verdict field's JSON type is checked first, so that true and "true"
    are not the same answer. A verdict that cannot be compared is an error, never "not met".
    """
    if config.check and config.until.field:
        kind = rule_type(config.until)
        try:
            value = read_field(verdict, config.until.field)
        except ValueError:
            value = None  # conditions.evaluate reports the same problem with its own message.
        else:
            if kind is not None and json_kind(value) != kind:
                raise VerdictTypeError(f'{node_id}: iteration {number}: the verdict field {config.until.field} must be a JSON {kind}.')
    try:
        return evaluate(verdict, config.until)
    except ValueError as exc:
        raise ValueError(f'{node_id}: iteration {number}: {exc}') from None


# --------------------------------------------------------------------------- rows and outcome

SUCCESS_KEYS = {'index', 'status', 'value', 'verdict', 'met'}
HALT_KEYS = {'index', 'status', 'budget_halt', 'calls'}
ABSTAIN_KEYS = {'index', 'status', 'value'}
# The guard decision that a Repeat stopped by an abstention carries in its summary, so that a
# downstream model node refuses too (answerability_guard.from_outputs reads it there).
GUARD_DECISION = {'decision': 'abstain', 'abstention_source': 'answerability_guard'}
# The node types that the compiler's upstream guard refuses (compiler guard_refusal).
MODEL_TYPES = ('llm', 'agent', 'query')


def checked_rows(node_id, done, config):
    """The saved rows of an earlier attempt, in order, each checked again.

    Valid rows are 0 … n-1 fully evaluated with no gap, plus at most one interrupted row at
    index n, within max_iterations; the last row may instead be a guard abstention. Each verdict is judged again (type check, then the stop
    rule); a result that differs from the saved `met`, or a `met` row that is not the last
    row, refuses the restore. A verdict type error keeps its own message.
    """
    from .iteration import HALT_REASONS
    if not done:
        return []
    if sorted(done) != list(range(len(done))) or len(done) > config.max_iterations:
        raise ValueError(restore_refused(node_id))
    rows = [done[index] for index in range(len(done))]
    for index, row in enumerate(rows):
        last = index == len(rows) - 1
        if not isinstance(row, dict) or row.get('index') != index:
            raise ValueError(restore_refused(node_id))
        if row.get('status') == 'interrupted':
            calls = row.get('calls')
            if (set(row) != HALT_KEYS or not last or row.get('budget_halt') not in HALT_REASONS
                    or type(calls) is not int or not 0 <= calls <= (2 if config.check else 1)):
                raise ValueError(restore_refused(node_id))
            continue
        if row.get('status') == 'abstained':
            if set(row) != ABSTAIN_KEYS or not last or not isinstance(row['value'], str):
                raise ValueError(restore_refused(node_id))
            continue
        if (row.get('status') != 'success' or set(row) != SUCCESS_KEYS or not isinstance(row['value'], str)
                or not isinstance(row['verdict'], str) or type(row['met']) is not bool):
            raise ValueError(restore_refused(node_id))
        if not config.check and row['verdict'] != row['value']:
            raise ValueError(restore_refused(node_id))
        try:
            met = judge(node_id, index + 1, row['verdict'], config)
        except VerdictTypeError:
            raise
        except ValueError:
            raise ValueError(restore_refused(node_id)) from None
        if met != row['met'] or (met and not last):
            raise ValueError(restore_refused(node_id))
    return rows


def budget_error(kind, message):
    """The Repeat's budget failure keeps its budget class, so node recovery reports it."""
    from .execution_policy import ActionBudgetExhausted
    from .token_budget import TokenBudgetExhausted
    return TokenBudgetExhausted(message) if kind == 'tokens' else ActionBudgetExhausted(message)


def repeat_outcome(node_id, rows, config):
    """The outcome fields the rows decide: text, feedback, summary and, for an incomplete
    result, the reserved truncation key. None while the loop has not finished.

    Raises the Repeat's error when the outcome is a failure: the limit under on_limit
    'fail', or a budget stop under 'fail' or with no fully evaluated iteration to return.
    A guard abstention is a complete answer: the refusal, not an earlier draft.
    """
    from .iteration import HALT_REASONS
    from .execution_policy import TRUNCATION_KEY, truncation
    evaluated = [row for row in rows if row['status'] == 'success']
    halt = rows[-1] if rows and rows[-1]['status'] == 'interrupted' else None
    last = evaluated[-1] if evaluated else None
    limit = config.max_iterations
    reason = ''
    if rows and rows[-1]['status'] == 'abstained':
        summary = {'iterations': len(evaluated), 'max_iterations': limit, 'met': False, 'stopped_by': 'guard',
                   'truncated': False, 'truncation_reason': '', 'answerability': dict(GUARD_DECISION)}
        return {'text': rows[-1]['value'], 'feedback': '', 'summary': json.dumps(summary)}
    if halt is not None:
        number, tail = halt['index'] + 1, HALT_REASONS[halt['budget_halt']]
        if config.on_limit == 'fail' or last is None:
            raise budget_error(halt['budget_halt'], f'{node_id}: the run budget stopped iteration {number} before it was checked. {tail}')
        stopped = 'budget'
        reason = f'{node_id}: the run budget stopped iteration {number} before it was checked; the answer from iteration {number - 1} is returned. {tail}'
    elif last is not None and last['met']:
        stopped = 'rule'
    elif len(evaluated) >= limit:
        if config.on_limit == 'fail':
            raise ValueError(f'{node_id}: the stop rule was not met after {limit} iterations.')
        stopped = 'limit'
        reason = f'{node_id} reached {limit} iterations without meeting its stop rule; the last answer is returned.'
    else:
        return None
    summary = {'iterations': len(evaluated), 'max_iterations': limit, 'met': stopped == 'rule',
               'stopped_by': stopped, 'truncated': bool(reason), 'truncation_reason': reason}
    outputs = {'text': last['value'], 'feedback': last['verdict'] if config.check else '', 'summary': json.dumps(summary)}
    if reason:
        outputs[TRUNCATION_KEY] = truncation(reason)
    return outputs


# --------------------------------------------------------------------------- saved sources

async def verify_owners(ctx, owners):
    """Check the saved knowledge sources of restored iterations before their text is used."""
    from .execution_policy import CONTROL_FLOW
    from .platform_nodes import source_list
    from .request_safety import UnsafeExecutionError
    saved = (ctx.run or {}).get('saved_dependencies') or {}
    for name in owners:
        if name not in saved:
            continue
        try:
            sources = source_list(saved[name])
            if sources:
                if ctx.platform is None:
                    raise ValueError('Knowledge source verification is unavailable.')
                await ctx.platform('verify_vector_sources', sources)
        except CONTROL_FLOW:
            raise
        except (KeyError, ValueError, TypeError):
            raise UnsafeExecutionError(sources_refused(name)) from None


def reuse(ctx, node_id, rows):
    """Reused iterations: the Response checks their saved sources again."""
    if ctx.run is not None:
        ctx.run.setdefault('reused_owners', set()).update(owner(node_id, row['index']) for row in rows)


async def restored_outputs(ctx, node, config, outputs):
    """Check a checkpointed Repeat before its outputs are used (the compiler's restore branch).

    The checkpoint's outcome fields must equal what repeat_outcome derives from the saved
    rows, key by key, including the truncation key present or absent. _provenance is
    checked separately by the compiler; any other key is a mismatch. Then the saved sources
    of every iteration are checked again.
    """
    from .iteration import completed_indices
    from .provenance import PROVENANCE_KEY
    rows = checked_rows(node.id, completed_indices(ctx.run, node.id), config)
    try:
        derived = repeat_outcome(node.id, rows, config)
    except ValueError:
        derived = None
    saved = {key: value for key, value in outputs.items() if key != PROVENANCE_KEY} if isinstance(outputs, dict) else None
    if derived is None or saved != derived:
        raise ValueError(restore_refused(node.id))
    await verify_owners(ctx, [owner(node.id, row['index']) for row in rows])
    reuse(ctx, node.id, rows)
    return outputs


# --------------------------------------------------------------------------- the handler

def entry_bytes(entry):
    return len(json.dumps(entry, ensure_ascii=False).encode('utf-8'))


def result_of(node_id, number, role, node, output):
    """The result of the body or the checker, from its declared port. A missing port fails
    the Repeat: an empty string would read as an empty draft and could meet the rule."""
    from .iteration import result_port
    port = result_port(node.type)
    value = output.get(port) if isinstance(output, dict) else None
    if not isinstance(value, str):
        raise ValueError(f"{node_id}: iteration {number}: the {role}'s {port} output is missing.")
    return value


def guard_abstained(output):
    """Whether this output is an answerability-guard abstention: a Retrieve or Query decision
    (answerability_guard.from_outputs), or an Agent that honoured one (its grounding)."""
    from .answerability_guard import from_outputs
    decision = from_outputs(output)
    if decision is not None and decision['decision'] == 'abstain':
        return True
    try:
        return json.loads(output.get('grounding') or '{}').get('abstention_source') == 'answerability_guard'
    except (TypeError, ValueError, AttributeError):
        return False


def budget_kind(output):
    """'tokens' or 'action' when a run-wide budget made this output incomplete, '' when it is
    complete, and None when it is incomplete for another reason (steps, a capped tool)."""
    from .execution_policy import TRUNCATION_KEY
    from .iteration import body_truncation
    if not body_truncation(output):
        return ''
    tool = output.get(TRUNCATION_KEY)
    if isinstance(tool, dict) and tool.get('truncated'):
        return None
    try:
        records = json.loads(output.get('grounding') or '{}').get('truncations') or []
        kind = records[-1].get('kind') if isinstance(records[-1], dict) else None
    except (TypeError, ValueError, AttributeError, IndexError):
        return None
    return kind if kind in ('tokens', 'action') else None


async def repeat_node(inputs, config, ctx):
    from . import exact_json
    from .iteration import completed_indices, record_progress
    node_id = ctx.node_id
    nodes = {n.id: n for n in (ctx.workflow.nodes if ctx.workflow else [])}
    body = nodes.get(config.body)
    checker = nodes.get(config.check) if config.check else None
    if body is None or (config.check and checker is None):
        raise ValueError(f'{node_id} has no attached body to run.')
    async def silent(event):
        pass
    emit = ctx.emit or silent
    source = inputs['input']

    rows = checked_rows(node_id, completed_indices(ctx.run, node_id), config)
    restored = [owner(node_id, row['index']) for row in rows]
    reuse(ctx, node_id, rows)
    verified = False
    while True:
        outcome = repeat_outcome(node_id, rows, config)
        if outcome is not None:
            # Check the restored sources again at the return, also when they were checked
            # before this attempt's body ran: a source can be removed while the body runs, and
            # a fallback ('last' after a budget stop) returns the restored draft itself.
            if restored:
                await verify_owners(ctx, restored)
            return outcome
        if restored and not verified:
            # The next body receives the saved draft and feedback.
            await verify_owners(ctx, restored)
            verified = True
        index = len(rows)
        previous = rows[-1] if rows else None
        text = source if previous is None else exact_json.dumps(
            {'input': source, 'previous': previous['value'], 'feedback': previous['verdict'] if config.check else ''}, compact=True)
        if ctx.upstream_abstained and (checker is not None or body.type in MODEL_TYPES):
            # A guarded retrieval upstream abstained and this loop would call a model: it
            # refuses before any call, as a standalone model node does, and the refusal is
            # saved as the guard row so that a resume returns it without a call.
            from .agent_runtime import immediate_abstention
            entry = {'index': index, 'status': 'abstained', 'value': immediate_abstention(GUARD_DECISION)['text']}
        else:
            entry = await _iteration(ctx, config, body, checker, index, source, text, emit)
        if entry['status'] != 'interrupted' and entry_bytes(entry) > MAX_ENTRY_BYTES:
            raise ValueError(f'{node_id}: iteration {index + 1} output is too large to save (limit {MAX_ENTRY_BYTES} bytes).')
        # Every budget stop is saved too, with no charge: the row commits with the accounting.
        record_progress(ctx, index, entry)
        await emit({'kind': 'loop_item', 'status': 'info', 'node_id': node_id, 'item_index': index,
                    'item_result': entry, 'transient': True})
        rows.append(entry)


async def _iteration(ctx, config, body, checker, index, source, text, emit):
    """Run one iteration's body and checker. Returns its row: fully evaluated or interrupted."""
    node_id = ctx.node_id
    number = index + 1
    calls = 0
    result = await _call(ctx, body, index, text, emit, first=index == 0)
    if result[0] == 'budget':
        return {'index': index, 'status': 'interrupted', 'budget_halt': result[1], 'calls': calls + result[2]}
    calls += 1
    answer = result[1]
    kind = budget_kind(answer)
    if kind:
        return {'index': index, 'status': 'interrupted', 'budget_halt': kind, 'calls': calls}
    if kind is None:
        from .iteration import body_truncation
        raise ValueError(f"{node_id}: iteration {number}: the body's output is incomplete ({body_truncation(answer)}).")
    value = result_of(node_id, number, 'body', body, answer)
    if guard_abstained(answer):
        # The body honoured an abstention (its own retrieval's, or one in its input). The
        # refusal is the answer: no checker reads it and no later body receives it.
        return {'index': index, 'status': 'abstained', 'value': value}
    verdict = value
    if checker is not None:
        from . import exact_json
        checked = await _call(ctx, checker, index, exact_json.dumps({'input': source, 'answer': value}, compact=True), emit)
        if checked[0] == 'budget':
            return {'index': index, 'status': 'interrupted', 'budget_halt': checked[1], 'calls': calls + checked[2]}
        calls += 1
        kind = budget_kind(checked[1])
        if kind:
            return {'index': index, 'status': 'interrupted', 'budget_halt': kind, 'calls': calls}
        if kind is None:
            from .iteration import body_truncation
            raise ValueError(f"{node_id}: iteration {number}: the checker's output is incomplete ({body_truncation(checked[1])}).")
        verdict = result_of(node_id, number, 'checker', checker, checked[1])
        if guard_abstained(checked[1]):
            # The checker could not judge the draft; the unchecked draft is never returned.
            return {'index': index, 'status': 'abstained', 'value': verdict}
    met = judge(node_id, number, verdict, config)
    return {'index': index, 'status': 'success', 'value': value, 'verdict': verdict, 'met': met}


async def _call(ctx, node, index, text, emit, first=False):
    """Run the body or the checker once, under its own retry policy and the run budgets.

    Returns ('ok', outputs) or ('budget', kind, ran) where ran is 1 when the refused call
    did work first. Any other failure fails the Repeat.
    """
    from dataclasses import replace
    from . import provenance
    from .accounting_state import AccountingUnavailable
    from .execution_policy import (ExecutionIdentity, execute_with_policy, invoke_attached, charges_action,
                                   with_own_usage, outcome_saved)
    from .request_safety import UnsafeExecutionError, guard
    from .tool_service import UncertainWriteError
    node_id = ctx.node_id
    invocation = f'{owner(node_id, index)}:{node.id}'
    # Child calls derive from this invocation, so a writer and a checker that call the same
    # tool on their first step get distinct identities under the shared iteration owner.
    identity = ExecutionIdentity(node.id, owner(node_id, index), invocation, node_id, index, child_base=invocation)
    loop_fields = {'loop_node_id': node_id, 'item_index': index}

    async def item_emit(event):
        await emit({**event, **loop_fields})
    proofs = ctx.input_proofs or {}
    if first:
        # The first body input is the Repeat's own input, with its origins and proof.
        call_ctx = replace(ctx, emit=item_emit, input_proofs={'input': proofs['input']} if 'input' in proofs else {},
                           execution_identity=identity, node_id=node.id, node_type=node.type)
    else:
        # Later inputs hold model text: a draft or feedback is never evidence.
        call_ctx = replace(ctx, emit=item_emit, input_origins=provenance.EvidenceContext(complete=False),
                           input_label=provenance.GUESSED, input_proofs={},
                           execution_identity=identity, node_id=node.id, node_type=node.type)
    common = {**identity.event_fields(), 'transient': True}
    await item_emit({**common, 'status': 'running', 'inputs': {'input': text[:PREVIEW_CHARS]}})
    budget = (ctx.run or {}).get('action_budget')
    try:
        async def invoke():
            return await invoke_attached(node, text, call_ctx, identity, item_emit, action_budget=budget)
        outcome = await execute_with_policy(
            invoke, identity, node.retry, ctx.run, item_emit, generic_error='Iteration call failed.',
            eligibility=lambda: guard(call_ctx, node, {'input': text}, identity),
            action_budget=budget, charge_first_attempt=charges_action(node.type))
    except asyncio.CancelledError as exc:
        # The open iteration is not saved; it reports what it spent, unless already saved.
        if not outcome_saved(exc, identity):
            await item_emit(with_own_usage({**common, 'status': 'cancelled'}, ctx.run, identity))
        raise
    except (UncertainWriteError, UnsafeExecutionError, AccountingUnavailable) as exc:
        failure = with_own_usage({**common, 'status': 'failed', 'error': str(exc)}, ctx.run, identity)
        if 'usage' in failure:
            await item_emit(failure)
        raise
    if outcome.status == 'failed':
        event = {**common, 'status': 'not_run' if outcome.budget_exhausted and not outcome.attempts else 'failed'}
        event['reason' if event['status'] == 'not_run' else 'error'] = outcome.error
        if outcome.usage:
            event['usage'] = outcome.usage
        await item_emit(event)
        if outcome.budget_exhausted:
            return 'budget', outcome.budget_kind, 1 if outcome.attempts else 0
        raise ValueError(f'{node_id}: iteration {index + 1}: {outcome.error}')
    event = {**common, 'status': 'success', 'outputs': outcome.outputs}
    if outcome.usage:
        event['usage'] = outcome.usage
    await item_emit(event)
    return 'ok', outcome.outputs
