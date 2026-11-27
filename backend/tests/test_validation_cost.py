"""Validation cost (DEF-03): the lineage walks are linear in nodes and bindings, and they give
the same answers as before.

Before the fix, compiler.unchecked_model_input, compiler.checked_dependency and
provenance.static_label each walked every input of every node with a per-path `seen` set, so
a chain of n nodes that each bind the previous node twice cost 2**(n+1) calls. Measured on
2026-11-16: n=16 0.32 s, n=18 1.36 s, n=20 5.63 s. /api/validate and /api/runs ran this on the
event loop. The walks now keep one visited set for each top-level call and remember only
complete answers; the routes validate in a thread.

The expected answers in fixtures/validation_answers_88d4d8b.json were made on the parent
commit 88d4d8b by evals/results/def03-2026-11-27/fixture/make_fixture.py.
"""
import asyncio
import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from backend.app import compiler, provenance
from backend.app.compiler import checked_dependency, switch_input_allowed, unchecked_model_input, validate_workflow
from backend.app.models import Node, Workflow
from backend.app.provenance import decision_warnings, static_label
from backend.tests.test_request_contract_validation import future_nodes  # noqa: F401  (fixture for the replay)
from backend.tests.test_switch import agent, flow, jira

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'validation_answers_88d4d8b.json'


# ------------------------------------------------------------------ shapes

def doubled_chain(n, start):
    """n Prompts; each binds the previous value twice, the first binds `start`."""
    nodes, prev = [], start
    for i in range(n):
        nodes.append({'id': f'p{i}', 'type': 'prompt', 'inputs': {'message': prev, 'x': prev}, 'config': {'template': '{message}'}})
        prev = f'p{i}.text'
    return nodes, prev


def plain_chain(n):
    """The DEF-03 trigger: Input → n Prompts → Response, contract 2 (no reader entry)."""
    middle, last = doubled_chain(n, 'input.message')
    nodes = [{'id': 'input', 'type': 'chat_input'}, *middle, {'id': 'out', 'type': 'response', 'inputs': {'text': last}}]
    ids = [n['id'] for n in nodes]
    edges = [{'id': f'e{i}', 'source': s, 'target': t} for i, (s, t) in enumerate(zip(ids, ids[1:]))]
    return {'version': 1, 'contract': 2, 'name': 'chain', 'nodes': nodes, 'edges': edges}


def switch_chain(n, start):
    """Input → Request reader → n Prompts → Switch on the last Prompt."""
    middle, last = doubled_chain(n, start)
    return flow(last, middle=middle)


def size(raw):
    return len(raw['nodes']), sum(len(n.get('inputs', {})) for n in raw['nodes'])


class TooManyCalls(Exception):
    pass


ANY_WALK_LIMIT = 100_000


def count_calls(module, names, limit, call):
    """Run `call` and count calls of the functions `names` defined in `module`. Stop at `limit`,
    so the exponential walk of the parent commit fails fast instead of running for hours. Also
    stop when any lineage walk passes ANY_WALK_LIMIT calls: on the parent commit an earlier,
    uncounted walk of the same chain is exponential before the counted walk starts."""
    filename, count, every = module.__file__, [0], [0]
    walks = {compiler.__file__, provenance.__file__}
    all_names = MODEL_WALK | CHECKED_WALK | LABEL_WALK
    def profile(frame, event, arg):
        if event != 'call' or frame.f_code.co_name not in all_names or frame.f_code.co_filename not in walks:return
        every[0] += 1
        if every[0] > ANY_WALK_LIMIT:raise TooManyCalls(f'more than {ANY_WALK_LIMIT} calls of the lineage walks')
        if frame.f_code.co_name in names and frame.f_code.co_filename == filename:
            count[0] += 1
            if count[0] > limit:raise TooManyCalls(f'more than {limit} calls of {sorted(names)}')
    sys.setprofile(profile)
    try:call()
    finally:sys.setprofile(None)
    return count[0]


MODEL_WALK = {'unchecked_model_input', 'calls_model', '_model_walk'}
CHECKED_WALK = {'checked_dependency', '_checked_walk'}
LABEL_WALK = {'static_label', '_static_walk'}


# ------------------------------------------------------------------ time

PROBE = textwrap.dedent('''
    from backend.app.models import Workflow
    from backend.app.compiler import validate_workflow
    nodes=[{'id':'input','type':'chat_input'}];edges=[];prev,port='input','message'
    for i in range(40):
        ref=f'{prev}.{port}'
        nodes.append({'id':f'p{i}','type':'prompt','inputs':{'message':ref,'x':ref},'config':{'template':'{message}'}})
        edges.append({'id':f'e{i}','source':prev,'target':f'p{i}'});prev,port=f'p{i}','text'
    nodes.append({'id':'out','type':'response','inputs':{'text':f'{prev}.text'}})
    edges.append({'id':'eo','source':prev,'target':'out'})
    validate_workflow(Workflow.model_validate({'version':1,'contract':2,'name':'chain','nodes':nodes,'edges':edges}))
''')


def test_validation_of_a_42_node_workflow_finishes_in_ten_seconds():
    # 42 nodes is under the 100-node limit (models.py:43). The child is killed at the timeout.
    try:
        subprocess.run([sys.executable, '-c', PROBE], timeout=10, check=True, capture_output=True)
    except subprocess.TimeoutExpired:
        pytest.fail('DEF-03: validate_workflow did not finish in 10 s for a 42-node workflow')


SWITCH_PROBE = textwrap.dedent('''
    import sys
    from backend.app.models import Workflow
    from backend.app.compiler import validate_workflow
    from backend.tests.test_validation_cost import switch_chain
    validate_workflow(Workflow.model_validate(switch_chain(40, sys.argv[1])))
''')


@pytest.mark.parametrize('start', ['input.message', 'reader.text'])
def test_validation_of_a_40_prompt_chain_into_a_switch_finishes_in_ten_seconds(start):
    # input.message: the Switch rule reaches static_label. reader.text: checked text, so the
    # rule reaches checked_dependency, which is true at every step.
    try:
        subprocess.run([sys.executable, '-c', SWITCH_PROBE, start], timeout=10, check=True, capture_output=True, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail(f'DEF-03: validate_workflow did not finish in 10 s for a 40-Prompt chain from {start} into a Switch')


# ------------------------------------------------------------------ calls

def test_unchecked_model_input_visits_each_node_once():
    raw = plain_chain(40);nodes, inputs = size(raw)
    workflow = Workflow.model_validate(raw)
    calls = count_calls(compiler, MODEL_WALK, nodes * inputs, lambda: validate_workflow(workflow))
    # Linear, not only under the bound: twice the chain costs about twice the calls.
    longer = Workflow.model_validate(plain_chain(80))
    assert count_calls(compiler, MODEL_WALK, 3 * calls, lambda: validate_workflow(longer)) <= 2.5 * calls


def graph_of(raw):
    return {n['id']: Node.model_validate(n) for n in raw['nodes']}


def test_checked_dependency_cost_is_linear():
    # The Switch rule's first test, on checked text: true at every step, so `all` never stops early.
    raw = switch_chain(40, 'reader.text');nodes, inputs = size(raw)
    graph = graph_of(raw)
    calls = count_calls(compiler, CHECKED_WALK, nodes * inputs, lambda: checked_dependency('p39.text', graph))
    assert checked_dependency('p39.text', graph)
    longer = switch_chain(80, 'reader.text');graph = graph_of(longer)
    assert count_calls(compiler, CHECKED_WALK, 3 * calls, lambda: checked_dependency('p79.text', graph)) <= 2.5 * calls


def test_static_label_cost_is_linear():
    raw = switch_chain(40, 'input.message');nodes, inputs = size(raw);graph = graph_of(raw)
    calls = count_calls(provenance, LABEL_WALK, nodes * inputs * 4, lambda: static_label(graph, 'p39.text'))
    assert static_label(graph, 'p39.text') == provenance.STATIC_TRUSTED
    graph = graph_of(switch_chain(80, 'input.message'))
    assert count_calls(provenance, LABEL_WALK, 3 * calls, lambda: static_label(graph, 'p79.text')) <= 2.5 * calls


# ------------------------------------------------------------------ meaning

REFUSAL = 'Route on checked values, or use a Condition, which supports review.'


def diamond():
    """D reads B (a record read) and C (a loop summary over an attached Agent). Only C comes
    from a model. static_label calls both trusted, so only the lineage walk refuses D."""
    each = {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'records.items'}, 'config': {'body': 'body', 'max_items': 10}}
    d = {'id': 'd', 'type': 'prompt', 'inputs': {'b': 'records.text', 'c': 'each.summary'}, 'config': {'template': '{b} {c}'}}
    raw = flow('d.text', middle=(jira('project = A'), each, d))
    raw['nodes'].append({'id': 'body', 'type': 'agent', 'config': {'provider': 'ollama', 'model': 'fixture', 'role': 'summarizer'}})
    raw['edges'].append({'id': 'loop', 'source': 'each', 'target': 'body', 'kind': 'loop', 'sourceHandle': 'body', 'targetHandle': 'input'})
    return raw


def test_a_diamond_with_a_model_on_one_side_is_still_refused():
    raw = diamond();nodes = {n['id']: Node.model_validate(n) for n in raw['nodes']}
    assert static_label(nodes, 'd.text') == provenance.STATIC_TRUSTED
    errors = validate_workflow(Workflow.model_validate(raw))
    # d's own check walked B and C before the Switch walks them again with stop_at_sources.
    assert any(e.startswith('route: ') and REFUSAL in e for e in errors), errors


def test_a_record_read_after_a_model_query_is_still_a_trusted_switch_input():
    # echo walks records.text without stop_at_sources first (true: guess wrote the query);
    # the Switch walk stops at the record read (false). One memo must keep the two apart.
    echo = {'id': 'echo', 'type': 'prompt', 'inputs': {'message': 'records.text'}, 'config': {'template': '{message}'}}
    raw = flow('records.text', middle=(agent(), jira('{input}', 'guess.text'), echo))
    errors = validate_workflow(Workflow.model_validate(raw))
    assert not any(e.startswith('route: ') and REFUSAL in e for e in errors), errors


def ring():
    """x reads y and a model; y reads x. A walk from x meets x again through y."""
    return {'x': Node(id='x', type='prompt', inputs={'y': 'y.text', 'm': 'm.text'}),
            'y': Node(id='y', type='prompt', inputs={'x': 'x.text'}),
            'm': Node(id='m', type='agent')}


def test_a_node_on_the_walk_path_is_not_remembered_as_safe():
    nodes, memo = ring(), {}
    assert unchecked_model_input('x.text', nodes, memo=memo)
    # The walk from x saw y while x was on its path; y reaches the model through x.
    assert unchecked_model_input('y.text', nodes, memo=memo)


def test_a_label_found_on_the_walk_path_is_not_remembered():
    # c is a nullable calculation over field a of y, and y reads c. From c, y (field a) meets c
    # on its path (guessed); from y itself, the walk goes round to y, and c lifts that guess to
    # runtime. The Calculate passes its operand path as the field, so the walk from c asks for
    # (y.text, 'a'), and that is the entry a wrong memo would keep.
    nodes = {'c': Node(id='c', type='calculate', inputs={'value': 'y.text'}, config={'expression': 'coalesce(a, 1)'}),
             'y': Node(id='y', type='prompt', inputs={'c': 'c.text'})}
    fresh = static_label(nodes, 'y.text', 'a')
    memo = {}
    static_label(nodes, 'c.text', 'result', memo=memo)
    assert static_label(nodes, 'y.text', 'a', memo=memo) == fresh == provenance.STATIC_RUNTIME


# ------------------------------------------------------------------ same answers as the parent commit

def outcome(call):
    try:return {'value': call()}
    except Exception as exc:return {'raised': f'{type(exc).__name__}: {exc}'}


def workflow_answers(raw):
    workflow = Workflow.model_validate(raw)
    return {'errors': outcome(lambda: validate_workflow(workflow)), 'warnings': outcome(lambda: decision_warnings(workflow))}


def graph_answers(graph, shared):
    """Each walk's answer for each reference of a small graph. With `shared`, the calls on one
    graph share one memo for each walk, so a remembered answer must hold for every later call."""
    nodes = {n['id']: Node.model_validate(n) for n in graph['nodes']}
    deps = graph['deps']
    memos = {name: {} for name in ('u0', 'u1', 'ud', 'c', 's')} if shared else None
    kw = (lambda name: {'memo': memos[name]}) if shared else (lambda name: {})
    answers = {}
    for ref in graph['refs']:
        answers[ref] = {
            'unchecked': unchecked_model_input(ref, nodes, deps, **kw('u0')),
            'unchecked_sources': unchecked_model_input(ref, nodes, deps, stop_at_sources=True, **kw('u1')),
            'unchecked_no_deps': unchecked_model_input(ref, nodes, **kw('ud')),
            'checked': checked_dependency(ref, nodes, **kw('c')),
            'labels': [static_label(nodes, ref, field, **kw('s')) for field in ('', 'result', 'question')],
            'switch': switch_input_allowed(ref, nodes, deps),
        }
    return answers


@pytest.fixture(scope='module')
def expected():
    return json.loads(FIXTURE.read_text())


def test_the_trust_rule_gives_the_same_answers_as_before(expected):
    plain = [e for e in expected['workflows'] if e['env'] == 'plain']
    assert len(plain) >= 40 and any(e['source'].startswith('examples/') for e in plain)
    differ = [e['source'] for e in plain if workflow_answers(e['workflow']) != e['answers']]
    assert differ == []


def test_the_trust_rule_gives_the_same_answers_as_before_for_the_guard_declarations(expected, future_nodes):
    declared = [e for e in expected['workflows'] if e['env'] == 'future_nodes']
    assert len(declared) >= 10
    differ = [e['source'] for e in declared if workflow_answers(e['workflow']) != e['answers']]
    assert differ == []


@pytest.mark.parametrize('shared', [False, True], ids=['fresh-memo', 'shared-memo'])
def test_each_walk_gives_the_same_answers_as_before_on_small_graphs(expected, shared):
    # Random graphs with binding cycles, attachment cycles, missing nodes and every trust case.
    graphs = expected['graphs']
    assert len(graphs) >= 500 and any(g['cyclic'] for g in graphs)
    differ = [i for i, g in enumerate(graphs) if graph_answers(g, shared) != g['answers']]
    assert differ == []


# ------------------------------------------------------------------ event loop

@pytest.mark.asyncio
async def test_validate_route_does_not_block_the_event_loop(tmp_path, monkeypatch):
    from backend.app import main
    from backend.tests.test_compiler import sample
    started, ended = [], []
    def slow(workflow):
        started.append(time.perf_counter());time.sleep(1);ended.append(time.perf_counter())
        return []
    monkeypatch.setattr(main, 'validate_workflow', slow)
    app = main.create_app(database_url=f'sqlite:///{tmp_path}/t.db', encryption_key=Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        validating = asyncio.create_task(client.post('/api/validate', json=sample()))
        for _ in range(200):
            if started:break
            await asyncio.sleep(.01)
        health = await client.get('/api/health');answered = time.perf_counter()
        assert (await validating).status_code == 200
    assert health.status_code == 200
    # On the event loop, /api/health could answer only after the slow validation ended.
    assert answered < ended[0] and answered - started[0] < 0.2, (answered - started[0], ended[0] - started[0])
