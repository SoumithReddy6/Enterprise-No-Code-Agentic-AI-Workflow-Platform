"""Decision provenance, phase 1: every value is labelled by where it came from, and a
Condition records whether a person would have had to decide. No branch changes yet."""
import json
from decimal import Decimal
from unittest.mock import patch

import pytest

from backend.app import provenance
from backend.app.compiler import compile_workflow
from backend.app.models import Workflow
from backend.app.provenance import ABSENT, CALCULATED, GUESSED, QUOTED, SOURCE, Evidence, numbers_in

# ------------------------------------------------------------------ the quote grammars


@pytest.mark.parametrize('text,expected', [
    ('Please order three laptops at $450 each, $1,350 in total.', {3, 450, 1350}),
    ('Total: $1,350.50.', {Decimal('1350.50')}),
    ('A 12.5% discount', {Decimal('12.5')}),
    ('Balance -5 after the refund', {-5}),
    ('Due 2026-10-08', {2026, 10, 8}),
    ('a dozen pens', {12}),
    ('Twenty chairs', {20}),
    ('twenty-one chairs', set()),
    ('1,35 is not a grouped number', set()),
    ('version 2.0.1', set()),
    ('SKU123 and ABC450', set()),
    ('1000.00000000000001 exactly', {Decimal('1000.00000000000001')}),
    ('Unit price $300, quantity 4.', {300, 4}),
    ('Pay $1,350, then $45, today', {1350, 45}),
])
def test_numbers_written_in_text(text, expected):
    assert numbers_in(text) == {Decimal(v) for v in expected}


@pytest.mark.parametrize('value,label', [
    (1350, QUOTED), (Decimal('1350.0'), QUOTED), (450, QUOTED), (3, QUOTED),
    (0, GUESSED), (999.72, GUESSED), (Decimal('1000.00000000000001'), GUESSED),
    ('laptops', QUOTED), ('LAPTOPS', QUOTED), ('laptop', GUESSED), ('monitor', GUESSED), ('x', GUESSED),
    (True, GUESSED), (None, ABSENT),
])
def test_values_are_quoted_only_when_written_in_the_input(value, label):
    assert Evidence(['Please order three laptops at $450 each, $1,350 in total.']).label(value) == label


def test_booleans_are_quoted_only_from_a_structured_source_with_the_same_key():
    evidence = Evidence(['{"approved": true, "rush": false}', 'yes, approved'])
    assert evidence.label(True, 'approved') == QUOTED
    assert evidence.label(False, 'rush') == QUOTED
    assert evidence.label(True, 'rush') == GUESSED
    assert evidence.label(True, 'eligible') == GUESSED
    assert Evidence(['yes, approved']).label(True, 'approved') == GUESSED


def test_containers_are_as_trusted_as_their_weakest_member():
    evidence = Evidence(['3 laptops at $450 each'])
    assert evidence.label({'qty': 3, 'price': 450}) == QUOTED
    assert evidence.label({'qty': 3, 'price': 999}) == GUESSED
    assert evidence.label([None, None]) == ABSENT
    assert provenance.weakest([SOURCE, QUOTED]) == QUOTED


def test_field_labels_are_bounded():
    labels, truncated = provenance.field_labels({f'f{i}': i for i in range(80)}, Evidence(['1 2 3']))
    assert len(labels) == provenance.MAX_FIELDS and truncated
    record = {'v': 1, 'ports': {'text': GUESSED}, 'fields': {'text': labels}, 'truncated': True}
    assert provenance.checked(record) is record


@pytest.mark.parametrize('record', [
    None, {}, {'v': 2, 'ports': {}}, {'v': 1, 'ports': {'text': 'trusted'}}, {'v': 1, 'ports': {}, 'extra': 1},
    {'v': 1, 'ports': {}, 'fields': {'text': {'a': 'maybe'}}}, {'v': 1, 'ports': {}, 'truncated': False},
    {'v': 1, 'ports': {'text': GUESSED}, 'fields': {'text': {f'f{i}': GUESSED for i in range(65)}}},
])
def test_malformed_records_are_refused(record):
    with pytest.raises(ValueError):
        provenance.checked(record)


# ------------------------------------------------------------------ through the compiled graph

EXTRACT = {'provider': 'ollama', 'model': 'llama3.1:latest', 'role': 'extraction',
           'output_schema': {'type': 'object', 'properties': {'item': {'type': ['string', 'null']},
                                                               'amount': {'type': ['number', 'null']}}}}


def purchase(condition, extract_input='input.message', second_agent=False):
    nodes = [{'id': 'input', 'type': 'chat_input'},
             {'id': 'extract', 'type': 'agent', 'inputs': {'input': extract_input}, 'config': EXTRACT},
             {'id': 'check', 'type': 'condition', 'inputs': {'value': 'extract.text'}, 'config': condition},
             {'id': 'yes', 'type': 'response', 'inputs': {'text': 'input.message'}},
             {'id': 'no', 'type': 'response', 'inputs': {'text': 'input.message'}}]
    edges = [{'id': 'a', 'source': 'input', 'target': 'extract'}, {'id': 'b', 'source': 'extract', 'target': 'check'},
             {'id': 't', 'source': 'check', 'target': 'yes', 'sourceHandle': 'true'},
             {'id': 'f', 'source': 'check', 'target': 'no', 'sourceHandle': 'false'}]
    if second_agent:
        # The first Agent writes free text; the second extracts from that text, not the message.
        nodes.insert(1, {'id': 'draft', 'type': 'agent', 'inputs': {'input': 'input.message'},
                         'config': {'provider': 'ollama', 'model': 'llama3.1:latest'}})
        nodes[2]['inputs'] = {'input': 'draft.text'}
        edges[0] = {'id': 'a', 'source': 'input', 'target': 'draft'}
        edges.append({'id': 'd', 'source': 'draft', 'target': 'extract'})
    return Workflow.model_validate({'version': 1, 'name': 'Purchase', 'nodes': nodes, 'edges': edges})


async def run(workflow, message, *answers):
    replies = iter(answers)
    async def model(*args, **kwargs):
        kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
        return next(replies)
    events = []
    async def emit(event): events.append(event)
    with patch('backend.app.providers.ollama_chat', model):
        result = await compile_workflow(workflow, lambda _: '', emit, message).graph.ainvoke({'values': {}})
    return result['values'], events


def decision(events):
    return next(e['decision'] for e in events if e.get('node_id') == 'check' and e['status'] == 'success')


@pytest.mark.asyncio
async def test_an_invented_amount_is_guessed_and_would_need_a_person_but_the_branch_is_unchanged():
    values, events = await run(purchase({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}),
                               'How do AI workflows work?', '{"item": null, "amount": 0}')
    assert values['input']['_provenance'] == {'v': 1, 'ports': {'message': SOURCE}}
    assert values['extract']['_provenance']['fields']['text'] == {'item': ABSENT, 'amount': GUESSED}
    assert values['extract']['_provenance']['ports']['text'] == GUESSED
    assert decision(events) == {'label': GUESSED, 'would_review': True, 'reason': 'guessed'}
    assert 'no' in values and 'yes' not in values, 'shadow mode: the branch is taken exactly as before'
    assert values['check']['_provenance']['ports']['branch'] == GUESSED


@pytest.mark.asyncio
async def test_a_quoted_amount_decides_without_review():
    values, events = await run(purchase({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}),
                               'I need a monitor for $1,200.', '{"item": "monitor", "amount": 1200}')
    assert values['extract']['_provenance']['fields']['text'] == {'item': QUOTED, 'amount': QUOTED}
    assert decision(events) == {'label': QUOTED, 'would_review': False}
    assert values['check']['_provenance']['ports']['branch'] == CALCULATED


@pytest.mark.asyncio
async def test_a_miscalculated_total_is_guessed():
    # Measured on llama3.1: 7 x $142.86 came back as 999.72 and was auto-approved.
    values, events = await run(purchase({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}),
                               '7 licences at $142.86 each.', '{"item": "licences", "amount": 999.72}')
    assert decision(events)['would_review'] is True


@pytest.mark.asyncio
async def test_an_absent_value_checked_with_empty_is_an_honest_decision():
    values, events = await run(purchase({'operator': 'empty', 'field': 'amount'}),
                               'Hello, can you help me?', '{"item": null, "amount": null}')
    assert decision(events) == {'label': ABSENT, 'would_review': False}
    assert 'yes' in values


@pytest.mark.asyncio
async def test_a_value_repeated_from_another_agents_text_is_never_quoted():
    """Agent A invents 450; Agent B finds 450 in A's text. A's text is a guess, so B's
    value cannot be quoted from it, even though the digits appear there."""
    values, events = await run(purchase({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}, second_agent=True),
                               'buy something', 'The customer wants to spend $450.', '{"item": null, "amount": 450}')
    assert values['draft']['_provenance']['ports']['text'] == GUESSED
    assert values['extract']['_provenance']['fields']['text']['amount'] == GUESSED
    assert decision(events)['would_review'] is True


@pytest.mark.asyncio
async def test_examples_in_the_system_prompt_never_count_as_evidence():
    workflow = purchase({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'})
    workflow.nodes[1].config['system'] = 'Example: {"item": "laptops", "amount": 1350}'
    values, events = await run(workflow, 'buy stuff', '{"item": "laptops", "amount": 1350}')
    assert values['extract']['_provenance']['fields']['text'] == {'item': GUESSED, 'amount': GUESSED}


@pytest.mark.asyncio
async def test_a_condition_on_free_model_text_is_guessed():
    workflow = purchase({'contains': 'yes'})
    workflow.nodes[1].config = {'provider': 'ollama', 'model': 'llama3.1:latest'}
    values, events = await run(workflow, 'Is this urgent?', 'yes, it seems urgent')
    assert decision(events) == {'label': GUESSED, 'would_review': True, 'reason': 'guessed'}


@pytest.mark.asyncio
async def test_a_condition_on_the_trigger_message_is_source():
    raw = purchase({'contains': 'refund'}).model_dump(mode='json')
    raw['nodes'] = [n for n in raw['nodes'] if n['id'] != 'extract']
    raw['nodes'][1]['inputs'] = {'value': 'input.message'}
    raw['edges'] = [e for e in raw['edges'] if e['id'] != 'b']
    raw['edges'][0] = {'id': 'a', 'source': 'input', 'target': 'check'}
    values, events = await run(Workflow.model_validate(raw), 'I want a refund')
    assert decision(events) == {'label': SOURCE, 'would_review': False}


@pytest.mark.asyncio
async def test_restored_labels_are_validated_and_older_checkpoints_count_as_unlabelled():
    workflow = purchase({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'})
    completed = {'input': {'message': 'I need a monitor for $1,200.', '_provenance': {'v': 1, 'ports': {'message': SOURCE}}},
                 'extract': {'text': '{"item": "monitor", "amount": 1200}', 'provider': 'ollama', 'sources': '[]', 'grounding': '{}'}}
    events = []
    async def emit(event): events.append(event)
    await compile_workflow(workflow, lambda _: '', emit, 'x', completed=completed).graph.ainvoke({'values': {}})
    assert decision(events) == {'label': GUESSED, 'would_review': True, 'reason': 'unlabelled'}
    completed['extract']['_provenance'] = {'v': 1, 'ports': {'text': 'trusted-by-me'}}
    with pytest.raises(ValueError, match='extract cannot be restored: its checkpoint carries malformed provenance metadata'):
        await compile_workflow(workflow, lambda _: '', emit, 'x', completed=completed).graph.ainvoke({'values': {}})


@pytest.mark.asyncio
async def test_labels_persist_in_checkpoints_through_the_real_worker(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store = Store(f'sqlite:///{tmp_path}/p.db', Fernet.generate_key())
    store.allow_model('ollama', 'llama3.1:latest', '', 'local')
    async def model(*args, **kwargs):
        kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
        return '{"item": "monitor", "amount": 1200}'
    monkeypatch.setattr('backend.app.providers.ollama_chat', model)
    workflow = purchase({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}).model_dump(mode='json')
    created = store.create_run(workflow, 'I need a monitor for $1,200.')
    worker = Worker(store)
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(created['id'])
    assert run['status'] == 'success', run.get('error')
    assert run['checkpoints']['extract']['_provenance']['fields']['text'] == {'item': QUOTED, 'amount': QUOTED}
    assert [e['decision'] for e in run['events'] if e.get('node_id') == 'check' and e.get('status') == 'success'] == [
        {'label': QUOTED, 'would_review': False}]
    store.engine.dispose()
