"""Permanent acceptance tests for the independent R01–R12 audit.

Responses are controlled deliberately: these test unsafe model outputs, not predictions
about what a live model will say. All compilation, labels and durable replay are real.
"""
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from backend.app import provenance as p
from backend.app.calculate import run as calculate
from backend.app.compiler import compile_workflow, validate_workflow
from backend.app.models import Workflow, Node, Edge
from backend.app.request_safety import UnsafeExecutionError

MODEL = {'provider': 'ollama', 'model': 'llama3.1:latest'}
STRUCTURED = {**MODEL, 'role': 'extraction', 'output_schema': {'type': 'object'}}


def agent(name='extract', source='input.message', config=None):
    return {'id': name, 'type': 'agent', 'inputs': {'input': source}, 'config': config or STRUCTURED}


def flow(middle, decide='extract.text', field='amount', operator='gt'):
    nodes = [{'id': 'input', 'type': 'chat_input'}, *middle]
    if decide:
        config = {'operator': operator, 'field': field}
        if operator == 'gt': config['compare_to'] = '1000'
        nodes.append({'id': 'check', 'type': 'condition', 'inputs': {'value': decide}, 'config': config})
    else:
        nodes.append({'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}})
    edges = [{'id': f'e{i}', 'source': a['id'], 'target': b['id']} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))]
    if decide:
        for route in ('true', 'false'):
            nodes.append({'id': route, 'type': 'response', 'inputs': {'text': 'input.message'}})
            edges.append({'id': route, 'source': 'check', 'target': route, 'sourceHandle': route})
    wf = Workflow.model_validate({'name': 'Provenance boundary', 'nodes': nodes, 'edges': edges})
    assert not validate_workflow(wf)
    return wf


async def execute(wf, message, answers=(), platform=None, completed=None, progress=None, events=None):
    replies, calls = iter(answers), []
    events = [] if events is None else events
    async def model(*args, **kwargs):
        calls.append(True)
        kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
        return next(replies)
    async def emit(event): events.append(event)
    with patch('backend.app.providers.ollama_chat', model):
        values = (await compile_workflow(wf, lambda _: '', emit, message, platform_resolver=platform,
                                        completed=completed, loop_progress=progress).graph.ainvoke({'values': {}}))['values']
    decisions = {e['node_id']: e['decision'] for e in events if 'decision' in e}
    return values, events, decisions, len(calls)


@pytest.mark.asyncio
async def test_prompt_examples_are_not_eligible_evidence():
    prompt = {'id': 'prompt', 'type': 'prompt', 'inputs': {'message': 'input.message'},
              'config': {'template': 'Example: {{"amount":0}}\nRequest: {message}'}}
    _, _, decisions, _ = await execute(flow([prompt, agent(source='prompt.text')]), 'Hello!', ['{"amount":0}'])
    assert decisions['check']['label'] == p.GUESSED
    assert decisions['check']['would_review'] is True


@pytest.mark.asyncio
async def test_python_echo_cannot_promote_a_model_guess():
    async def echo(*args): return args[3] + '\n'
    wf = flow([agent('draft', config=MODEL), {'id': 'echo', 'type': 'tool_python',
              'inputs': {'input': 'draft.text'}, 'config': {}}, agent(source='echo.text')])
    values, _, decisions, _ = await execute(wf, 'Hello!', ['0', '{"amount":0}'], echo)
    assert values['echo']['_provenance']['ports']['text'] == p.GUESSED
    assert decisions['check']['would_review'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('answer', ['{"order":{"amount":0},"order.amount":1200}',
                                  '{"order.amount":1200,"order":{"amount":0}}'])
async def test_field_identity_is_insertion_order_independent(answer):
    values, _, decisions, _ = await execute(flow([agent()], field='order.amount'), 'Order monitors for $1200.', [answer])
    assert decisions['check']['label'] == p.GUESSED
    assert decisions['check']['would_review'] is True
    assert values['check']['branch'] == 'false'


@pytest.mark.asyncio
@pytest.mark.parametrize('answer,label', [('1', p.GUESSED), ('1000', p.QUOTED)])
async def test_exponents_quote_the_exact_value_never_the_prefix(answer, label):
    _, _, decisions, _ = await execute(flow([agent()]), '{"amount":1e3}', ['{"amount":' + answer + '}'])
    assert decisions['check']['label'] == label


@pytest.mark.parametrize('text', ['1e', '1e+', '1e-3oops', '1,35', '2.0.1', '12abc'])
def test_unsupported_numeric_tokens_do_not_quote_their_prefix(text):
    assert p.numbers_in(text) == set()


@pytest.mark.parametrize('expression,expected', [('a + b', str(2 * int('9' * 30))), ('a - a', '0'),
    ('(1 / 3) - (1 / 3)', '0'), ('round((1 / 3) * 3, 2)', '1.00')])
def test_only_the_selected_arithmetic_operator_runs(expression, expected):
    assert calculate(expression, json.dumps({'a': int('9' * 30), 'b': int('9' * 30)}), 'compute')[0] == Decimal(expected)


@pytest.mark.parametrize('expression,sign', [('-a', -1), ('abs(a)', 1)])
def test_sign_operations_preserve_every_digit(expression, sign):
    number = int('1234567890' * 6 + '1')
    actual = calculate(expression, json.dumps({'a': number if sign == -1 else -number}), 'compute')[0]
    assert actual == sign * number


@pytest.mark.asyncio
async def test_64_long_fields_complete_through_the_worker(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    from backend.app.worker import Worker
    answer = json.dumps({f'field{i}' + 'x' * 110: i for i in range(64)})
    async def model(*args, **kwargs):
        kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
        return answer
    monkeypatch.setattr('backend.app.providers.ollama_chat', model)
    store = Store(f'sqlite:///{tmp_path}/audit.db', Fernet.generate_key())
    try:
        store.allow_model('ollama', 'llama3.1:latest')
        wf = flow([agent()], decide=None)
        wf.nodes[-1].inputs = {'text': 'extract.text'}
        created = store.create_run(wf.model_dump(mode='json'), 'Hello!')
        worker = Worker(store)
        await worker.execute(store.claim_next(worker.owner))
        saved = store.run(created['id'])
        assert saved['status'] == 'success', saved.get('error')
        record = saved['checkpoints']['extract']['_provenance']
        assert len(json.dumps(record, ensure_ascii=False).encode()) <= p.MAX_BYTES
        assert record['truncated'] is True
        assert json.loads(saved['checkpoints']['extract']['text']) == json.loads(answer)
        assert json.loads(saved['output']) == json.loads(answer)
    finally:
        store.engine.dispose()


@pytest.mark.parametrize('expression,mode', [('coalesce(1200, amount)', 'compute'),
    ('(1 == 2) and (amount > 0)', 'check'), ('(1 == 1) or (amount > 0)', 'check')])
def test_static_short_circuits_do_not_report_unreachable_guesses(expression, mode):
    wf = flow([agent('draft', config=MODEL), {'id': 'calc', 'type': 'calculate',
              'inputs': {'value': 'draft.text'}, 'config': {'expression': expression, 'mode': mode}}],
              decide='calc.text', field='result', operator='empty')
    assert p.decision_warnings(wf) == []
    assert p.static_label({n.id: n for n in wf.nodes}, 'calc.text', 'result') == p.STATIC_TRUSTED


@pytest.mark.asyncio
async def test_checkpoint_replay_preserves_the_original_decision():
    wf = flow([agent()])
    values, _, original, _ = await execute(wf, 'Hello!', ['{"amount":0}'])
    _, events, replayed, calls = await execute(wf, 'Hello!', completed=values)
    assert calls == 0
    assert replayed['check'] == original['check']
    assert next(e for e in events if e.get('node_id') == 'check')['cached'] is True


@pytest.mark.asyncio
async def test_depth_limit_means_unknown_not_absent():
    deep = 0
    for _ in range(40): deep = [deep]
    _, _, decisions, _ = await execute(flow([agent()], field='items', operator='empty'), 'Hello!', [json.dumps({'items': deep})])
    assert decisions['check']['label'] == p.GUESSED
    assert decisions['check']['would_review'] is True


def loop_workflow():
    return Workflow.model_validate({'name': 'Quoted loop', 'nodes': [
        {'id': 'input', 'type': 'chat_input'}, {'id': 'tickets', 'type': 'tool_jira',
         'inputs': {'input': 'input.message'}, 'config': {'connection_id': 'audit', 'operation': 'search'}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'}, 'config': {'body': 'work'}},
        {'id': 'work', 'type': 'agent', 'config': STRUCTURED},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'tickets'}, {'id': 'b', 'source': 'tickets', 'target': 'each'},
                  {'id': 'c', 'source': 'each', 'target': 'out'}, {'id': 'body', 'source': 'each', 'target': 'work',
                   'kind': 'loop', 'sourceHandle': 'body', 'targetHandle': 'input'}]})


@pytest.mark.asyncio
async def test_attached_labels_and_item_labels_survive_resume():
    async def jira(*args):
        return {'text': '{"issues":[]}', 'items': [{'amount': 1200}, {'amount': 1350}], 'truncated': False, 'warnings': []}
    values, events, _, _ = await execute(loop_workflow(), 'go', ['{"amount":1200}', '{"amount":1350}'], jira)
    bodies = [e for e in events if e.get('node_id') == 'work' and e.get('status') == 'success']
    assert len(bodies) == 2
    assert all(e['outputs']['_provenance']['fields']['text']['/amount'] == p.QUOTED for e in bodies)
    results = values['each']['results']
    assert all(r['_provenance']['ports']['text'] == p.QUOTED for r in results)
    assert values['each']['_provenance']['ports']['results'] in p.TRUSTED
    progress = {'each': {'0': results[0]}}
    completed = {k: values[k] for k in ('input', 'tickets')}
    resumed, _, _, calls = await execute(loop_workflow(), 'go', ['{"amount":1350}'], jira, completed, progress)
    assert calls == 1
    assert resumed['each'] == values['each']


@pytest.mark.asyncio
async def test_grounding_answer_does_not_launder_a_model_guess():
    wf = flow([agent('draft', config=MODEL), agent(source='draft.grounding')])
    envelope = json.dumps({'question': 'Cost?', 'passages': [{'citation': 'S1', 'filename': 'catalogue.txt',
                          'page': 1, 'text': 'Laptops are listed without prices.'}]})
    first = json.dumps({'answer': 'The amount is $0 [S1]', 'citations': ['S1'], 'abstain': False, 'reason': ''})
    events = []
    # R01: the typed passage has no knowledge source and this run has no verifier, so the
    # terminal Response refuses to publish it. The decision below is made before that.
    with pytest.raises(UnsafeExecutionError):
        await execute(wf, envelope, [first, '{"amount":0}'], events=events)
    draft = next(e for e in events if e.get('node_id') == 'draft' and e.get('status') == 'success')
    assert draft['outputs']['_provenance']['ports']['grounding'] == p.GUESSED
    decisions = {e['node_id']: e['decision'] for e in events if 'decision' in e}
    assert decisions['check']['would_review'] is True


def test_pointer_fields_never_collide_and_omissions_never_become_trusted():
    labels, _ = p.field_labels({'a/b': {'~c': 0}, 'a': {'b': {'~c': 1200}}}, p.Evidence(['1200']))
    assert labels == {'/a~1b/~0c': p.GUESSED, '/a/b/~0c': p.QUOTED}
    answer = {('é' * 100) + str(i): 1200 for i in range(64)}
    labels, omitted = p.field_labels(answer, p.Evidence(['1200']))
    assert omitted
    record = {'v': 2, 'ports': {'text': p.GUESSED}, 'fields': {'text': labels}, 'truncated': True}
    p.checked(record)
    missing = next(k for k in answer if p.pointer([k]) not in labels)
    assert p.value_label({'a': {'text': json.dumps(answer), '_provenance': record}}, 'a.text', missing) == (p.GUESSED, 'unrecorded_field')


@pytest.mark.parametrize('value,expected', [(None, p.ABSENT), ([], p.ABSENT), ({}, p.ABSENT), ([None], p.ABSENT)])
def test_real_absence_still_differs_from_incomplete_traversal(value, expected):
    assert p.Evidence(['Hello']).label(value) == expected


def test_evidence_context_is_specific_to_resolved_data_and_has_origin_digests():
    import hashlib
    wf = flow([agent()])
    values = {'input': {'message': '{"amount":1200}', '_provenance': {
        'v': 1, 'ports': {'message': p.SOURCE}, 'eligible_ports': ['message']}},
        'unbound': {'text': '0', '_provenance': {'v': 1, 'ports': {'text': p.SOURCE}, 'eligible_ports': ['text']}}}
    context = p.evidence_context(wf.nodes[1], {'input': values['input']['message']}, values)
    assert context.eligible_texts == ('{"amount":1200}',)
    source = context.structured_sources[0]
    assert (source['node_id'], source['port']) == ('input', 'message')
    assert source['sha256'] == hashlib.sha256(context.eligible_texts[0].encode()).hexdigest()
    assert p.Evidence(context).label(0) == p.GUESSED


@pytest.mark.parametrize('text', ['1e--3', '1e++3', '--3', '++3', '1e-+3', '1e+-3'])
def test_malformed_signs_cannot_restart_a_numeric_match(text):
    assert p.numbers_in(text) == set()


def test_old_dotted_parent_keys_cannot_launder_nested_labels():
    value = {'a': {'b': {'c': 0}}, 'a.b': {'c': 1200}}
    record = {'v': 1, 'ports': {'text': p.GUESSED}, 'fields': {'text': {'a.b.c': p.QUOTED}}}
    assert p.value_label({'extract': {'text': json.dumps(value), '_provenance': record}}, 'extract.text', 'a.b.c')[0] == p.GUESSED


@pytest.mark.asyncio
async def test_legacy_opaque_tool_labels_are_not_used_for_new_decisions():
    wf = flow([{'id': 'echo', 'type': 'tool_python', 'inputs': {'input': 'input.message'}, 'config': {}}], decide='echo.text')
    completed = {'input': {'message': 'Hello!'}, 'echo': {'text': '{"amount":0}',
                  '_provenance': {'v': 1, 'ports': {'text': p.SOURCE}}}}
    _, _, decisions, calls = await execute(wf, 'Hello!', completed=completed)
    assert calls == 0
    assert decisions['check']['would_review'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['agent', 'calculate', 'prompt'])
async def test_pre_fix_derived_labels_cannot_authorize_new_decisions(kind):
    middle = agent('old') if kind == 'agent' else {'id': 'old', 'type': kind,
        'inputs': {'value' if kind == 'calculate' else 'message': 'input.message'},
        'config': {'expression': 'amount + 1'} if kind == 'calculate' else {}}
    field = 'result' if kind == 'calculate' else 'amount'
    wf = flow([middle], decide='old.text', field=field)
    completed = {'input': {'message': '{"amount":1e3}'}, 'old': {
        'text': json.dumps({field: 1}), '_provenance': {'v': 1,
        'ports': {'text': p.CALCULATED}, 'fields': {'text': {field: p.QUOTED}}}}}
    _, _, decisions, calls = await execute(wf, 'unused', completed=completed)
    assert calls == 0
    assert decisions['check']['would_review'] is True


def test_near_cap_legacy_labels_are_downgraded_without_breaking_restore():
    node = Node.model_validate(agent())
    record = {'v': 1, 'ports': {'text': p.GUESSED}, 'fields': {'text': {
        f'f{i}_' + 'x' * 99: p.QUOTED for i in range(34)}}}
    p.checked(record)
    restored = p.restored_record(node, record)
    assert len(json.dumps(restored, ensure_ascii=False).encode()) <= p.MAX_BYTES
    assert p.output_label({'_provenance': restored}) == p.GUESSED
    assert all(label == p.GUESSED for fields in restored.get('fields', {}).values() for label in fields.values())


@pytest.mark.asyncio
async def test_nullable_calculation_does_not_claim_every_run_will_review():
    wf = flow([agent('draft', config=MODEL), {'id': 'calc', 'type': 'calculate',
              'inputs': {'value': 'draft.text'}, 'config': {'expression': 'amount + 1'}}],
              decide='calc.text', field='result', operator='empty')
    assert p.static_label({n.id: n for n in wf.nodes}, 'calc.text', 'result') == p.STATIC_RUNTIME
    assert all('always' not in w and 'every run' not in w for w in p.decision_warnings(wf))
    _, _, decisions, _ = await execute(wf, 'Hello!', ['{"amount":null}'])
    assert decisions['check']['label'] == p.ABSENT
    assert decisions['check']['would_review'] is False


@pytest.mark.asyncio
async def test_cached_loop_refuses_malformed_nested_item_metadata_before_downstream():
    from copy import deepcopy
    async def jira(*args):
        return {'text': '{"issues":[]}', 'items': [{'amount': 1200}], 'truncated': False, 'warnings': []}
    values, _, _, _ = await execute(loop_workflow(), 'go', ['{"amount":1200}'], jira)
    damaged = deepcopy(values)
    damaged['each']['results'][0]['_provenance'] = {'v': 99, 'ports': {'text': p.SOURCE}}
    with pytest.raises(ValueError, match='malformed provenance'):
        await execute(loop_workflow(), 'go', completed=damaged)


def test_retrieval_question_and_filenames_are_not_passage_evidence():
    from backend.app.registry import REGISTRY
    node = Node.model_validate({'id': 'retrieve', 'type': 'retrieve', 'inputs': {'query': 'draft.text'}})
    state = {'draft': {'text': 'amount 0', '_provenance': {'v': 1, 'ports': {'text': p.GUESSED}}}}
    passage = {'citation': 'S1', 'filename': 'price-450.txt', 'page': 1200, 'text': 'Laptops are available.'}
    outputs = {'query': 'amount 0', 'context': json.dumps({'question': 'amount 0', 'passages': [passage]}),
               'sources': json.dumps([passage])}
    outputs['_provenance'] = p.for_node(node, REGISTRY['retrieve'], {'query': 'amount 0'}, outputs, state)
    extract = Node.model_validate(agent(source='retrieve.context'))
    context = p.evidence_context(extract, {'input': outputs['context']}, {'retrieve': outputs})
    evidence = p.Evidence(context)
    assert evidence.label(0) == p.GUESSED
    assert evidence.label(1200) == p.GUESSED
    assert evidence.label('Laptops') == p.QUOTED


@pytest.mark.asyncio
async def test_a_sibling_item_cannot_quote_another_items_amount():
    async def jira(*args):
        return {'text': '{"issues":[]}', 'items': [{'amount': 1200}, {'amount': 1350}], 'truncated': False, 'warnings': []}
    values, events, _, _ = await execute(loop_workflow(), 'go', ['{"amount":1350}', '{"amount":1350}'], jira)
    bodies = [e for e in events if e.get('node_id') == 'work' and e.get('status') == 'success']
    assert bodies[0]['outputs']['_provenance']['fields']['text']['/amount'] == p.GUESSED
    assert bodies[1]['outputs']['_provenance']['fields']['text']['/amount'] == p.QUOTED
    assert values['each']['_provenance']['ports']['results'] == p.GUESSED


@pytest.mark.asyncio
async def test_specialists_and_agent_tools_have_labels_without_promoting_the_agent_task(monkeypatch):
    from backend.tests.test_agent_tools import calc_flow, scripted
    wf = calc_flow()
    wf.nodes += [Node.model_validate(
        {'id': 'specialist', 'type': 'agent', 'config': STRUCTURED})]
    wf.edges += [Edge.model_validate(
        {'id': 'specialist-link', 'source': 'agent', 'target': 'specialist', 'kind': 'agent'})]
    scripted(monkeypatch, ['{"action":"call","target":"calc","input":"0"}',
                          '{"action":"call","target":"specialist","input":"{\\"amount\\":0}"}',
                          '{"amount":0}', '{"action":"final","text":"Done"}'])
    events = []
    async def platform(*args): return '0'
    async def emit(event): events.append(event)
    await compile_workflow(wf, platform_resolver=platform, emit=emit, message='Hello!').graph.ainvoke({'values': {}})
    successes = {e['node_id']: e for e in events if e.get('status') == 'success'}
    assert successes['calc']['outputs']['_provenance']['ports']['text'] == p.GUESSED
    assert successes['specialist']['outputs']['_provenance']['fields']['text']['/amount'] == p.GUESSED


@pytest.mark.asyncio
async def test_durable_item_labels_replay_without_a_second_model_call(tmp_path, monkeypatch):
    import asyncio
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store = Store(f'sqlite:///{tmp_path}/items.db', Fernet.generate_key())
    try:
        store.allow_model('ollama', 'llama3.1:latest')
        wf = loop_workflow()
        from backend.tests.test_iteration import jira_connection
        wf.nodes[1].config['connection_id'] = jira_connection(store)
        replies = iter(['{"amount":1200}', '{"amount":1350}']); calls = []
        async def model(*args, **kwargs):
            kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
            calls.append(True); return next(replies)
        monkeypatch.setattr('backend.app.providers.ollama_chat', model)
        worker = Worker(store)
        async def search(*args, **kwargs):
            return json.dumps({'issues': [{'key': f'AUDIT-{i}', 'fields': {'summary': f'Amount {amount}'}}
                                         for i, amount in enumerate((1200, 1350))]})
        monkeypatch.setattr(worker.tools, 'execute_prepared', search)
        original = store.worker_event
        def interrupt(id, owner, event):
            result = original(id, owner, event)
            if event.get('kind') == 'loop_item' and event['item_index'] == 0: raise asyncio.CancelledError()
            return result
        monkeypatch.setattr(store, 'worker_event', interrupt)
        created = store.create_run(wf.model_dump(mode='json'), 'go')
        await worker.execute(store.claim_next(worker.owner))
        first = store.run(created['id'])
        assert first['loop_progress']['each']['0']['_provenance']['fields']['text']['/amount'] == p.QUOTED
        monkeypatch.setattr(store, 'worker_event', original)
        store.resume_run(created['id'])
        await worker.execute(store.claim_next(worker.owner))
        final = store.run(created['id'])
        assert final['status'] == 'success', final.get('error')
        assert len(calls) == 2
        assert final['checkpoints']['each']['_provenance']['ports']['results'] == p.QUOTED
        assert all(r['_provenance']['fields']['text']['/amount'] == p.QUOTED for r in final['checkpoints']['each']['results'])
    finally:
        store.engine.dispose()
