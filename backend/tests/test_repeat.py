"""A6.1 Part 1: the Repeat node, a bounded loop with a body, an optional checker and a stop rule.

Design: docs/superpowers/specs/2026-11-09-bounded-loops-design.md (revision 5, approved).
The graph stays acyclic: a Repeat runs its iterations inside one node. Each fully evaluated
iteration, and every budget stop, is one saved row. Validation tests assert exact messages.
Runtime tests substitute only the external chat (Ollama) and tool transports; the policy,
budgets, accounting, storage and the real Worker stay real. Test names carry the design IDs.
"""
import asyncio
import copy
import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import providers
from backend.app.compiler import bindable_outputs, compile_workflow, validate_workflow
from backend.app.models import Workflow
from backend.tests.test_kb_services_integration import populated, services  # noqa: F401 (fixture)

MODEL = {'provider': 'ollama', 'model': 'fixture'}
VERDICT = {'type': 'object', 'required': ['pass', 'feedback'],
           'properties': {'pass': {'type': 'boolean'}, 'feedback': {'type': 'string'}}}
PASS = {'operator': 'eq', 'field': 'pass', 'compare_to': 'true'}
NEVER = {'operator': 'contains', 'contains': 'never-present'}

NEEDS_BODY = 'r: Repeat needs exactly one attached body.'
BODY_MATCH = 'r: the configured body must match the attached node.'
CHECKER = 'r: a Repeat has at most one checker, and it must be an Agent.'
CHECK_MATCH = 'r: the configured checker must match the attached node.'
LOOP_START = 'Loop connections must start at a For each or Repeat node.'
BODY_PORT = 'Loop connections must leave the body port.'
WRITES = 'external writes belong after the Repeat.'
MEMORY = 'an Agent that writes memory cannot run inside a Repeat.'
NESTED = 'Nested loops are not supported yet.'
CYCLE = 'Workflow contains a cycle; use a Repeat node for a bounded loop.'
SCHEMA_NEEDED = 'r: a checker needs an output schema, and the stop rule must read one of its fields.'
EMPTY_RULE = "r: a checker's stop rule cannot use the empty operator."
RESTORE = 'r cannot be restored: a saved iteration no longer matches its stop rule. Start a new run.'
REMOVED_SOURCES = 'saved knowledge sources are unavailable or removed. Start a new run with current evidence.'
HALT_TOKENS = 'Run token budget exhausted; raise RELAY_RUN_TOKEN_LIMIT.'
HALT_ACTIONS = 'Run-wide agent action budget exhausted; raise AGENT_RUN_BUDGET.'


def typed_rule(field, kind):
    return f"r: the stop rule reads {field}, which the checker's schema must require as {kind}."


def budget_fail(n, halt=HALT_TOKENS):
    return f'r: the run budget stopped iteration {n} before it was checked. {halt}'


def budget_last(n, halt=HALT_TOKENS):
    return f'r: the run budget stopped iteration {n} before it was checked; the answer from iteration {n - 1} is returned. {halt}'


def verdict(passed, feedback='add the date'):
    return json.dumps({'pass': passed, 'feedback': feedback})


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


# --------------------------------------------------------------------------- builders

def writer(id='writer', **config):
    return {'id': id, 'type': 'agent', 'config': {**MODEL, 'system': f'KEY:{id}', **config}}


def judge(id='judge', **config):
    return {'id': id, 'type': 'agent', 'config': {**MODEL, 'system': f'KEY:{id}', 'role': 'critic', 'output_schema': VERDICT, **config}}


def jira(id='lookup', connection='c1'):
    return {'id': id, 'type': 'tool_jira', 'config': {'connection_id': connection, 'operation': 'search', 'jql': 'project = A'}}


def write_tool(id):
    return {'id': id, 'type': 'tool_http', 'config': {'connection_id': 'sink', 'method': 'POST', 'enable_writes': True, 'approval': False}}


def repeat(body='writer', check='judge', until=PASS, source='input.message', on_error=None, **config):
    node = {'id': 'r', 'type': 'repeat', 'inputs': {'input': source},
            'config': {'body': body, 'check': check, 'until': until, **config}}
    if on_error: node['on_error'] = on_error
    return node


def loop(source, target, handle):
    return {'source': source, 'target': target, 'kind': 'loop', 'sourceHandle': handle, 'targetHandle': 'input'}


def tool_edge(tool, agent):
    return {'source': tool, 'target': agent, 'kind': 'tool', 'targetHandle': 'tools'}


def document(nodes, edges, contract=1):
    built = []
    for i, edge in enumerate(edges):
        item = {'id': f'e{i}', **edge} if isinstance(edge, dict) else {'id': f'e{i}', 'source': edge[0], 'target': edge[1]}
        if not isinstance(edge, dict) and len(edge) > 2: item['sourceHandle'] = edge[2]
        built.append(item)
    return {'version': 1, 'contract': contract, 'name': 'Repeat', 'nodes': [{'id': 'input', 'type': 'chat_input'}, *nodes], 'edges': built}


def standard(body=None, check='default', out='r.text', extra=(), extra_edges=(), on_error=None, **config):
    """input -> r -> out, with the writer as body and the judge as checker (check=None: no checker)."""
    body = body or writer()
    checker = judge() if check == 'default' else check
    nodes = [repeat(body['id'], checker['id'] if checker else '', on_error=on_error, **config), body, *([checker] if checker else []),
             {'id': 'out', 'type': 'response', 'inputs': {'text': out}}, *extra]
    edges = [('input', 'r'), ('r', 'out'), loop('r', body['id'], 'body'), *([loop('r', checker['id'], 'check')] if checker else []), *extra_edges]
    if on_error == 'route':
        nodes.append({'id': 'fallback', 'type': 'response', 'inputs': {'text': 'input.message'}})
        edges.append(('r', 'fallback', 'error'))
    return document(nodes, edges)


def errors(raw):
    try: return validate_workflow(Workflow.model_validate(raw))
    except ValueError as exc: return [str(exc)]


def has(messages, text):
    assert any(text in m for m in messages), f'expected {text!r} in {messages}'


# --------------------------------------------------------------------------- scripted models

class Script:
    """Ollama chat keyed by the node's system marker KEY:<id>. Each queued reply is text, an
    exception, (reply, prompt tokens), or an async callable taking the prompt."""
    def __init__(self, monkeypatch, **queues):
        self.queues = {key: list(replies) for key, replies in queues.items()}
        self.calls = []
        async def chat(model, system, prompt, usage=None, **kwargs):
            key = next((k for k in self.queues if f'KEY:{k}' in system), None)
            if key is None: raise AssertionError(f'unscripted model call: {system[:120]}')
            if not self.queues[key]: raise AssertionError(f'unexpected call for {key}')
            reply = self.queues[key].pop(0)
            text, tokens = reply if isinstance(reply, tuple) else (reply, 1)
            self.calls.append((key, prompt))
            if callable(text): text = await text(prompt)
            if isinstance(text, BaseException): raise text
            providers.record_usage(usage, tokens, 0)
            return text
        monkeypatch.setattr(providers, 'ollama_chat', chat)

    def prompts(self, key):
        return [prompt for k, prompt in self.calls if k == key]

    def count(self, key):
        return len(self.prompts(key))


def call(target, text='x'):
    return json.dumps({'action': 'call', 'target': target, 'input': text})


def final(text):
    return json.dumps({'action': 'final', 'text': text})


async def run_mem(raw, platform=None, message='go', sink=None):
    """Run in memory; return (values, exception, events)."""
    events = []
    async def emit(event): events.append(dict(event))
    try:
        compiled = compile_workflow(Workflow.model_validate(raw), message=message, platform_resolver=platform, emit=emit,
                                    accounting_sink=sink)
        return (await compiled.graph.ainvoke({'values': {}}))['values'], None, events
    except Exception as exc:
        return None, exc, events


def tools(answer='record'):
    """An in-memory platform whose tool calls return `answer`; records each call."""
    calls = []
    async def platform(action, *args):
        if action == 'tool':
            calls.append(args[3].invocation_id if len(args) > 3 else None)
            if args[0] == 'tool_jira': return {'text': answer, 'items': [], 'truncated': False, 'warnings': []}
            return answer
        if action in ('record_vector_sources', 'verify_vector_sources'): return args[-1]
        raise AssertionError(action)
    return platform, calls


def items(events, kind='loop_item'):
    return [e['item_result'] for e in events if e.get('kind') == kind and e.get('node_id') == 'r']


def summary(values):
    return json.loads(values['r']['summary'])


# --------------------------------------------------------------------------- V01-V12: validation

def test_v01_a_repeat_with_an_agent_body_and_checker_validates_and_its_outputs_bind():
    raw = standard(out='r.text')
    raw['nodes'].insert(-1, {'id': 'note', 'type': 'prompt', 'inputs': {'message': 'r.feedback'}, 'config': {'template': '{message}'}})
    raw['edges'] = [e for e in raw['edges'] if not (e['source'] == 'r' and e['target'] == 'out')]
    raw['edges'] += [{'id': 'x1', 'source': 'r', 'target': 'note'}, {'id': 'x2', 'source': 'note', 'target': 'out'}]
    raw['nodes'][-1]['inputs'] = {'text': 'r.summary'}
    assert errors(raw) == []
    assert errors(standard(body=jira(), check=None, until={'operator': 'contains', 'contains': 'done'})) == []


def test_v02_the_body_must_be_exactly_one_matching_attachment():
    none = standard(); none['edges'] = [e for e in none['edges'] if e.get('sourceHandle') != 'body']
    none['nodes'] = [n for n in none['nodes'] if n['id'] != 'writer']
    has(errors(none), NEEDS_BODY)
    two = standard(extra=[writer('second')], extra_edges=[loop('r', 'second', 'body')])
    has(errors(two), NEEDS_BODY)
    mismatch = standard(); next(n for n in mismatch['nodes'] if n['id'] == 'r')['config']['body'] = 'judge'
    has(errors(mismatch), BODY_MATCH)


def test_v03_the_checker_is_at_most_one_agent_and_must_match():
    tool_checker = standard(check=jira('judge'))
    has(errors(tool_checker), CHECKER)
    two = standard(extra=[judge('second')], extra_edges=[loop('r', 'second', 'check')])
    has(errors(two), CHECKER)
    mismatch = standard(); next(n for n in mismatch['nodes'] if n['id'] == 'r')['config']['check'] = 'writer'
    has(errors(mismatch), CHECK_MATCH)
    configured_without_edge = standard(); configured_without_edge['edges'] = [e for e in configured_without_edge['edges'] if e.get('sourceHandle') != 'check']
    configured_without_edge['nodes'] = [n for n in configured_without_edge['nodes'] if n['id'] != 'judge']
    has(errors(configured_without_edge), CHECK_MATCH)


@pytest.mark.parametrize('where', ['body', 'checker', 'body tool'])
def test_v04_a_write_anywhere_in_the_closure_is_refused(where):
    if where == 'body':
        raw = standard(body=write_tool('send'), check=None, until={'operator': 'contains', 'contains': 'ok'})
        culprit = 'send'
    elif where == 'checker':
        raw = standard(extra=[write_tool('mail')], extra_edges=[tool_edge('mail', 'judge')]); culprit = 'mail'
    else:
        raw = standard(extra=[write_tool('mail')], extra_edges=[tool_edge('mail', 'writer')]); culprit = 'mail'
    has(errors(raw), f'{culprit}: {WRITES}')


def test_v05_a_memory_agent_in_the_closure_is_refused():
    has(errors(standard(body=writer(role='memory'))), f'writer: {MEMORY}')
    specialist = standard(extra=[writer('notes', role='memory')],
                          extra_edges=[{'source': 'writer', 'target': 'notes', 'kind': 'agent', 'sourceHandle': 'agents', 'targetHandle': 'input'}])
    has(errors(specialist), f'notes: {MEMORY}')


def test_v06_nesting_is_refused_in_all_three_directions():
    # Repeat inside For each.
    raw = standard()
    raw['nodes'] += [{'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
                      'config': {'connection_id': 'c1', 'operation': 'search', 'jql': 'x'}},
                     {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'}, 'config': {'body': 'r'}}]
    raw['edges'] = [e for e in raw['edges'] if not (e['source'] == 'input' and e['target'] == 'r') and not (e['source'] == 'r' and e['target'] == 'out')]
    raw['edges'] += [{'id': 'n1', 'source': 'input', 'target': 'tickets'}, {'id': 'n2', 'source': 'tickets', 'target': 'each'},
                     {'id': 'n3', 'source': 'each', 'target': 'out'}, {'id': 'n4', **loop('each', 'r', 'body')}]
    next(n for n in raw['nodes'] if n['id'] == 'out')['inputs'] = {'text': 'input.message'}
    next(n for n in raw['nodes'] if n['id'] == 'r')['inputs'] = {}
    has(errors(raw), NESTED)
    # For each inside Repeat.
    inner = {'id': 'each', 'type': 'for_each', 'config': {'body': 'writer'}}
    raw = standard(body=inner, check=None, until={'operator': 'contains', 'contains': 'x'}, extra=[writer()],
                   extra_edges=[loop('each', 'writer', 'body')])
    has(errors(raw), NESTED)
    # Repeat inside Repeat.
    inner = {'id': 'r2', 'type': 'repeat', 'config': {'body': 'writer', 'check': '', 'until': {'operator': 'contains', 'contains': 'x'}}}
    raw = standard(body=inner, check=None, until={'operator': 'contains', 'contains': 'x'}, extra=[writer()],
                   extra_edges=[loop('r2', 'writer', 'body')])
    has(errors(raw), NESTED)


def test_v07_settings_are_checked_and_shown_as_written():
    has(errors(standard(max_iterations=0)), 'r: max_iterations: Input should be greater than or equal to 1')
    has(errors(standard(max_iterations=11)), 'r: max_iterations: Input should be less than or equal to 10')
    assert errors(standard(max_iterations=1)) == []
    assert errors(standard(max_iterations=10)) == []
    has(errors(standard(until={'operator': 'contains'})), 'r: until: The contains operator needs contains.')


def test_v08_a_back_edge_names_the_repeat_node():
    raw = document([{'id': 'a', 'type': 'prompt', 'inputs': {'message': 'input.message'}, 'config': {'template': '{message}'}},
                    {'id': 'b', 'type': 'prompt', 'inputs': {'message': 'a.text'}, 'config': {'template': '{message}'}},
                    {'id': 'out', 'type': 'response', 'inputs': {'text': 'b.text'}}],
                   [('input', 'a'), ('a', 'b'), ('b', 'a'), ('b', 'out')])
    has(errors(raw), CYCLE)


def test_v09_a_check_edge_on_a_for_each_and_a_loop_edge_from_another_node_are_refused():
    raw = document([{'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
                     'config': {'connection_id': 'c1', 'operation': 'search', 'jql': 'x'}},
                    {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'}, 'config': {'body': 'work'}},
                    {'id': 'work', 'type': 'tool_python', 'config': {'code': 'print(1)', 'description': 'x'}},
                    {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
                   [('input', 'tickets'), ('tickets', 'each'), ('each', 'out'), loop('each', 'work', 'check')])
    has(errors(raw), BODY_PORT)
    raw = standard(extra=[{'id': 'p', 'type': 'prompt', 'config': {'template': '{message}'}}], extra_edges=[loop('p', 'writer', 'body')])
    has(errors(raw), LOOP_START)


def test_v10_downstream_nodes_may_bind_exactly_the_three_repeat_outputs():
    refs = bindable_outputs(Workflow.model_validate(standard()))['out']
    assert [ref for ref in refs if ref.startswith('r.')] == ['r.text', 'r.feedback', 'r.summary']


def test_v11_a_checker_needs_a_schema_and_a_field_rule():
    has(errors(standard(check=judge(output_schema={}))), SCHEMA_NEEDED)
    has(errors(standard(until={'operator': 'contains', 'contains': 'yes'})), SCHEMA_NEEDED)


def test_v12_the_rule_field_must_be_required_with_the_matching_type():
    string_pass = {**VERDICT, 'properties': {**VERDICT['properties'], 'pass': {'type': 'string'}}}
    has(errors(standard(check=judge(output_schema=string_pass))), typed_rule('pass', 'boolean'))
    optional = {**VERDICT, 'required': ['feedback']}
    has(errors(standard(check=judge(output_schema=optional))), typed_rule('pass', 'boolean'))
    scored = {'type': 'object', 'required': ['score'], 'properties': {'score': {'type': 'string'}}}
    has(errors(standard(check=judge(output_schema=scored), until={'operator': 'gte', 'field': 'score', 'compare_to': '8'})),
        typed_rule('score', 'number'))
    integer = {'type': 'object', 'required': ['score'], 'properties': {'score': {'type': 'integer'}}}
    assert errors(standard(check=judge(output_schema=integer), until={'operator': 'gte', 'field': 'score', 'compare_to': '8'})) == []
    assert errors(standard()) == []
    has(errors(standard(until={'operator': 'empty', 'field': 'pass'})), EMPTY_RULE)


# --------------------------------------------------------------------------- R01-R10: runtime in memory

@pytest.mark.asyncio
async def test_r01_the_rule_is_met_at_iteration_two(monkeypatch):
    script = Script(monkeypatch, writer=['draft 1', 'draft 2'], judge=[verdict(False), verdict(True, 'good')])
    values, exc, events = await run_mem(standard())
    assert exc is None, exc
    assert script.count('writer') == 2 and script.count('judge') == 2
    assert values['r']['text'] == 'draft 2' and json.loads(values['r']['feedback'])['pass'] is True
    assert summary(values) == {'iterations': 2, 'max_iterations': 3, 'met': True, 'stopped_by': 'rule',
                               'truncated': False, 'truncation_reason': ''}
    assert '_truncation' not in values['r']


@pytest.mark.asyncio
async def test_r02_iteration_inputs_are_the_specified_compact_json(monkeypatch):
    first = verdict(False, 'shorter')
    script = Script(monkeypatch, writer=['draft 1', 'draft 2', 'draft 3'], judge=[first, verdict(False, 'warmer'), verdict(True)])
    values, exc, _ = await run_mem(standard(), message='Write a reply')
    assert exc is None, exc
    assert script.prompts('writer')[0] == 'Write a reply'
    assert json.loads(script.prompts('judge')[0]) == {'input': 'Write a reply', 'answer': 'draft 1'}
    assert script.prompts('judge')[0] == compact({'input': 'Write a reply', 'answer': 'draft 1'})
    second = json.loads(script.prompts('writer')[1])
    assert list(second) == ['input', 'previous', 'feedback']
    assert second['input'] == 'Write a reply' and second['previous'] == 'draft 1'
    assert json.loads(second['feedback']) == {'pass': False, 'feedback': 'shorter'}
    assert script.prompts('writer')[1] == compact(second)
    # The third input carries the latest draft and feedback, not the first.
    third = json.loads(script.prompts('writer')[2])
    assert third['previous'] == 'draft 2' and json.loads(third['feedback'])['feedback'] == 'warmer'
    assert script.prompts('judge')[2] == compact({'input': 'Write a reply', 'answer': 'draft 3'})


@pytest.mark.asyncio
async def test_r03_the_limit_fails_by_default_and_on_error_route_takes_the_error_branch(monkeypatch):
    Script(monkeypatch, writer=['a', 'b', 'c'], judge=[verdict(False)] * 3)
    _, exc, _ = await run_mem(standard())
    assert str(exc) == 'r: the stop rule was not met after 3 iterations.'
    Script(monkeypatch, writer=['a', 'b', 'c'], judge=[verdict(False)] * 3)
    values, exc, _ = await run_mem(standard(on_error='route'))
    assert exc is None and 'fallback' in values and 'out' not in values


@pytest.mark.asyncio
async def test_r04_on_limit_last_returns_the_last_draft_marked_incomplete(monkeypatch):
    from backend.app.worker import run_truncation
    Script(monkeypatch, writer=['a', 'b'], judge=[verdict(False, 'f1'), verdict(False, 'f2')])
    raw = standard(max_iterations=2, on_limit='last')
    values, exc, _ = await run_mem(raw)
    assert exc is None, exc
    reason = 'r reached 2 iterations without meeting its stop rule; the last answer is returned.'
    assert values['r']['text'] == 'b' and json.loads(values['r']['feedback'])['feedback'] == 'f2'
    assert summary(values) == {'iterations': 2, 'max_iterations': 2, 'met': False, 'stopped_by': 'limit',
                               'truncated': True, 'truncation_reason': reason}
    assert values['r']['_truncation'] == {'truncated': True, 'truncation_reason': reason, 'truncation_source': 'confirmed'}
    verdict_of_run = run_truncation(Workflow.model_validate(raw), values)
    assert verdict_of_run['truncated'] is True and verdict_of_run['truncation_reason'] == reason


@pytest.mark.asyncio
async def test_r05_without_a_checker_the_rule_reads_the_body_output(monkeypatch):
    script = Script(monkeypatch, writer=['not yet', 'DONE: reply'])
    values, exc, _ = await run_mem(standard(check=None, until={'operator': 'contains', 'contains': 'DONE'}))
    assert exc is None, exc
    assert script.count('writer') == 2 and values['r']['text'] == 'DONE: reply' and values['r']['feedback'] == ''
    assert summary(values)['iterations'] == 2 and summary(values)['met'] is True


@pytest.mark.asyncio
async def test_r06_a_verdict_that_is_not_json_fails_at_that_iteration(monkeypatch):
    script = Script(monkeypatch, writer=['plain text'])
    _, exc, events = await run_mem(standard(check=None))
    assert str(exc) == 'r: iteration 1: Condition field pass: the value is not JSON, so it has no fields.'
    assert script.count('writer') == 1 and items(events) == []


@pytest.mark.asyncio
async def test_r07_a_body_failure_fails_the_repeat_and_nothing_further_runs(monkeypatch):
    script = Script(monkeypatch, writer=[ValueError('provider down')])
    _, exc, events = await run_mem(standard())
    assert str(exc).startswith('r: iteration 1: ') and script.count('judge') == 0 and script.count('writer') == 1
    assert items(events) == []


@pytest.mark.asyncio
async def test_r08_each_tool_call_is_charged_once_and_the_action_budget_stops_the_loop(monkeypatch):
    monkeypatch.setenv('AGENT_RUN_BUDGET', '2')
    for on_limit in ('last', 'fail'):
        platform, calls = tools('found')
        sink = {}
        values, exc, events = await run_mem(standard(body=jira(), check=None, until=NEVER, on_limit=on_limit), platform, sink=sink)
        assert calls == ['r:0:lookup', 'r:1:lookup']
        assert sink['action_budget']['counter'] == 2
        assert items(events)[-1] == {'index': 2, 'status': 'interrupted', 'budget_halt': 'action', 'calls': 0}
        if on_limit == 'last':
            assert exc is None, exc
            assert summary(values)['iterations'] == 2 and summary(values)['stopped_by'] == 'budget'
            assert summary(values)['truncation_reason'] == budget_last(3, HALT_ACTIONS)
        else:
            assert str(exc) == budget_fail(3, HALT_ACTIONS)


@pytest.mark.asyncio
async def test_r08b_a_checker_stopped_by_the_action_budget_is_not_judged(monkeypatch):
    """With no action left, an Agent checker makes no model call and returns an abstention
    marked truncated (kind action). The iteration is interrupted, never judged."""
    monkeypatch.setenv('AGENT_RUN_BUDGET', '2')
    script = Script(monkeypatch, judge=[verdict(False, 'f1')])
    platform, calls = tools('found')
    sink = {}
    values, exc, events = await run_mem(standard(body=jira(), on_limit='last'), platform, sink=sink)
    assert exc is None, exc
    assert calls == ['r:0:lookup', 'r:1:lookup'] and script.count('judge') == 1
    assert set(sink['usage_by_invocation']) >= {'r:0:judge'}
    assert items(events)[-1] == {'index': 1, 'status': 'interrupted', 'budget_halt': 'action', 'calls': 2}
    assert summary(values)['iterations'] == 1 and summary(values)['truncation_reason'] == budget_last(2, HALT_ACTIONS)


@pytest.mark.asyncio
async def test_r09_the_token_limit_refuses_the_next_writer_without_a_call(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
    script = Script(monkeypatch, writer=[('draft 1', 5)], judge=[(verdict(False), 5)])
    values, exc, events = await run_mem(standard(on_limit='last'))
    assert exc is None, exc
    assert script.count('writer') == 1, 'the second writer must not reach the provider'
    assert values['r']['text'] == 'draft 1' and summary(values)['stopped_by'] == 'budget'
    assert items(events)[-1] == {'index': 1, 'status': 'interrupted', 'budget_halt': 'tokens', 'calls': 1}


@pytest.mark.asyncio
async def test_r10_nested_events_carry_the_iteration_and_shared_tools_get_separate_ids(monkeypatch):
    Script(monkeypatch, writer=[call('lookup'), final('draft')], judge=[call('lookup'), final(verdict(True))])
    platform, calls = tools('record')
    raw = standard(extra=[jira()], extra_edges=[tool_edge('lookup', 'writer'), tool_edge('lookup', 'judge')])
    values, exc, events = await run_mem(raw, platform)
    assert exc is None, exc
    assert calls == ['r:0:writer:1:lookup', 'r:0:judge:1:lookup']
    nested = [e for e in events if e.get('loop_node_id') == 'r']
    assert nested and all(e['item_index'] == 0 for e in nested)
    successes = [e for e in nested if e.get('node_id') == 'lookup' and e['status'] == 'success']
    assert sorted(e['invocation_id'] for e in successes) == ['r:0:judge:1:lookup', 'r:0:writer:1:lookup']
    assert {e['invocation_id'] for e in nested if e.get('node_id') in ('writer', 'judge')} == {'r:0:writer', 'r:0:judge'}
    assert len(items(events)) == 1


@pytest.mark.asyncio
async def test_r10b_for_each_child_ids_do_not_change(monkeypatch):
    Script(monkeypatch, item=[call('lookup'), final('ok')])
    raw = document([{'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
                     'config': {'connection_id': 'c1', 'operation': 'search', 'jql': 'x'}},
                    {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'}, 'config': {'body': 'item'}},
                    writer('item'), jira(),
                    {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
                   [('input', 'tickets'), ('tickets', 'each'), ('each', 'out'), loop('each', 'item', 'body'), tool_edge('lookup', 'item')])
    calls = []
    async def platform(action, *args):
        if action != 'tool': raise AssertionError(action)
        calls.append(args[3].invocation_id)
        found = [{'key': 'A-1'}] if args[1].get('jql') == 'x' else []
        return {'text': 'record', 'items': found, 'truncated': False, 'warnings': []}
    values, exc, _ = await run_mem(raw, platform)
    assert exc is None, exc
    assert 'each:0:1:lookup' in calls


# --------------------------------------------------------------------------- R13-R23: limits, budgets, verdicts

@pytest.mark.asyncio
async def test_r13_an_entry_over_200000_bytes_fails_and_nothing_is_saved(monkeypatch):
    Script(monkeypatch, writer=['x' * 200_001])
    _, exc, events = await run_mem(standard(check=None, until={'operator': 'contains', 'contains': 'x'}))
    assert str(exc) == 'r: iteration 1 output is too large to save (limit 200000 bytes).'
    assert items(events) == []


@pytest.mark.asyncio
async def test_r14a_a_repeat_runs_inside_a_parallel_branch(monkeypatch):
    Script(monkeypatch, writer=['draft'], judge=[verdict(True)], b1=['side'])
    raw = branch_flow()
    assert errors(raw) == []
    values, exc, _ = await run_mem(raw)
    assert exc is None, exc
    assert values['out']['text'] == 'draft'


@pytest.mark.asyncio
async def test_r14b_a_failed_sibling_cancels_the_repeat_and_the_open_iteration_is_not_saved(monkeypatch):
    started = asyncio.Event()
    async def hold(prompt):
        started.set(); await asyncio.sleep(30); return 'never'
    async def fail_after_start(prompt):
        await asyncio.wait_for(started.wait(), 10); return ValueError('b1 failed')
    Script(monkeypatch, writer=[hold], b1=[fail_after_start])
    _, exc, events = await run_mem(branch_flow())
    assert exc is not None and 'b1 failed' in str(exc)
    assert items(events) == []
    assert any(e.get('node_id') == 'r' and e['status'] == 'cancelled' for e in events)
    assert any(e.get('node_id') == 'writer' and e['status'] == 'cancelled' for e in events)


def branch_flow():
    return document([{'id': 'fork', 'type': 'prompt', 'inputs': {'message': 'input.message'}, 'config': {'template': '{message}'}},
                     repeat(source='fork.text'), writer(), judge(),
                     {'id': 'b1', 'type': 'llm', 'inputs': {'prompt': 'fork.text'}, 'config': {**MODEL, 'system': 'KEY:b1'}},
                     {'id': 'join', 'type': 'join'}, {'id': 'out', 'type': 'response', 'inputs': {'text': 'r.text'}}],
                    [('input', 'fork'), ('fork', 'r'), ('fork', 'b1'), ('r', 'join'), ('b1', 'join'), ('join', 'out'),
                     loop('r', 'writer', 'body'), loop('r', 'judge', 'check')])


def test_r16_contract_2_refuses_a_switch_on_the_repeat_text():
    from backend.tests.test_switch import flow
    raw = flow('r.text', middle=(repeat(source='reader.text'),))
    raw['nodes'] += [writer(), judge()]
    raw['edges'] += [{'id': 'lb', **loop('r', 'writer', 'body')}, {'id': 'lc', **loop('r', 'judge', 'check')}]
    # Exactly this one error: the Repeat itself is valid in contract 2, and only the Switch on its
    # model-derived text is refused (unchecked_model_input walks into the body).
    assert errors(raw) == ['route: Route on checked values, or use a Condition, which supports review.']


@pytest.mark.asyncio
@pytest.mark.parametrize('on_limit', ['last', 'fail'])
async def test_r19_a_budget_stop_between_writer_and_checker(monkeypatch, on_limit):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
    script = Script(monkeypatch, writer=[('draft 1', 1), ('draft 2', 8)], judge=[(verdict(False), 1)])
    values, exc, events = await run_mem(standard(on_limit=on_limit))
    assert script.count('judge') == 1, 'the second checker must not reach the provider'
    assert items(events)[-1] == {'index': 1, 'status': 'interrupted', 'budget_halt': 'tokens', 'calls': 1}
    if on_limit == 'last':
        assert exc is None, exc
        assert values['r']['text'] == 'draft 1', 'the unchecked second draft is ineligible'
        assert summary(values) == {'iterations': 1, 'max_iterations': 3, 'met': False, 'stopped_by': 'budget',
                                   'truncated': True, 'truncation_reason': budget_last(2)}
    else:
        assert str(exc) == budget_fail(2)


@pytest.mark.asyncio
@pytest.mark.parametrize('on_limit', ['last', 'fail'])
async def test_r19_with_no_fully_evaluated_iteration_both_settings_fail(monkeypatch, on_limit):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
    Script(monkeypatch, writer=[('draft 1', 10)])
    _, exc, events = await run_mem(standard(on_limit=on_limit))
    assert str(exc) == budget_fail(1)
    assert type(exc).__name__ == 'ValueError' and items(events) == [{'index': 0, 'status': 'interrupted', 'budget_halt': 'tokens', 'calls': 1}]


@pytest.mark.asyncio
async def test_r19b_a_budget_stop_before_any_call_is_saved_without_a_charge(monkeypatch):
    monkeypatch.setenv('AGENT_RUN_BUDGET', '1')
    platform, calls = tools('found')
    sink = {}
    values, exc, events = await run_mem(standard(body=jira(), check=None, until=NEVER, on_limit='last'), platform, sink=sink)
    assert exc is None, exc
    assert calls == ['r:0:lookup'] and sink['action_budget']['counter'] == 1
    assert items(events) == [items(events)[0], {'index': 1, 'status': 'interrupted', 'budget_halt': 'action', 'calls': 0}]
    assert values['r']['text'] == 'found' and summary(values)['truncation_reason'] == budget_last(2, HALT_ACTIONS)


@pytest.mark.asyncio
async def test_r20_a_truncated_verdict_is_never_judged(monkeypatch):
    """A free-text Agent out of tokens succeeds with an abstention marked truncated; a
    contains rule on that text would match it."""
    from backend.app.agent_runtime import NO_EVIDENCE
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
    script = Script(monkeypatch, writer=[('first draft', 10)])
    phrase = NO_EVIDENCE.split('.')[0]
    values, exc, events = await run_mem(standard(check=None, until={'operator': 'contains', 'contains': phrase}, on_limit='last'))
    assert exc is None, exc
    assert script.count('writer') == 1
    assert values['r']['text'] == 'first draft' and summary(values)['met'] is False
    assert summary(values)['stopped_by'] == 'budget'
    assert items(events)[-1]['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_r22a_the_agent_rejects_a_string_verdict_before_the_repeat_sees_it(monkeypatch):
    wrong = json.dumps({'pass': 'true', 'feedback': 'x'})
    Script(monkeypatch, writer=['draft'], judge=[wrong, wrong])
    _, exc, _ = await run_mem(standard())
    assert str(exc).startswith('r: iteration 1: Agent output did not match the required structure')


@pytest.mark.asyncio
async def test_r23_a_body_stopped_by_its_own_step_limit_fails_the_repeat(monkeypatch):
    Script(monkeypatch, writer=[call('lookup')])
    platform, _ = tools('record')
    raw = standard(body=writer(max_steps=1), extra=[jira()], extra_edges=[tool_edge('lookup', 'writer')])
    _, exc, _ = await run_mem(raw, platform)
    assert str(exc).startswith("r: iteration 1: the body's output is incomplete (Agent step budget exhausted")


# --------------------------------------------------------------------------- durable runs (real Worker and Store)

@pytest.fixture
def lab(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from backend.app import tool_service
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store = Store(f'sqlite:///{tmp_path}/repeat.db', Fernet.generate_key())
    store.allow_model('ollama', 'fixture', '', 'local')
    monkeypatch.setattr(tool_service, 'public_addresses', lambda *_: ['93.184.216.34'])
    worker = Worker(store)
    worker.requests = []
    def answer(request):
        worker.requests.append(str(request.url)); return httpx.Response(200, json={'issues': [{'key': 'A-1', 'fields': {'summary': 's'}}]})
    worker.tools.transport = httpx.MockTransport(answer)
    yield store, worker
    store.engine.dispose()


def connection(store):
    from backend.tests.test_iteration import jira_connection
    return jira_connection(store)


def crash_on(monkeypatch, store, predicate, before=False):
    """Stop the attempt when `predicate(event)`: after the event is saved, or before it.
    Each call replaces the previous rule; the wrappers never stack."""
    original = store.__dict__.get('_unwrapped_worker_event') or store.worker_event
    store._unwrapped_worker_event = original
    def wrapped(id, owner, event, **kwargs):
        if before and predicate(event): raise asyncio.CancelledError()
        saved = original(id, owner, event, **kwargs)
        if not before and predicate(event): raise asyncio.CancelledError()
        return saved
    monkeypatch.setattr(store, 'worker_event', wrapped)
    return original


def checkpoint_of(node):
    return lambda e: e.get('node_id') == node and e.get('status') == 'success' and not e.get('transient')


def row_saved(index, status=None):
    return lambda e: (e.get('kind') == 'loop_item' and e.get('node_id') == 'r' and e.get('item_index') == index
                      and (status is None or e['item_result'].get('status') == status))


async def attempt(store, worker, raw=None, id=None, message='go'):
    if id is None: id = store.create_run(raw, message)['id']
    else: store.resume_run(id)
    await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 30)
    return id, store.run(id)


def rows(store, id):
    from backend.app.storage import RunLoopItemRecord
    with Session(store.engine) as s:
        return {r.item_index: r.entry for r in s.scalars(select(RunLoopItemRecord).where(RunLoopItemRecord.run_id == id, RunLoopItemRecord.node_id == 'r'))}


def edit_rows(store, id, change):
    """change(index, entry) returns the new entry, or None to delete the row."""
    from backend.app.storage import RunLoopItemRecord
    with Session(store.engine) as s:
        for record in s.scalars(select(RunLoopItemRecord).where(RunLoopItemRecord.run_id == id, RunLoopItemRecord.node_id == 'r')):
            new = change(record.item_index, copy.deepcopy(record.entry))
            if new is None: s.delete(record)
            else: record.entry = new
        s.commit()


def edit_checkpoint(store, id, change):
    from backend.app.storage import RunRecord
    with Session(store.engine) as s:
        record = s.get(RunRecord, id)
        data = copy.deepcopy(record.data)
        data['checkpoints']['r'] = change(data['checkpoints']['r'])
        record.data = data
        s.commit()


def node_events(run, node, status=None):
    return [e for e in run['events'] if e.get('node_id') == node and (status is None or e['status'] == status)]


def downstream_flow():
    """input -> r -> next (an Agent reading r.text) -> out."""
    raw = standard(out='next.text', extra=[{'id': 'next', 'type': 'agent', 'inputs': {'input': 'r.text'}, 'config': {**MODEL, 'system': 'KEY:next'}}])
    raw['edges'] = [e for e in raw['edges'] if not (e['source'] == 'r' and e['target'] == 'out')]
    raw['edges'] += [{'id': 'd1', 'source': 'r', 'target': 'next'}, {'id': 'd2', 'source': 'next', 'target': 'out'}]
    return raw


@pytest.mark.asyncio
async def test_r11_a_resumed_repeat_reuses_saved_iterations(lab, monkeypatch):
    store, worker = lab
    script = Script(monkeypatch, writer=['draft 1', 'draft 2'], judge=[verdict(False, 'tighter'), verdict(True)])
    crash_on(monkeypatch, store, row_saved(0))
    id, first = await attempt(store, worker, standard())
    assert first['status'] != 'success' and set(rows(store, id)) == {0}
    crash_on(monkeypatch, store, lambda e: False)
    id, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'success', final_run.get('error')
    assert script.count('writer') == 2 and script.count('judge') == 2, 'iteration 1 was not called again'
    resumed = json.loads(script.prompts('writer')[1])
    assert resumed['previous'] == 'draft 1' and json.loads(resumed['feedback'])['feedback'] == 'tighter'
    assert final_run['output'] == 'draft 2'


@pytest.mark.asyncio
async def test_r12_a_saved_iteration_whose_met_no_longer_matches_is_refused(lab, monkeypatch):
    store, worker = lab
    script = Script(monkeypatch, writer=['draft 1'], judge=[verdict(False)])
    crash_on(monkeypatch, store, row_saved(0))
    id, _ = await attempt(store, worker, standard())
    edit_rows(store, id, lambda i, entry: {**entry, 'met': True})
    crash_on(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed' and RESTORE in final_run['error']
    assert script.count('writer') == 1


@pytest.mark.asyncio
async def test_r21a_replay_after_the_halt_row_commits_is_exact(lab, monkeypatch):
    store, worker = lab
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
    monkeypatch.setenv('AGENT_RUN_BUDGET', '5')
    script = Script(monkeypatch, judge=[(verdict(False), 10)])
    raw = standard(body=jira(connection=connection(store)), on_limit='last')
    crash_on(monkeypatch, store, row_saved(1, 'interrupted'))
    id, first = await attempt(store, worker, raw)
    assert first['status'] != 'success'
    saved = rows(store, id)[1]
    assert saved == {'index': 1, 'status': 'interrupted', 'budget_halt': 'tokens', 'calls': 1}
    requests = len(worker.requests)
    crash_on(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'success', final_run.get('error')
    assert len(worker.requests) == requests and script.count('judge') == 1, 'the replay made no call'
    assert json.loads(final_run['checkpoints']['r']['summary'])['truncation_reason'] == budget_last(2)
    assert rows(store, id)[1] == saved


@pytest.mark.asyncio
async def test_r21a_replay_of_a_halt_row_with_no_calls(lab, monkeypatch):
    store, worker = lab
    monkeypatch.setenv('AGENT_RUN_BUDGET', '1')
    raw = standard(body=jira(connection=connection(store)), check=None, until=NEVER, on_limit='last')
    crash_on(monkeypatch, store, row_saved(1, 'interrupted'))
    id, _ = await attempt(store, worker, raw)
    assert rows(store, id)[1] == {'index': 1, 'status': 'interrupted', 'budget_halt': 'action', 'calls': 0}
    counter = store.run(id)['accounting']['action_budget']['counter']
    assert counter == 1, 'saving the halt row charged no action'
    crash_on(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'success', final_run.get('error')
    assert len(worker.requests) == 1
    assert json.loads(final_run['checkpoints']['r']['summary'])['truncation_reason'] == budget_last(2, HALT_ACTIONS)


@pytest.mark.asyncio
async def test_r21b_replay_before_the_halt_row_commits_keeps_drafts_and_paid_usage(lab, monkeypatch):
    """The review's reproduction: a read uses the last action, then the checker is refused
    for tokens; the attempt stops before the halt row commits. The replay may stop for
    actions instead; the test does not assert the kind."""
    store, worker = lab
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
    monkeypatch.setenv('AGENT_RUN_BUDGET', '2')
    script = Script(monkeypatch, judge=[(verdict(False), 10)])
    raw = standard(body=jira(connection=connection(store)), on_limit='last')
    crash_on(monkeypatch, store, row_saved(1, 'interrupted'), before=True)
    id, first = await attempt(store, worker, raw)
    assert first['status'] != 'success' and set(rows(store, id)) == {0}
    before = store.run(id)['accounting']
    requests = len(worker.requests)
    assert requests == 2 and before['action_budget']['counter'] == 2
    crash_on(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    after = store.run(id)['accounting']
    assert final_run['status'] == 'success', final_run.get('error')
    assert script.count('judge') == 1, 'row 0 was not called again'
    assert len(worker.requests) == requests
    assert after['tokens_spent'] == before['tokens_spent'] and after['action_budget']['counter'] == before['action_budget']['counter']
    assert json.loads(final_run['checkpoints']['r']['summary'])['stopped_by'] == 'budget'
    assert final_run['output'] == rows(store, id)[0]['value']


@pytest.mark.asyncio
async def test_r22b_a_restored_string_verdict_is_refused_by_the_repeat(lab, monkeypatch):
    store, worker = lab
    script = Script(monkeypatch, writer=['draft 1'], judge=[verdict(False)])
    crash_on(monkeypatch, store, row_saved(0))
    id, _ = await attempt(store, worker, standard())
    edit_rows(store, id, lambda i, entry: {**entry, 'verdict': json.dumps({'pass': 'true', 'feedback': 'x'}), 'met': True})
    crash_on(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed', 'the text comparison alone would accept "true"'
    assert 'r: iteration 1: the verdict field pass must be a JSON boolean.' in final_run['error']
    assert script.count('writer') == 1 and not node_events(final_run, 'out', 'success')


async def checkpointed(lab, monkeypatch, shape):
    """Run until r's node checkpoint; shape: met, limit (last) or budget (last)."""
    store, worker = lab
    if shape == 'met':
        script = Script(monkeypatch, writer=['draft 1', 'draft 2'], judge=[verdict(False), verdict(True)], next=['done'])
        raw = downstream_flow()
    elif shape == 'limit':
        script = Script(monkeypatch, writer=['draft 1', 'draft 2'], judge=[verdict(False), verdict(False)], next=['done'])
        raw = downstream_flow(); next(n for n in raw['nodes'] if n['id'] == 'r')['config'].update(max_iterations=2, on_limit='last')
    else:
        monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
        script = Script(monkeypatch, writer=[('draft 1', 1), ('draft 2', 8)], judge=[(verdict(False), 1)], next=['done'])
        raw = downstream_flow(); next(n for n in raw['nodes'] if n['id'] == 'r')['config'].update(on_limit='last')
    crash_on(monkeypatch, store, checkpoint_of('r'))
    id, first = await attempt(store, worker, raw)
    assert first['status'] != 'success' and 'r' in first['checkpoints'], first.get('error')
    crash_on(monkeypatch, store, lambda e: False)
    if shape == 'budget':
        # The resumed downstream Agent needs tokens; the stop itself is already saved.
        monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '1000')
    return store, worker, id, script


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['verdict', 'met', 'text'])
async def test_r24_a_changed_checkpoint_or_row_is_refused_and_downstream_never_runs(lab, monkeypatch, change):
    store, worker, id, script = await checkpointed(lab, monkeypatch, 'met')
    if change == 'verdict': edit_rows(store, id, lambda i, e: {**e, 'verdict': verdict(False)} if i == 1 else e)
    elif change == 'met': edit_rows(store, id, lambda i, e: {**e, 'met': True} if i == 0 else e)
    else: edit_checkpoint(store, id, lambda cp: {**cp, 'text': 'changed'})
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed' and RESTORE in final_run['error']
    assert script.count('next') == 0 and not node_events(final_run, 'next', 'running')


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', ['met', 'limit', 'budget'])
async def test_r24c_control_an_untouched_checkpoint_resumes(lab, monkeypatch, shape):
    store, worker, id, script = await checkpointed(lab, monkeypatch, shape)
    assert '_provenance' in store.run(id)['checkpoints']['r'], 'the compiler adds provenance before saving'
    writers, judges = script.count('writer'), script.count('judge')
    saved = store.run(id)['checkpoints']['r']
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'success', final_run.get('error')
    assert script.count('writer') == writers and script.count('judge') == judges
    assert script.count('next') == 1 and final_run['checkpoints']['r'] == saved


@pytest.mark.asyncio
async def test_r24c_d_a_checkpoint_with_an_unknown_key_is_refused(lab, monkeypatch):
    store, worker, id, script = await checkpointed(lab, monkeypatch, 'met')
    edit_checkpoint(store, id, lambda cp: {**cp, 'extra': 'x'})
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed' and RESTORE in final_run['error'] and script.count('next') == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', ['limit', 'budget'])
async def test_r25_a_cached_fallback_without_its_truncation_key_is_refused(lab, monkeypatch, shape):
    store, worker, id, script = await checkpointed(lab, monkeypatch, shape)
    edit_checkpoint(store, id, lambda cp: {k: v for k, v in cp.items() if k != '_truncation'})
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed' and RESTORE in final_run['error'] and script.count('next') == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', ['limit', 'budget'])
async def test_r25_one_loop_cause_per_repeat(lab, monkeypatch, shape):
    from backend.app.worker import run_truncation
    store, worker, id, _ = await checkpointed(lab, monkeypatch, shape)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'success', final_run.get('error')
    reason = json.loads(final_run['checkpoints']['r']['summary'])['truncation_reason']
    causes = [c for c in final_run['truncation_causes'] if c['node_id'] == 'r']
    assert causes == [{'node_id': 'r', 'kind': 'loop', 'source': 'confirmed', 'reason': reason}]
    workflow = Workflow.model_validate(final_run['workflow'])
    values = {'r': {k: v for k, v in final_run['checkpoints']['r'].items() if k != '_truncation'}}
    assert run_truncation(workflow, values)['truncation_causes'] == causes


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'route'])
async def test_r25_a_recovered_budget_failure_keeps_one_budget_cause(lab, monkeypatch, policy):
    store, worker = lab
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
    Script(monkeypatch, writer=[('draft 1', 10)])
    raw = standard(out='input.message', on_error=policy)
    _, final_run = await attempt(store, worker, raw)
    assert final_run['status'] == 'success', final_run.get('error')
    causes = [c for c in final_run['truncation_causes'] if c['node_id'] == 'r']
    assert len(causes) == 1 and causes[0]['kind'] == 'budget'
    assert budget_fail(1) in causes[0]['reason']


@pytest.mark.asyncio
async def test_r26_a_cached_budget_fallback_without_its_halt_row_is_refused(lab, monkeypatch):
    store, worker, id, script = await checkpointed(lab, monkeypatch, 'budget')
    edit_rows(store, id, lambda i, e: None if e['status'] == 'interrupted' else e)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed' and RESTORE in final_run['error'] and script.count('next') == 0


# --------------------------------------------------------------------------- R15, R17, R18: saved sources (real KB)

def kb_flow(kb_id, **config):
    query = {'id': 'writer', 'type': 'query', 'config': {'knowledge_base_id': kb_id, 'mode': 'keyword', **MODEL, 'system': 'KEY:query'}}
    return standard(body=query, **config)


def answer_from_passage(script_calls):
    async def reply(prompt):
        passages = json.loads(prompt)['evidence']['passages']
        script_calls.append(passages[0]['document_id'])
        return json.dumps({'answer': passages[0]['text'] + f" [{passages[0]['citation']}]",
                           'citations': [passages[0]['citation']], 'abstain': False, 'reason': ''})
    return reply


async def kb_lab(tmp_path, monkeypatch, services):
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    from backend.app.worker import Worker
    kb, doc, _ = await populated(services)
    store = Store(f'sqlite:///{tmp_path}/repeat-kb.db', Fernet.generate_key())
    store.allow_model('ollama', 'fixture', '', 'local')
    worker = Worker(store); worker.kbs = services
    return store, worker, kb, doc


def remove(services, kb, doc):
    services.management.call_sync('remove_document', 'local', {'kb_id': kb['id'], 'document_id': doc['id']})


def remove_after_check(monkeypatch, services, kb, doc, checked_node):
    """Remove the document right after the worker's cached-step check for `checked_node`
    passes in this attempt, so that only a later check can catch the removal. The first
    call is the pre-run check (all checkpoints); a cached-step check names one node."""
    from backend.app import worker as worker_module
    original = worker_module.kb_errors
    state = {'done': False, 'calls': 0}
    async def wrapped(kbs, workflow, tenant, checkpoints=None, dependencies=None, progress=None):
        found = await original(kbs, workflow, tenant, checkpoints, dependencies, progress)
        state['calls'] += 1
        if not state['done'] and state['calls'] > 1 and checkpoints is not None and set(checkpoints) == {checked_node}:
            remove(services, kb, doc); state['done'] = True
        return found
    monkeypatch.setattr(worker_module, 'kb_errors', wrapped)
    return state


def kb_script(monkeypatch, judges, cited):
    queries = [(answer_from_passage(cited), 10)] * 4
    return Script(monkeypatch, query=queries, judge=judges, next=['done'])


@pytest.mark.asyncio
async def test_r15_reused_iterations_are_checked_at_the_response(tmp_path, monkeypatch, services):
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    cited = []
    kb_script(monkeypatch, [verdict(False), verdict(True)], cited)
    crash_on(monkeypatch, store, checkpoint_of('r'))
    id, first = await attempt(store, worker, kb_flow(kb['id']), message='project codename')
    assert 'r' in first['checkpoints'] and {'r:0', 'r:1'} <= set(first['vector_dependencies'])
    crash_on(monkeypatch, store, lambda e: False)
    phase, checks = {}, []
    original_store_event = store.worker_event
    def track(id_, owner, event, **kwargs):
        if event.get('status') == 'running' and not event.get('transient'): phase['node'] = event.get('node_id')
        return original_store_event(id_, owner, event, **kwargs)
    monkeypatch.setattr(store, 'worker_event', track)
    original_verify = services.verify
    async def verify(sources, tenant):
        checks.append((phase.get('node'), [s['document_id'] for s in sources]))
        return await original_verify(sources, tenant)
    monkeypatch.setattr(services, 'verify', verify)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'success', final_run.get('error')
    assert any(node == 'out' and doc['id'] in docs for node, docs in checks), checks


@pytest.mark.asyncio
async def test_r17_a_source_removed_before_resume_refuses_before_any_call(tmp_path, monkeypatch, services):
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    script = kb_script(monkeypatch, [verdict(False), verdict(True)], [])
    crash_on(monkeypatch, store, row_saved(0))
    id, first = await attempt(store, worker, kb_flow(kb['id']), message='project codename')
    assert 'r:0' in first['vector_dependencies']
    calls = len(script.calls)
    remove(services, kb, doc)
    crash_on(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed' and f'r:0: {REMOVED_SOURCES}' in final_run['error']
    assert len(script.calls) == calls
    assert len(node_events(final_run, 'input', 'success')) == 1, 'the pre-run check refused before the graph started'


@pytest.mark.asyncio
async def test_r17b_a_late_removal_with_partial_progress_stops_the_next_writer(tmp_path, monkeypatch, services):
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    script = kb_script(monkeypatch, [verdict(False), verdict(True)], [])
    crash_on(monkeypatch, store, row_saved(0))
    id, _ = await attempt(store, worker, kb_flow(kb['id']), message='project codename')
    calls = len(script.calls)
    crash_on(monkeypatch, store, lambda e: False)
    state = remove_after_check(monkeypatch, services, kb, doc, 'input')
    _, final_run = await attempt(store, worker, id=id)
    assert state['done'], 'the removal happened after the pre-run and upstream checks'
    assert final_run['status'] == 'failed' and f'r:0: {REMOVED_SOURCES}' in final_run['error']
    assert len(script.calls) == calls, 'the next writer never received the saved draft'


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['met', 'halt'])
async def test_r17c_a_late_removal_when_the_rows_hold_the_final_answer(tmp_path, monkeypatch, services, terminal):
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    if terminal == 'met':
        script = kb_script(monkeypatch, [(verdict(False), 1), (verdict(True), 1)], [])
        raw = kb_flow(kb['id'])
        crash_on(monkeypatch, store, row_saved(1))
    else:
        monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '21')
        script = kb_script(monkeypatch, [(verdict(False), 1)], [])
        raw = kb_flow(kb['id'], on_limit='last')
        crash_on(monkeypatch, store, row_saved(1, 'interrupted'))
    id, first = await attempt(store, worker, raw, message='project codename')
    assert 'r' not in first['checkpoints'] and set(rows(store, id)) == {0, 1}, first.get('error')
    calls = len(script.calls)
    crash_on(monkeypatch, store, lambda e: False)
    state = remove_after_check(monkeypatch, services, kb, doc, 'input')
    _, final_run = await attempt(store, worker, id=id)
    assert state['done']
    assert final_run['status'] == 'failed' and REMOVED_SOURCES in final_run['error']
    assert len(script.calls) == calls and not node_events(final_run, 'out', 'success')


@pytest.mark.asyncio
async def test_r18_a_source_removed_before_resume_refuses_a_checkpointed_repeat(tmp_path, monkeypatch, services):
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    script = kb_script(monkeypatch, [verdict(False), verdict(True)], [])
    raw = kb_downstream(kb['id'])
    crash_on(monkeypatch, store, checkpoint_of('r'))
    id, first = await attempt(store, worker, raw, message='project codename')
    assert 'r' in first['checkpoints']
    remove(services, kb, doc)
    crash_on(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed' and REMOVED_SOURCES in final_run['error']
    assert script.count('next') == 0
    assert len(node_events(final_run, 'input', 'success')) == 1, 'the pre-run check refused before the graph started'


@pytest.mark.asyncio
async def test_r18b_a_late_removal_refuses_in_the_restore_branch(tmp_path, monkeypatch, services):
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    script = kb_script(monkeypatch, [verdict(False), verdict(True)], [])
    crash_on(monkeypatch, store, checkpoint_of('r'))
    id, _ = await attempt(store, worker, kb_downstream(kb['id']), message='project codename')
    crash_on(monkeypatch, store, lambda e: False)
    state = remove_after_check(monkeypatch, services, kb, doc, 'r')
    _, final_run = await attempt(store, worker, id=id)
    assert state['done'], "the removal came after the worker's cached-step check for r"
    assert final_run['status'] == 'failed' and REMOVED_SOURCES in final_run['error']
    assert script.count('next') == 0 and not node_events(final_run, 'next', 'running')


def kb_downstream(kb_id):
    raw = downstream_flow()
    raw['nodes'] = [n if n['id'] != 'writer' else {'id': 'writer', 'type': 'query', 'config': {
        'knowledge_base_id': kb_id, 'mode': 'keyword', **MODEL, 'system': 'KEY:query'}} for n in raw['nodes']]
    return raw


# --------------------------------------------------------------------------- review of 011a9e0
# R17d: a fallback that returns a saved draft is checked again at the return.
# R27: an answerability-guard abstention stops the Repeat and its decision travels on.
# R28: the body's result is read from its declared result port, never defaulted to ''.

GUARD = {'decision': 'abstain', 'abstention_source': 'answerability_guard'}


def guard_abstains(monkeypatch):
    """The local answerability reader abstains; returns the list of checked questions."""
    from backend.app import answerability_guard
    asked = []
    async def check(question, passages, node_id=None):
        asked.append(question)
        return {'decision': 'abstain', 'status': 'scored', 'abstention_source': 'answerability_guard'}
    monkeypatch.setattr(answerability_guard, 'check', check)
    return asked


def fixture_kb(sources=()):
    async def platform(action, *args):
        if action == 'kb_retrieve': return {'sources': list(sources), 'version': 'fixture'}
        if action in ('record_vector_sources', 'verify_vector_sources'): return []
        raise AssertionError(action)
    return platform


def guarded_retrieve(id='lookup'):
    return {'id': id, 'type': 'retrieve', 'config': {'knowledge_base_id': 'fixture', 'answerability_guard': True}}


def guard_summary(iterations=0):
    return {'iterations': iterations, 'max_iterations': 3, 'met': False, 'stopped_by': 'guard',
            'truncated': False, 'truncation_reason': '', 'answerability': GUARD}


@pytest.mark.asyncio
async def test_r17d_a_removal_after_the_halt_row_refuses_the_saved_fallback(tmp_path, monkeypatch, services):
    monkeypatch.setenv('AGENT_RUN_BUDGET', '2')
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    script = kb_script(monkeypatch, [verdict(False)], [])
    raw = kb_downstream(kb['id'])
    for node in raw['nodes']:
        if node['id'] == 'r': node['config']['on_limit'] = 'last'
        if node['id'] == 'next': node.update(type='llm', inputs={'prompt': 'r.text'})
    assert errors(raw) == []
    original = crash_on(monkeypatch, store, row_saved(0))
    id, first = await attempt(store, worker, raw, message='project codename')
    assert set(rows(store, id)) == {0} and 'r' not in first['checkpoints'], first.get('error')
    removed = []
    def remove_after_halt(id_, owner, event, **kwargs):
        saved = original(id_, owner, event, **kwargs)
        if row_saved(1, 'interrupted')(event):
            remove(services, kb, doc); removed.append(True)
        return saved
    monkeypatch.setattr(store, 'worker_event', remove_after_halt)
    _, final_run = await attempt(store, worker, id=id)
    assert removed and rows(store, id)[1]['budget_halt'] == 'action', 'the source went after the halt row committed'
    assert final_run['status'] == 'failed' and f'r:0: {REMOVED_SOURCES}' in final_run['error']
    assert script.count('next') == 0 and not node_events(final_run, 'next', 'running')


@pytest.mark.asyncio
async def test_r27a_an_upstream_abstention_stops_the_repeat_before_the_checker(monkeypatch):
    from backend.app.agent_runtime import NO_EVIDENCE
    guard_abstains(monkeypatch)
    script = Script(monkeypatch, writer=['model-generated draft'], judge=[verdict(False), verdict(True)])
    raw = document([
        {**guarded_retrieve('retrieve'), 'inputs': {'query': 'input.message'}},
        repeat(source='retrieve.context'), writer(), judge(),
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'r.text'}},
    ], [('input', 'retrieve'), ('retrieve', 'r'), ('r', 'out'), loop('r', 'writer', 'body'), loop('r', 'judge', 'check')])
    assert errors(raw) == []
    values, exc, events = await run_mem(raw, platform=fixture_kb())
    assert exc is None, exc
    assert script.count('writer') == 0 and script.count('judge') == 0, 'no checker and no second writer'
    refusal = values['r']['text']
    assert refusal.startswith(NO_EVIDENCE) and values['out']['text'] == refusal and values['r']['feedback'] == ''
    assert summary(values) == guard_summary()
    assert items(events) == [{'index': 0, 'status': 'abstained', 'value': refusal}]
    assert '_truncation' not in values['r']


@pytest.mark.asyncio
async def test_r27b_a_body_abstention_reaches_a_downstream_model_node(monkeypatch):
    from backend.app.agent_runtime import NO_EVIDENCE
    guard_abstains(monkeypatch)
    script = Script(monkeypatch, next=['generated anyway'])
    raw = document([
        repeat(body='lookup', check='', until=NEVER), guarded_retrieve(),
        {'id': 'next', 'type': 'llm', 'inputs': {'prompt': 'r.text'}, 'config': {**MODEL, 'system': 'KEY:next'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'next.text'}},
    ], [('input', 'r'), ('r', 'next'), ('next', 'out'), loop('r', 'lookup', 'body')])
    assert errors(raw) == []
    values, exc, events = await run_mem(raw, platform=fixture_kb())
    assert exc is None, exc
    assert script.count('next') == 0, 'the downstream model node honoured the carried decision'
    assert values['next']['text'].startswith(NO_EVIDENCE)
    assert json.loads(values['r']['text'])['answerability']['decision'] == 'abstain'
    assert summary(values) == guard_summary()
    assert items(events) == [{'index': 0, 'status': 'abstained', 'value': values['r']['text']}]


@pytest.mark.asyncio
async def test_r27c_a_checker_abstention_returns_the_refusal_not_the_unchecked_draft(monkeypatch):
    from backend.app.agent_runtime import NO_EVIDENCE
    guard_abstains(monkeypatch)
    script = Script(monkeypatch, writer=['draft 1', 'draft 2'], judge=[call('lookup', 'q'), final(verdict(True))])
    raw = standard(extra=[guarded_retrieve()], extra_edges=[tool_edge('lookup', 'judge')])
    assert errors(raw) == []
    values, exc, events = await run_mem(raw, platform=fixture_kb())
    assert exc is None, exc
    assert script.count('writer') == 1 and script.count('judge') == 1
    refusal = values['r']['text']
    assert refusal.startswith(NO_EVIDENCE) and 'draft 1' not in refusal
    assert summary(values) == guard_summary()
    assert items(events) == [{'index': 0, 'status': 'abstained', 'value': refusal}]


def guarded_query_flow(kb_id):
    raw = kb_flow(kb_id)
    for node in raw['nodes']:
        if node['id'] == 'writer': node['config']['answerability_guard'] = True
    return raw


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['row', 'checkpoint'])
async def test_r27d_a_saved_guard_stop_is_reused_without_a_call(tmp_path, monkeypatch, services, stop):
    from backend.app.agent_runtime import NO_EVIDENCE
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    asked = guard_abstains(monkeypatch)
    script = Script(monkeypatch, query=[], judge=[])
    crash_on(monkeypatch, store, row_saved(0) if stop == 'row' else checkpoint_of('r'))
    id, first = await attempt(store, worker, guarded_query_flow(kb['id']), message='project codename')
    assert first['status'] != 'success' and ('r' in first['checkpoints']) == (stop == 'checkpoint')
    saved = rows(store, id)
    assert set(saved) == {0} and saved[0]['status'] == 'abstained' and saved[0]['value'].startswith(NO_EVIDENCE)
    crash_on(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'success', final_run.get('error')
    assert final_run['output'] == saved[0]['value'] and len(asked) == 1 and not script.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [lambda e: {**e, 'met': False}, lambda e: {**e, 'value': 7},
                                    lambda e: {**e, 'index': 1}])
async def test_r27e_a_changed_guard_row_is_refused(tmp_path, monkeypatch, services, change):
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    guard_abstains(monkeypatch)
    Script(monkeypatch, query=[], judge=[])
    crash_on(monkeypatch, store, row_saved(0))
    id, _ = await attempt(store, worker, guarded_query_flow(kb['id']), message='project codename')
    edit_rows(store, id, lambda i, e: change(e))
    crash_on(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed' and RESTORE in final_run['error']


@pytest.mark.asyncio
async def test_r28a_a_retrieve_body_result_is_its_context(tmp_path, monkeypatch, services):
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    raw = standard(body={'id': 'lookup', 'type': 'retrieve', 'config': {'knowledge_base_id': kb['id'], 'mode': 'keyword'}},
                   check=None, until={'operator': 'contains', 'contains': 'Bluebird'})
    assert errors(raw) == []
    id, run = await attempt(store, worker, raw, message='project codename')
    assert run['status'] == 'success', run.get('error')
    row = rows(store, id)[0]
    assert row['met'] is True and row['value'] == row['verdict']
    assert json.loads(row['value'])['passages'][0]['document_id'] == doc['id']
    assert run['output'] == row['value']


@pytest.mark.asyncio
async def test_r28b_a_body_without_its_result_port_fails_the_repeat(monkeypatch):
    from backend.app.registry import REGISTRY
    async def no_context(inputs, config, ctx):
        return {'query': inputs['query'], 'sources': '[]'}
    monkeypatch.setattr(REGISTRY['retrieve'], 'handler', no_context)
    raw = standard(body={'id': 'lookup', 'type': 'retrieve', 'config': {'knowledge_base_id': 'fixture'}},
                   check=None, until={'operator': 'empty'})
    values, exc, events = await run_mem(raw, platform=fixture_kb())
    assert exc is not None and "r: iteration 1: the body's context output is missing." in str(exc)
    assert items(events) == []


@pytest.mark.asyncio
async def test_r27f_a_guard_row_over_200000_bytes_fails_and_nothing_is_saved(monkeypatch):
    guard_abstains(monkeypatch)
    raw = document([repeat(body='lookup', check='', until=NEVER), guarded_retrieve(),
                    {'id': 'out', 'type': 'response', 'inputs': {'text': 'r.text'}}],
                   [('input', 'r'), ('r', 'out'), loop('r', 'lookup', 'body')])
    values, exc, events = await run_mem(raw, platform=fixture_kb(), message='q' * 200_001)
    assert exc is not None and 'r: iteration 1 output is too large to save (limit 200000 bytes).' in str(exc), exc
    assert items(events) == []
