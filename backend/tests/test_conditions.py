"""Typed conditions (A5.1): the legacy contains condition is unchanged, every operator
compares the type it states, and anything it cannot compare is a named error."""
import itertools
import random

import pytest
from pydantic import ValidationError

from backend.app.compiler import compile_workflow, validate_workflow
from backend.app.conditions import ConditionConfig, evaluate
from backend.app.models import Workflow


def legacy_condition(value, contains, case_sensitive=False):
    """The condition node as it was before A5.1, kept verbatim as the reference."""
    needle = contains
    if not case_sensitive:
        value, needle = value.casefold(), needle.casefold()
    return needle in value


def check(value, **config):
    return evaluate(value, ConditionConfig.model_validate(config))


def workflow(config, message='', on_error='fail'):
    return Workflow.model_validate({'version': 1, 'name': 'Route', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'check', 'type': 'condition', 'inputs': {'value': 'input.message'}, 'config': config, 'on_error': on_error},
        {'id': 'yes', 'type': 'response', 'inputs': {'text': 'input.message'}},
        {'id': 'no', 'type': 'response', 'inputs': {'text': 'input.message'}},
    ], 'edges': [
        {'id': 'a', 'source': 'input', 'target': 'check'},
        {'id': 'b', 'source': 'check', 'target': 'yes', 'sourceHandle': 'true'},
        {'id': 'c', 'source': 'check', 'target': 'no', 'sourceHandle': 'false'},
    ]})


async def route(config, message):
    events = []
    async def emit(event): events.append(event)
    result = await compile_workflow(workflow(config), lambda _: '', emit, message).graph.ainvoke({'values': {}})
    return ({'yes', 'no'} & set(result['values'])), events


# ------------------------------------------------------------------ legacy behaviour


TEXTS = ['', ' ', 'refund', 'REFUND please', 'no refund', 'Straße', 'STRASSE', 'strasse', 'İstanbul',
         'i̇stanbul', 'ﬁle', 'FILE', 'Ǆ', 'ǆ', 'café', 'café', '😀 refund 😀', 'line\nbreak', '\tTabbed ']
NEEDLES = ['refund', 'REFUND', 'ß', 'ss', 'SS', 'i̇', 'İ', 'fi', 'ﬁ', 'ǅ', 'é', 'é', '😀', ' ', '\n', 'x']


def test_legacy_conditions_choose_the_same_branch_as_before():
    for value, needle, case_sensitive in itertools.product(TEXTS, NEEDLES, (False, True)):
        assert check(value, contains=needle, case_sensitive=case_sensitive) == legacy_condition(value, needle, case_sensitive), (value, needle, case_sensitive)
    pool = 'abcABCßSsİiı̇ﬁé́ \n😀Ǆǅǆ'
    rng = random.Random(51)
    for _ in range(20000):
        value = ''.join(rng.choice(pool) for _ in range(rng.randint(0, 12)))
        needle = ''.join(rng.choice(pool) for _ in range(rng.randint(1, 4)))
        case_sensitive = rng.random() < 0.5
        assert check(value, contains=needle, case_sensitive=case_sensitive) == legacy_condition(value, needle, case_sensitive), (value, needle)


def test_legacy_configurations_validate_exactly_as_before():
    for config in ({'contains': 'x'}, {'contains': 'x', 'case_sensitive': True}, {'contains': 'x' * 1000}):
        assert ConditionConfig.model_validate(config).operator == 'contains'
    for config in ({}, {'contains': ''}, {'contains': 'x' * 1001}, {'contains': 'x', 'api_key': 'secret'}, {'case_sensitive': True}):
        with pytest.raises(ValidationError):
            ConditionConfig.model_validate(config)


@pytest.mark.asyncio
@pytest.mark.parametrize('message,chosen', [('I want a refund for order 42', 'yes'), ('Where is my order?', 'no')])
async def test_a_saved_legacy_condition_routes_unchanged(message, chosen):
    taken, _ = await route({'contains': 'refund'}, message)
    assert taken == {chosen}


# ------------------------------------------------------------------ operator matrix


@pytest.mark.parametrize('value,config,expected', [
    # Ordering compares numbers: text holding a number, JSON numbers, exact decimals.
    ('1200', {'operator': 'gt', 'compare_to': '1000'}, True),
    (' 1000 ', {'operator': 'gt', 'compare_to': '1000'}, False),
    ('1000', {'operator': 'gte', 'compare_to': '1000'}, True),
    ('-5', {'operator': 'lt', 'compare_to': '0'}, True),
    ('2.5e3', {'operator': 'lte', 'compare_to': '2500'}, True),
    ('.5', {'operator': 'lt', 'compare_to': '0.6'}, True),
    ('0.30000000000000004', {'operator': 'gt', 'compare_to': '0.3'}, True),
    ('9007199254740993', {'operator': 'gt', 'compare_to': '9007199254740992'}, True),
    ('{"amount": 1200}', {'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}, True),
    ('{"amount": 999.99}', {'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}, False),
    ('{"amount": "1500"}', {'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}, True),
    # JSON numbers keep their exact digits (R01): no binary-float rounding before comparison.
    ('{"amount": 1000.00000000000001}', {'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}, True),
    ('{"amount": 9007199254740993.0}', {'operator': 'gt', 'compare_to': '9007199254740992', 'field': 'amount'}, True),
    ('{"amount": 0.100000000000000005}', {'operator': 'eq', 'compare_to': '0.1', 'compare_as': 'number', 'field': 'amount'}, False),
    ('{"amount": 1e309}', {'operator': 'gt', 'compare_to': '1e308', 'field': 'amount'}, True),
    ('{"code": 404.00000000000001}', {'operator': 'in', 'options': ['404'], 'compare_as': 'number', 'field': 'code'}, False),
    # eq / ne compare trimmed text, case-insensitively by default.
    ('High', {'operator': 'eq', 'compare_to': 'high'}, True),
    (' high\n', {'operator': 'eq', 'compare_to': 'high'}, True),
    ('High', {'operator': 'eq', 'compare_to': 'high', 'case_sensitive': True}, False),
    ('highest', {'operator': 'eq', 'compare_to': 'high'}, False),
    ('low', {'operator': 'ne', 'compare_to': 'high'}, True),
    ('HIGH', {'operator': 'ne', 'compare_to': 'high'}, False),
    ('02134', {'operator': 'eq', 'compare_to': '2134'}, False),
    ('{"done": true}', {'operator': 'eq', 'compare_to': 'true', 'field': 'done'}, True),
    # compare_as number makes eq / ne / in numeric.
    ('02134', {'operator': 'eq', 'compare_to': '2134', 'compare_as': 'number'}, True),
    ('10.0', {'operator': 'eq', 'compare_to': '10', 'compare_as': 'number'}, True),
    ('10.5', {'operator': 'ne', 'compare_to': '10', 'compare_as': 'number'}, True),
    ('{"code": 404}', {'operator': 'in', 'options': ['404', '410'], 'compare_as': 'number', 'field': 'code'}, True),
    # in is membership in a list of options.
    ('Urgent', {'operator': 'in', 'options': ['high', 'urgent']}, True),
    ('medium', {'operator': 'in', 'options': ['high', 'urgent']}, False),
    ('Urgent', {'operator': 'in', 'options': ['high', 'urgent'], 'case_sensitive': True}, False),
    # empty: blank text, null, an empty list or object; never a number or boolean.
    ('', {'operator': 'empty'}, True),
    ('  \n', {'operator': 'empty'}, True),
    ('x', {'operator': 'empty'}, False),
    ('{"items": []}', {'operator': 'empty', 'field': 'items'}, True),
    ('{"items": [1]}', {'operator': 'empty', 'field': 'items'}, False),
    ('{"note": null}', {'operator': 'empty', 'field': 'note'}, True),
    ('{"meta": {}}', {'operator': 'empty', 'field': 'meta'}, True),
    ('{"count": 0}', {'operator': 'empty', 'field': 'count'}, False),
    ('{"flag": false}', {'operator': 'empty', 'field': 'flag'}, False),
    # contains with a field reads the field's text.
    ('{"order": {"note": "Refund requested"}}', {'contains': 'refund', 'field': 'order.note'}, True),
    ('{"qty": 15}', {'contains': '5', 'field': 'qty'}, True),
])
def test_operator_matrix(value, config, expected):
    assert check(value, **config) is expected


@pytest.mark.parametrize('value,config,message', [
    ('about 1200', {'operator': 'gt', 'compare_to': '1000'}, "Condition value 'about 1200' is not a number."),
    ('1,200', {'operator': 'gt', 'compare_to': '1000'}, "Condition value '1,200' is not a number."),
    ('$1200', {'operator': 'gte', 'compare_to': '1000'}, "Condition value '$1200' is not a number."),
    ('NaN', {'operator': 'lt', 'compare_to': '1'}, "Condition value 'NaN' is not a number."),
    ('Infinity', {'operator': 'lt', 'compare_to': '1'}, "Condition value 'Infinity' is not a number."),
    ('', {'operator': 'gt', 'compare_to': '0'}, "Condition value '' is not a number."),
    ('1e9999999999999999999', {'operator': 'gt', 'compare_to': '0'}, 'is not a number.'),
    ('{"amount": true}', {'operator': 'gt', 'compare_to': '1', 'field': 'amount'}, 'Condition field amount is a boolean, not a number.'),
    ('{"amount": null}', {'operator': 'gt', 'compare_to': '1', 'field': 'amount'}, 'Condition field amount is null, not a number.'),
    ('{"amount": [1]}', {'operator': 'gt', 'compare_to': '1', 'field': 'amount'}, 'Condition field amount is a list, not a number.'),
    ('{"amount": NaN}', {'operator': 'gt', 'compare_to': '1', 'field': 'amount'}, 'Condition field amount: NaN is not a JSON number.'),
    ('{"amount": Infinity}', {'operator': 'gt', 'compare_to': '1', 'field': 'amount'}, 'Condition field amount: Infinity is not a JSON number.'),
    ('{"tags": ["a"]}', {'operator': 'eq', 'compare_to': 'a', 'field': 'tags'}, 'Condition field tags is a list, not text; use the empty operator, or choose a field.'),
    ('{"note": null}', {'operator': 'eq', 'compare_to': 'a', 'field': 'note'}, 'Condition field note is null, not text; use the empty operator to test for it.'),
    ('{"order": {}}', {'operator': 'gt', 'compare_to': '1', 'field': 'order.amount'}, "Condition field order.amount: 'amount' is missing."),
    ('not json', {'operator': 'gt', 'compare_to': '1', 'field': 'amount'}, 'Condition field amount: the value is not JSON, so it has no fields.'),
    ('[{"amount": 1}]', {'operator': 'gt', 'compare_to': '1', 'field': 'amount'}, 'Condition field amount: the value is a list, not an object.'),
    ('{"order": "x"}', {'operator': 'gt', 'compare_to': '1', 'field': 'order.amount'}, 'Condition field order.amount: order is text, not an object.'),
    ('abc', {'operator': 'eq', 'compare_to': '1', 'compare_as': 'number'}, "Condition value 'abc' is not a number."),
])
def test_values_that_cannot_be_compared_are_named_errors(value, config, message):
    with pytest.raises(ValueError) as caught:
        check(value, **config)
    assert message in str(caught.value)


@pytest.mark.parametrize('config,message', [
    ({'operator': 'gt'}, 'The gt operator needs compare_to.'),
    ({'operator': 'in'}, 'The in operator needs options.'),
    ({'operator': 'eq', 'contains': 'x'}, 'The eq operator needs compare_to.'),
    ({'operator': 'gt', 'compare_to': '1', 'contains': 'x'}, 'contains is not used by the gt operator; remove it.'),
    ({'operator': 'empty', 'compare_to': '1'}, 'compare_to is not used by the empty operator; remove it.'),
    ({'contains': 'x', 'options': ['a']}, 'options is not used by the contains operator; remove it.'),
    ({'operator': 'gt', 'compare_to': 'a thousand'}, "gt compares numbers, but compare_to 'a thousand' is not a number."),
    ({'operator': 'lte', 'compare_to': '1,000'}, "lte compares numbers, but compare_to '1,000' is not a number."),
    ({'operator': 'eq', 'compare_to': 'x', 'compare_as': 'number'}, "eq compares numbers, but compare_to 'x' is not a number."),
    ({'operator': 'in', 'options': ['1', 'two'], 'compare_as': 'number'}, "in compares numbers, but option 'two' is not a number."),
    ({'contains': 'x', 'compare_as': 'number'}, 'compare_as number does not apply to the contains operator.'),
    ({'operator': 'empty', 'compare_as': 'number'}, 'compare_as number does not apply to the empty operator.'),
    ({'operator': 'empty', 'field': 'order..amount'}, 'field must be an object key or a dotted path'),
    ({'operator': 'empty', 'field': 'items[0]'}, 'field must be an object key or a dotted path'),
    ({'operator': 'in', 'options': []}, 'List should have at least 1 item'),
    ({'operator': 'in', 'options': ['x' * 1001]}, 'Each option must be at most 1000 characters.'),
    ({'operator': 'matches', 'compare_to': 'a.*'}, "Input should be 'contains', 'eq'"),
])
def test_invalid_comparisons_are_reported_clearly_at_validation(config, message):
    errors = validate_workflow(workflow(config))
    assert any(message in error for error in errors), errors
    assert all(error.startswith('check: ') for error in errors if 'check' in error)
    assert not any('invalid configuration' in error for error in errors), errors


def test_other_node_configuration_errors_stay_generic():
    raw = workflow({'contains': 'x'}).model_dump(mode='json')
    raw['nodes'].insert(1, {'id': 'prompt', 'type': 'prompt', 'config': {'template': 'x', 'api_key': 'secret-value'}, 'inputs': {'message': 'input.message'}})
    errors = validate_workflow(Workflow.model_validate(raw))
    assert any(error.startswith('prompt: invalid configuration.') for error in errors), errors
    assert not any('secret-value' in error for error in errors)


# ------------------------------------------------------------------ through the compiled graph


@pytest.mark.asyncio
@pytest.mark.parametrize('config,message,chosen', [
    ({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}, '{"amount": 1200, "item": "laptop"}', 'yes'),
    ({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}, '{"amount": 80, "item": "mouse"}', 'no'),
    ({'operator': 'in', 'options': ['high', 'urgent'], 'field': 'priority'}, '{"priority": "Urgent"}', 'yes'),
    ({'operator': 'in', 'options': ['high', 'urgent'], 'field': 'priority'}, '{"priority": "low"}', 'no'),
    ({'operator': 'empty'}, '   ', 'yes'),
    ({'operator': 'ne', 'compare_to': 'cancel'}, 'continue', 'yes'),
])
async def test_each_operator_selects_only_its_branch(config, message, chosen):
    taken, events = await route(config, message)
    assert taken == {chosen}
    assert [e['outputs']['branch'] for e in events if e.get('node_id') == 'check' and e['status'] == 'success'] == ['true' if chosen == 'yes' else 'false']


@pytest.mark.asyncio
async def test_an_uncomparable_value_fails_the_run_with_its_reason_and_takes_no_branch():
    events = []
    async def emit(event): events.append(event)
    compiled = compile_workflow(workflow({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}), lambda _: '', emit, '{"amount": "about twelve hundred"}')
    with pytest.raises(ValueError, match="Condition field amount 'about twelve hundred' is not a number."):
        await compiled.graph.ainvoke({'values': {}})
    failed = [e for e in events if e.get('node_id') == 'check' and e['status'] == 'failed']
    assert failed and failed[0]['error'] == "Condition field amount 'about twelve hundred' is not a number."
    assert not any(e.get('node_id') in ('yes', 'no') for e in events)


def test_a_condition_still_cannot_recover_from_an_error():
    for on_error in ('continue', 'route'):
        errors = validate_workflow(workflow({'operator': 'gt', 'compare_to': '1'}, on_error=on_error))
        assert any(f"a condition cannot use on_error '{on_error}'" in error for error in errors), errors


def test_the_browser_fixture_matches_the_served_condition_definition():
    """The inspector browser test serves this file as /api/nodes; it must not drift."""
    import json
    from pathlib import Path
    from backend.app.registry import REGISTRY
    fixture = Path(__file__).resolve().parents[2] / 'frontend/tests/browser/condition-node.json'
    assert json.loads(fixture.read_text()) == json.loads(json.dumps(REGISTRY['condition'].public()))


def test_structured_agent_output_keeps_exact_numbers():
    from backend.app.agent_runtime import validate_structured
    schema = {'type': 'object', 'properties': {'amount': {'type': 'number', 'multipleOf': 0.5}}}
    assert validate_structured('{"amount": 1000.00000000000001}', {}) == '{"amount": 1000.00000000000001}'
    assert validate_structured('```json\n{"amount": 1.5, "n": 7, "ok": true}\n```', schema) == '{"amount": 1.5, "n": 7, "ok": true}'
    with pytest.raises(ValueError, match='multiple of'):
        validate_structured('{"amount": 1.2}', schema)
    with pytest.raises(ValueError, match='NaN is not a JSON number'):
        validate_structured('{"amount": NaN}', {})


@pytest.mark.asyncio
async def test_an_extraction_agent_amount_reaches_the_condition_exactly():
    from unittest.mock import patch
    raw = workflow({'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}).model_dump(mode='json')
    raw['nodes'].insert(1, {'id': 'extract', 'type': 'agent', 'inputs': {'input': 'input.message'},
                            'config': {'provider': 'ollama', 'model': 'llama3.1:latest', 'role': 'extraction'}})
    raw['nodes'][2]['inputs']['value'] = 'extract.text'
    raw['edges'][0] = {'id': 'a', 'source': 'input', 'target': 'extract'}
    raw['edges'].append({'id': 'x', 'source': 'extract', 'target': 'check'})
    async def model(*args, **kwargs):
        kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
        return '{"amount":1000.00000000000001}'
    with patch('backend.app.providers.ollama_chat', model):
        result = await compile_workflow(Workflow.model_validate(raw), lambda _: '', message='Order').graph.ainvoke({'values': {}})
    assert result['values']['extract']['text'] == '{"amount": 1000.00000000000001}'
    assert result['values']['check']['branch'] == 'true'
