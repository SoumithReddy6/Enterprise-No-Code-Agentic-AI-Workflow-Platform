"""Switch: one of several named paths, chosen in code from checked or trusted values only."""
import copy
import re
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from backend.app.compiler import compile_workflow, validate_workflow
from backend.app.conditions import ConditionConfig, evaluate
from backend.app.models import Workflow
from backend.app.registry import REGISTRY
from backend.app.switch import SwitchConfig, choose

ROOT = Path(__file__).resolve().parents[2]
SUPPORT = json.loads((ROOT / 'examples/request-admission/support.json').read_text())
READER = next(n for n in SUPPORT['nodes'] if n['type'] == 'request_reader')
MESSAGE = '{"summary":"Invoice download is broken","category":"billing"}'


def case(id, compare_to, label=None, **when):
    return {'id': id, 'label': label or id.title(), 'when': {'field': 'category', 'operator': 'eq', 'compare_to': compare_to, **when}}


CASES = [case('billing', 'billing'), case('technical', 'technical'), case('account', 'account')]


def flow(value='reader.text', cases=None, middle=(), contract=2, after=None):
    """input → reader → [middle nodes] → switch → one response per route; reader reply → response."""
    cases = CASES if cases is None else cases
    reader = copy.deepcopy(READER);reader['config']['model'] = 'fixture'
    nodes = [{'id': 'input', 'type': 'chat_input'}, reader, *middle,
             {'id': 'route', 'type': 'switch', 'inputs': {'value': value}, 'config': {'cases': cases}},
             {'id': 'replied', 'type': 'response', 'inputs': {'text': 'reader.reply'}}]
    chain = ['reader', *(m['id'] for m in middle), 'route']
    edges = [{'id': 'a', 'source': 'input', 'target': 'reader'}, {'id': 'r', 'source': 'reader', 'target': 'replied', 'sourceHandle': 'reply'}]
    edges += [{'id': f'c{i}', 'source': s, 'target': t, **({'sourceHandle': 'continue'} if s == 'reader' else {})}
              for i, (s, t) in enumerate(zip(chain, chain[1:]))]
    for route in [*(c['id'] for c in cases), 'default']:
        target = (after or {}).get(route)
        if target:
            nodes.extend(target['nodes']);edges.append({'id': 'to_' + route, 'source': 'route', 'target': target['nodes'][0]['id'], 'sourceHandle': route})
            edges.extend(target['edges'])
        else:
            out = 'out_' + re.sub(r'[^A-Za-z0-9_-]', '_', route)
            nodes.append({'id': out, 'type': 'response', 'inputs': {'text': 'input.message'}})
            edges.append({'id': 'to_' + out, 'source': 'route', 'target': out, 'sourceHandle': route})
    return {'name': 'Support routing', 'contract': contract, 'nodes': nodes, 'edges': edges}


def reading(**values):
    return json.dumps({'type': 'request', 'values': {'summary': 'Invoice download is broken', 'category': 'billing', **values}, 'reply': ''})


@pytest.fixture
def model(monkeypatch):
    calls, answers = [], []
    async def chat(model, system, prompt, usage=None, **kwargs):
        calls.append(prompt);usage.update(prompt_tokens=5, completion_tokens=2)
        return answers.pop(0)
    monkeypatch.setattr('backend.app.providers.ollama_chat', chat)
    return calls, answers


async def run(raw, message=MESSAGE, *answers, model, completed=None):
    model[1].extend(answers or [reading()])
    events = []
    async def emit(event):events.append(event)
    compiled = compile_workflow(Workflow.model_validate(raw), lambda _: '', emit, message, completed=completed, run_id='run1')
    return (await compiled.graph.ainvoke({'values': {}}))['values'], events


# ------------------------------------------------------------------ predicates and order

@pytest.mark.parametrize('value,when', [
    ('{"amount": 0}', {'field': 'amount', 'operator': 'eq', 'compare_to': '0', 'compare_as': 'number'}),
    ('{"amount": 0}', {'field': 'amount', 'operator': 'empty'}),
    ('{"flag": false}', {'field': 'flag', 'operator': 'eq', 'compare_to': 'false'}),
    ('{"amount": 1000.00000000000001}', {'field': 'amount', 'operator': 'gt', 'compare_to': '1000'}),
    ('{"tier": " Gold "}', {'field': 'tier', 'operator': 'in', 'options': ['gold', 'silver']}),
    ('{"tier": "Gold"}', {'field': 'tier', 'operator': 'eq', 'compare_to': 'gold', 'case_sensitive': True}),
    ('Please refund me', {'operator': 'contains', 'contains': 'refund'}),
    ('{"items": []}', {'field': 'items', 'operator': 'empty'}),
])
def test_each_case_matches_exactly_when_the_same_condition_is_true(value, when):
    config = SwitchConfig.model_validate({'cases': [{'id': 'hit', 'label': 'Hit', 'when': when}]})
    assert choose(value, config) == ('hit' if evaluate(value, ConditionConfig.model_validate(when)) else 'default')


def test_the_first_true_case_wins_and_later_cases_are_not_evaluated():
    # The second case would raise (text compared as a number) if it were evaluated.
    config = SwitchConfig.model_validate({'cases': [case('first', 'billing'),
        {'id': 'second', 'label': 'Second', 'when': {'field': 'category', 'operator': 'gt', 'compare_to': '5'}}]})
    assert choose('{"category":"billing"}', config) == 'first'
    with pytest.raises(ValueError):
        choose('{"category":"other"}', config)


def test_no_match_takes_default_and_an_error_never_does():
    config = SwitchConfig.model_validate({'cases': CASES})
    assert choose('{"category":"shipping"}', config) == 'default'
    for value in ('{"summary":"no category"}', 'not json', '{"category": {"nested": 1}}'):
        with pytest.raises(ValueError):
            choose(value, config)


@pytest.mark.parametrize('cases', [
    [], [case(f'c{i}', str(i)) for i in range(16)], [case('a', 'x'), case('a', 'y')],
    [case('default', 'x')], [case('error', 'x')], [case('9bad', 'x')], [case('a b', 'x')],
    [{**case('a', 'x'), 'label': ' '}], [{**case('a', 'x'), 'when': {'operator': 'gt'}}],
    [{**case('a', 'x'), 'extra': 1}],
])
def test_invalid_settings_are_refused_with_their_reason(cases):
    errors = validate_workflow(Workflow.model_validate(flow(cases=cases)))
    assert any(e.startswith('route:') for e in errors), errors


@pytest.mark.parametrize('mutation', [
    lambda w: w['edges'].remove(next(e for e in w['edges'] if e.get('sourceHandle') == 'default')),
    lambda w: w['edges'].remove(next(e for e in w['edges'] if e.get('sourceHandle') == 'account')),
    lambda w: w['edges'].append({'id': 'x', 'source': 'route', 'target': 'out_billing', 'sourceHandle': 'shipping'}),
    lambda w: w['edges'].append({'id': 'x', 'source': 'route', 'target': 'out_technical', 'sourceHandle': 'billing'}),
    lambda w: next(n for n in w['nodes'] if n['id'] == 'route').update(on_error='continue'),
])
def test_every_route_needs_exactly_one_edge(mutation):
    raw = flow();mutation(raw)
    assert any('route' in e for e in validate_workflow(Workflow.model_validate(raw)))


# ------------------------------------------------------------------ execution and restore

@pytest.mark.asyncio
@pytest.mark.parametrize('category,route', [('billing', 'billing'), ('technical', 'technical'), ('account', 'default')])
async def test_only_the_selected_path_runs(model, category, route):
    # Two cases only, so the checked category "account" matches none and takes Default.
    message = json.dumps({'summary': 'Invoice download is broken', 'category': category}, separators=(',', ':'))
    values, events = await run(flow(cases=CASES[:2]), message, reading(category=category), model=model)
    assert values['route']['branch'] == route
    ran = {k for k in values if k.startswith('out_')}
    assert ran == {'out_' + route} and len(model[0]) == 1
    assert values['route']['_provenance']['ports']['branch'] == 'calculated'


@pytest.mark.asyncio
async def test_a_comparison_error_fails_the_run_and_takes_no_path(model):
    cases = [{'id': 'big', 'label': 'Big', 'when': {'field': 'category', 'operator': 'gt', 'compare_to': '5'}}]
    with pytest.raises(ValueError):
        await run(flow(cases=cases), model=model)


@pytest.mark.asyncio
@pytest.mark.parametrize('branch,error', [('shipping', 'not a declared control route'), ('technical', 'does not match')])
async def test_a_saved_route_must_be_declared_and_match_its_input(model, branch, error):
    values, _ = await run(flow(), model=model)
    saved = {k: values[k] for k in ('input', 'reader', 'route')}
    saved['route'] = {**saved['route'], 'branch': branch}
    with pytest.raises(ValueError, match=error):
        await run(flow(), model=model, completed=copy.deepcopy(saved))
    # The honest checkpoint replays without a model call.
    calls = len(model[0])
    replayed, _ = await run(flow(), model=model, completed={k: values[k] for k in ('input', 'reader', 'route')})
    assert 'out_billing' in replayed and len(model[0]) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize('branch', ['shipping', 'technical'])
async def test_a_handler_cannot_choose_a_different_route(model, monkeypatch, branch):
    from dataclasses import replace
    async def forged(inputs, config, ctx):return {'branch': branch}
    monkeypatch.setitem(REGISTRY, 'switch', replace(REGISTRY['switch'], handler=forged))
    with pytest.raises(ValueError):
        await run(flow(), model=model)


@pytest.mark.parametrize('kind', ['tool', 'loop', 'agent'])
def test_a_switch_cannot_be_attached(kind):
    raw = flow();raw['edges'].append({'id': 'attachment', 'source': 'route', 'target': 'out_billing', 'kind': kind})
    assert any('control' in e.lower() for e in validate_workflow(Workflow.model_validate(raw)))


# ------------------------------------------------------------------ checked or trusted values only

def agent(id='guess'):
    return {'id': id, 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': {'provider': 'ollama', 'model': 'fixture'}}


def tool(id, type, **config):
    return {'id': id, 'type': type, 'inputs': {'input': 'input.message'}, 'config': {'connection_id': 'c1', **config}}


CALC = {'id': 'calc', 'type': 'prompt', 'inputs': {'message': 'reader.text'}, 'config': {'template': '{message}'}}


@pytest.mark.parametrize('value,middle', [
    ('reader.text', ()),                                   # checked by the reader
    ('calc.text', (CALC,)),                                # derived only from checked values
    ('input.message', ()),                                 # the trigger itself
    ('records.text', (tool('records', 'tool_jira', operation='search', jql='project = A', enable_writes=False),)),
])
def test_checked_or_trusted_values_are_accepted(value, middle):
    assert validate_workflow(Workflow.model_validate(flow(value, middle=middle))) == []


@pytest.mark.parametrize('value,middle', [
    ('guess.text', (agent(),)),
    ('http.text', (tool('http', 'tool_http', method='GET', path='/x'),)),
    ('py.text', ({'id': 'py', 'type': 'tool_python', 'inputs': {'input': 'input.message'}, 'config': {'code': 'result = input'}},)),
    ('records.text', (tool('records', 'tool_jira', operation='create_issue', enable_writes=True),)),
])
def test_unchecked_or_untrusted_values_are_refused_at_validation(value, middle):
    try:errors = validate_workflow(Workflow.model_validate(flow(value, middle=middle)))
    except ValueError as exc:errors = [str(exc)]
    assert any('Route on checked values, or use a Condition, which supports review.' in e for e in errors), errors


def test_a_switch_requires_contract_2():
    raw = flow(contract=1)
    raw['nodes'] = [n for n in raw['nodes'] if n['type'] != 'request_reader' and n['id'] != 'replied']
    raw['edges'] = [e for e in raw['edges'] if 'reader' not in (e['source'], e['target'])]
    raw['edges'].append({'id': 'a2', 'source': 'input', 'target': 'route'})
    raw['nodes'][next(i for i, n in enumerate(raw['nodes']) if n['id'] == 'route')]['inputs']['value'] = 'input.message'
    assert 'Switch requires a contract-2 workflow.' in ' '.join(validate_workflow(Workflow.model_validate(raw)))


@pytest.mark.asyncio
async def test_runtime_refuses_a_model_value_even_if_validation_missed_it(model, monkeypatch):
    monkeypatch.setattr('backend.app.compiler.request_contract_errors', lambda *a: [])
    with pytest.raises(ValueError, match='Route on checked values'):
        await run(flow('guess.text', middle=(agent(),)), MESSAGE, reading(), '{"category":"billing"}', model=model)


@pytest.mark.asyncio
async def test_runtime_refuses_a_switch_outside_contract_2(model, monkeypatch):
    monkeypatch.setattr('backend.app.compiler.request_contract_errors', lambda *a: [])
    from backend.app.request_safety import check_execution_eligibility, UnsafeExecutionError
    from backend.app.models import Node
    with pytest.raises(UnsafeExecutionError, match='contract-2'):
        await check_execution_eligibility(None, {}, {}, 1, node=Node(id='route', type='switch'), ctx=None)


@pytest.mark.asyncio
async def test_a_forged_receipt_is_not_a_checked_value(model, monkeypatch):
    # A value the reader never checked, presented with a copied receipt dictionary.
    monkeypatch.setattr('backend.app.compiler.request_contract_errors', lambda *a: [])
    values, _ = await run(flow(), model=model)
    forged = {k: copy.deepcopy(values[k]) for k in ('input', 'reader')}
    forged['reader']['text'] = '{"summary": "x", "category": "billing"}'
    with pytest.raises(ValueError):
        await run(flow(), model=model, completed=forged)


# ------------------------------------------------------------------ a case name grants nothing

def http_write(id, approval):
    return {'nodes': [{'id': id, 'type': 'tool_http', 'inputs': {'input': 'reader.text'},
                       'config': {'connection_id': 'CONN', 'method': 'POST', 'path': '/deliver/' + id,
                                  'body': '{input}', 'enable_writes': True, 'approval': approval}},
                      {'id': id + '_done', 'type': 'response', 'inputs': {'text': id + '.text'}}],
            'edges': [{'id': id + '_e', 'source': id, 'target': id + '_done'}]}


def test_a_write_after_any_case_still_needs_a_checked_value():
    after = {'approved': {'nodes': [{'id': 'send', 'type': 'tool_http', 'inputs': {'input': 'input.message'},
             'config': {'connection_id': 'c1', 'method': 'POST', 'path': '/x', 'body': '{input}', 'enable_writes': True}},
             {'id': 'sent', 'type': 'response', 'inputs': {'text': 'send.text'}}],
             'edges': [{'id': 'se', 'source': 'send', 'target': 'sent'}]}}
    raw = flow(cases=[case('approved', 'billing'), case('other', 'technical')], after=after)
    errors = validate_workflow(Workflow.model_validate(raw))
    assert any('send:' in e and 'verified' in e for e in errors), errors


@pytest.fixture
def worker_setup(tmp_path, monkeypatch, model):
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store = Store(f'sqlite:///{tmp_path}/switch.db', Fernet.generate_key());store.allow_model('ollama', 'fixture')
    worker = Worker(store)
    connection = worker.tools.save_connection({'name': 'Receiver', 'provider': 'http', 'endpoint': 'https://receiver.example'})['id']
    sent = []
    monkeypatch.setattr('backend.app.tool_service.public_addresses', lambda *a: ['93.184.216.34'])
    worker.tools.transport = httpx.MockTransport(lambda request: (sent.append(request) or httpx.Response(200, text='sent')))
    yield store, worker, connection, sent
    store.engine.dispose()


def routed_writes(connection, approval_on='approved'):
    after = {}
    for route in ('approved', 'other', 'default'):
        target = http_write('send_' + route, route == approval_on)
        target['nodes'][0]['config']['connection_id'] = connection
        after[route] = target
    return flow(cases=[case('approved', 'billing', label='Approved'), case('other', 'technical')], after=after)


@pytest.mark.asyncio
async def test_a_case_named_approved_still_pauses_for_write_approval(worker_setup, model):
    store, worker, connection, sent = worker_setup
    model[1].append(reading())
    run_id = store.create_run(routed_writes(connection), MESSAGE)['id']
    await worker.execute(store.claim_next(worker.owner))
    saved = store.run(run_id)
    assert saved['status'] == 'awaiting_approval' and sent == []
    assert saved['approvals'][0]['kind'] == 'write' and saved['checkpoints']['route']['branch'] == 'approved'


@pytest.mark.asyncio
async def test_a_resumed_run_takes_the_same_route_and_sends_once(worker_setup, model, monkeypatch):
    store, worker, connection, sent = worker_setup
    model[1].append(reading())
    original = store.worker_event
    def crash_after_route(id, owner, event):
        persisted = original(id, owner, event)
        if event.get('node_id') == 'route' and event.get('status') == 'success':
            raise RuntimeError('Injected crash after the route checkpoint')
        return persisted
    monkeypatch.setattr(store, 'worker_event', crash_after_route)
    run_id = store.create_run(routed_writes(connection, approval_on=None), MESSAGE)['id']
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run_id)['status'] == 'failed' and sent == []
    monkeypatch.setattr(store, 'worker_event', original)
    store.resume_run(run_id)
    await worker.execute(store.claim_next(worker.owner))
    final = store.run(run_id)
    assert final['status'] == 'success', final.get('error')
    assert len(sent) == 1 and sent[0].url.path == '/deliver/send_approved' and len(model[0]) == 1


EXTRACT = {'id': 'guess', 'type': 'agent', 'inputs': {'input': 'input.message'},
           'config': {'provider': 'ollama', 'model': 'fixture', 'role': 'extraction',
                      'output_schema': {'type': 'object', 'properties': {'amount': {'type': ['number', 'null']}}}}}
DOUBLE = {'id': 'calc', 'type': 'calculate', 'inputs': {'value': 'guess.text'}, 'config': {'expression': 'amount * 2'}}
BIG = [{'id': 'big', 'label': 'Big', 'when': {'field': 'result', 'operator': 'gt', 'compare_to': '500'}}]


@pytest.mark.asyncio
async def test_a_trusted_label_over_a_model_extraction_is_still_refused(model, monkeypatch):
    # The amount is quoted from the message, so the Calculate output is labelled calculated,
    # but which number is the amount is still the model's guess: not a checked value.
    monkeypatch.setattr('backend.app.compiler.request_contract_errors', lambda *a: [])
    message = '{"summary":"Invoice download is broken","category":"billing","amount":450}'
    with pytest.raises(ValueError, match='Route on checked values'):
        await run(flow('calc.text', cases=BIG, middle=(EXTRACT, DOUBLE)), message, reading(), '{"amount": 450}', model=model)


@pytest.mark.asyncio
async def test_a_restored_route_over_an_unchecked_value_is_refused(model, monkeypatch):
    # A saved route whose input was never eligible must not replay as an automatic path.
    monkeypatch.setattr('backend.app.compiler.request_contract_errors', lambda *a: [])
    raw = flow('guess.text', middle=(agent(),))
    model[1].extend([reading(), '{"category":"billing"}'])
    events = []
    async def emit(event):events.append(event)
    with pytest.raises(ValueError, match='Route on checked values'):
        await compile_workflow(Workflow.model_validate(raw), lambda _: '', emit, MESSAGE, run_id='run1').graph.ainvoke({'values': {}})
    # The same run's real checkpoints, plus a forged route for the Switch that never ran.
    saved = {e['node_id']: e['outputs'] for e in events if e.get('status') == 'success'}
    assert set(saved) == {'input', 'reader', 'guess'}
    saved['route'] = {'branch': 'billing'}
    calls = len(model[0])
    with pytest.raises(ValueError, match='Route on checked values'):
        await run(raw, model=model, completed=saved)
    assert len(model[0]) == calls
