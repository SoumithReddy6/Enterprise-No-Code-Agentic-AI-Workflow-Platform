"""Decision provenance, phase 2: the Calculate node computes and checks in code, exactly,
so a model never does the arithmetic a decision depends on."""
import json
import random
import string
from decimal import Decimal
from unittest.mock import patch

import pytest

from backend.app.calculate import CalculationError, MAX_DEPTH, MAX_LENGTH, MAX_TOKENS, parse, run
from backend.app.compiler import compile_workflow, validate_workflow
from backend.app.models import Workflow
from backend.app.provenance import ABSENT, CALCULATED, GUESSED, QUOTED


def compute(expression, value=None):
    return run(expression, json.dumps(value or {}), 'compute')[0]


# ------------------------------------------------------------------ exact arithmetic


@pytest.mark.parametrize('expression,value,expected', [
    # The totals llama3.1 got wrong, computed exactly.
    ('quantity * unit_price', {'quantity': 7, 'unit_price': 142.86}, '1000.02'),
    ('quantity * unit_price', {'quantity': 9, 'unit_price': 111.11}, '999.99'),
    ('quantity * unit_price', {'quantity': 7, 'unit_price': 142.85}, '999.95'),
    ('quantity * unit_price', {'quantity': 2, 'unit_price': 500.01}, '1000.02'),
    ('0.1 + 0.2', {}, '0.3'),
    ('2 + 3 * 4', {}, '14'),
    ('(2 + 3) * 4', {}, '20'),
    ('-total + 5', {'total': 3}, '2'),
    ('10 - 2 - 3', {}, '5'),
    ('12 / 4 / 3', {}, '1'),
    ('round(10 / 3, 2)', {}, '3.33'),
    ('round(2.345, 2)', {}, '2.35'),
    ('round(2.5, 0)', {}, '3'),
    ('min(a, b, 3)', {'a': 5, 'b': 4}, '3'),
    ('max(a, b)', {'a': 5, 'b': 4}, '5'),
    ('abs(a - b)', {'a': 4, 'b': 5}, '1'),
    ('order.qty * order.price', {'order': {'qty': 3, 'price': 450}}, '1350'),
    ('coalesce(total, quantity * unit_price)', {'total': None, 'quantity': 3, 'unit_price': 450}, '1350'),
    ('coalesce(total, quantity * unit_price)', {'total': 1350, 'quantity': None, 'unit_price': None}, '1350'),
    ('coalesce(total, 0)', {'total': None}, '0'),
    ('1e3' if False else '1000 * 1000', {}, '1000000'),
])
def test_arithmetic_is_exact(expression, value, expected):
    assert compute(expression, value) == Decimal(expected)


def test_a_missing_part_makes_the_result_null_rather_than_a_guess():
    assert compute('quantity * unit_price', {'quantity': None, 'unit_price': 450}) is None
    assert compute('coalesce(total, quantity * unit_price)', {'total': None, 'quantity': None, 'unit_price': 450}) is None
    assert compute('min(a, b)', {'a': None, 'b': 1}) is None


@pytest.mark.parametrize('expression,mode,value,expected', [
    ('total == quantity * unit_price', 'check', {'total': 1350, 'quantity': 3, 'unit_price': 450}, True),
    ('total == quantity * unit_price', 'check', {'total': 1300, 'quantity': 3, 'unit_price': 450}, False),
    ('total > 1000 and not rush', 'check', {'total': 1350, 'rush': False}, True),
    ('rush == true_flag', 'check', {'rush': True, 'true_flag': True}, True),
    ('a < b or b < a', 'check', {'a': 1, 'b': 1}, False),
])
def test_checks(expression, mode, value, expected):
    assert run(expression, json.dumps(value), mode)[0] is expected


def test_only_the_fields_actually_read_are_reported():
    assert run('coalesce(total, quantity * unit_price)', '{"total": 1350, "quantity": 3, "unit_price": 450}', 'compute')[1] == ['total']
    assert run('coalesce(total, quantity * unit_price)', '{"total": null, "quantity": 3, "unit_price": 450}', 'compute')[1] == ['total', 'quantity', 'unit_price']
    assert run('a > 1 and b > 1', '{"a": 0, "b": 5}', 'check')[1] == ['a'], 'and stops at the first false'


@pytest.mark.parametrize('expression,mode,value,message', [
    ('quantity * unit_price', 'compute', {'quantity': 3}, 'field unit_price is missing'),
    ('quantity * unit_price', 'compute', {'quantity': 3, 'unit_price': '450'}, 'field unit_price is text, not a number'),
    ('quantity * unit_price', 'compute', {'quantity': True, 'unit_price': 4}, 'field quantity is true/false, not a number'),
    ('items * 2', 'compute', {'items': [1]}, 'field items is a list, not a number'),
    ('1 / 0', 'compute', {}, 'division by zero'),
    ('a * b', 'compute', {'a': int('9' * 30), 'b': int('9' * 30)}, 'the exact result needs more than 50 significant digits'),
    ('a * a', 'compute', {'a': Decimal('1e900')}, 'outside the supported number range'),
    ('total == quantity * unit_price', 'check', {'total': None, 'quantity': 3, 'unit_price': 450}, 'cannot compare with a missing value'),
    ('flag < 1', 'check', {'flag': True}, 'cannot compare true/false with a number'),
    ('a and b', 'check', {'a': 1, 'b': True}, 'and needs true/false values'),
    ('round(x, 25)', 'compute', {'x': 1}, 'round needs 0 to 20 places'),
])
def test_values_that_cannot_be_calculated_are_named_errors(expression, mode, value, message):
    with pytest.raises(CalculationError, match=message):
        run(expression, json.dumps(value, default=str).replace('"1E+900"', '1e900'), mode)


@pytest.mark.parametrize('text,message', [
    ('{"a": 1e99999999999999}', 'the input has .* outside the supported number range'),
    ('not json', 'the input is not JSON'),
    ('[1, 2]', 'the input must be a JSON object'),
])
def test_the_input_must_be_a_json_object(text, message):
    with pytest.raises(CalculationError, match=message):
        run('a + 1', text, 'compute')


# ------------------------------------------------------------------ the parser


@pytest.mark.parametrize('expression,message', [
    ('', 'the expression is empty'),
    ('a +', 'the expression ends too early'),
    ('a b', "unexpected 'b' after a complete expression"),
    ('a < b < c', 'chain comparisons with and'),
    ('and', "'and' needs a value on each side"),
    ('round(x, places)', 'round needs a whole number of places'),
    ('min(a)', 'min takes 2 to 8 arguments'),
    ('abs(a, b)', 'abs takes 1 arguments'),
    ('a $ b', "unexpected character '\\$'"),
    ('__import__("os")', 'unexpected character'),
    ('a.b(c)', "unexpected '\\('"),
    ('a' * (MAX_LENGTH + 1), f'longer than {MAX_LENGTH} characters'),
    ('(' * (MAX_DEPTH + 1) + 'a' + ')' * (MAX_DEPTH + 1), f'nests more than {MAX_DEPTH} levels'),
    ('+'.join(['a'] * (MAX_TOKENS // 2 + 2)), f'more than {MAX_TOKENS} parts'),
])
def test_malformed_expressions_are_named_errors(expression, message):
    with pytest.raises(CalculationError, match=message):
        parse(expression)


def test_a_hyphen_is_always_subtraction():
    assert compute('total-discount', {'total': 100, 'discount': 15}) == 85


def test_random_text_is_parsed_or_refused_and_never_executed():
    rng = random.Random(9)
    alphabet = string.ascii_letters + string.digits + ' +-*/().,<>=!_"\'[]{};:@#$%^&|\\`~'
    for _ in range(5000):
        expression = ''.join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
        try:
            run(expression, '{}', 'compute')
        except CalculationError:
            pass


# ------------------------------------------------------------------ in a workflow

EXTRACT = {'provider': 'ollama', 'model': 'llama3.1:latest', 'role': 'extraction',
           'output_schema': {'type': 'object', 'properties': {f: {'type': ['number', 'null']} for f in ('quantity', 'unit_price', 'total')}}}


def workflow(expression, mode='compute'):
    return Workflow.model_validate({'version': 1, 'name': 'Calc', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'extract', 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': EXTRACT},
        {'id': 'calc', 'type': 'calculate', 'inputs': {'value': 'extract.text'}, 'config': {'expression': expression, 'mode': mode}},
        {'id': 'check', 'type': 'condition', 'inputs': {'value': 'calc.text'},
         'config': {'operator': 'eq', 'compare_to': 'true'} if mode == 'check' else {'operator': 'gt', 'compare_to': '1000', 'field': 'result'}},
        {'id': 'yes', 'type': 'response', 'inputs': {'text': 'calc.text'}},
        {'id': 'no', 'type': 'response', 'inputs': {'text': 'calc.text'}},
    ], 'edges': [
        {'id': 'a', 'source': 'input', 'target': 'extract'}, {'id': 'b', 'source': 'extract', 'target': 'calc'},
        {'id': 'c', 'source': 'calc', 'target': 'check'},
        {'id': 't', 'source': 'check', 'target': 'yes', 'sourceHandle': 'true'},
        {'id': 'f', 'source': 'check', 'target': 'no', 'sourceHandle': 'false'},
    ]})


async def execute(wf, message, answer):
    async def model(*args, **kwargs):
        kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
        return answer
    events = []
    async def emit(event): events.append(event)
    with patch('backend.app.providers.ollama_chat', model):
        values = (await compile_workflow(wf, lambda _: '', emit, message).graph.ainvoke({'values': {}}))['values']
    decisions = {e['node_id']: e['decision'] for e in events if e.get('status') == 'success' and 'decision' in e}
    return values, decisions


@pytest.mark.asyncio
async def test_quoted_parts_give_a_calculated_total_that_decides_without_review():
    values, decisions = await execute(workflow('coalesce(total, quantity * unit_price)'), '7 licences at $142.86 each.',
                                      '{"quantity": 7, "unit_price": 142.86, "total": null}')
    assert json.loads(values['calc']['text']) == {'result': 1000.02}
    assert values['calc']['_provenance'] == {'v': 1, 'ports': {'text': CALCULATED}, 'fields': {'text': {'result': CALCULATED}}}
    assert decisions['check'] == {'label': CALCULATED, 'would_review': False}
    assert 'yes' in values, 'over $1,000: the exact total routes to review, where the model said 999.72'


@pytest.mark.asyncio
async def test_a_guessed_part_makes_the_total_guessed():
    values, decisions = await execute(workflow('coalesce(total, quantity * unit_price)'), 'buy a few laptops at $450',
                                      '{"quantity": 3, "unit_price": 450, "total": null}')
    assert values['calc']['_provenance']['ports']['text'] == GUESSED, 'the quantity 3 is not in the message'
    assert decisions['check']['would_review'] is True


@pytest.mark.asyncio
async def test_a_written_total_is_used_and_unused_guessed_parts_do_not_count():
    values, decisions = await execute(workflow('coalesce(total, quantity * unit_price)'), 'Laptops, $1,350 total.',
                                      '{"quantity": 3, "unit_price": 450, "total": 1350}')
    assert values['calc']['_provenance']['ports']['text'] == CALCULATED
    assert decisions['check'] == {'label': CALCULATED, 'would_review': False}


@pytest.mark.asyncio
async def test_a_missing_total_is_absent_and_an_empty_gate_decides_on_it_honestly():
    wf = workflow('coalesce(total, quantity * unit_price)')
    wf.nodes[3].config = {'operator': 'empty', 'field': 'result'}
    values, decisions = await execute(wf, 'How do AI workflows work?', '{"quantity": null, "unit_price": null, "total": null}')
    assert json.loads(values['calc']['text']) == {'result': None}
    assert values['calc']['_provenance']['ports']['text'] == ABSENT
    assert decisions['check'] == {'label': ABSENT, 'would_review': False} and 'yes' in values


@pytest.mark.asyncio
@pytest.mark.parametrize('message,answer,expected', [
    ('3 laptops at $450 each, $1,350 total.', '{"quantity": 3, "unit_price": 450, "total": 1350}',
     {'label': CALCULATED, 'passed': True, 'would_review': False}),
    ('3 laptops at $450 each, $1,300 total.', '{"quantity": 3, "unit_price": 450, "total": 1300}',
     {'label': CALCULATED, 'passed': False, 'would_review': True, 'reason': 'check_failed'}),
    ('laptops at $450 each, $1,350 total.', '{"quantity": 3, "unit_price": 450, "total": 1350}',
     {'label': GUESSED, 'passed': True, 'would_review': True, 'reason': 'guessed'}),
])
async def test_a_check_that_fails_or_rests_on_a_guess_would_need_a_person(message, answer, expected):
    _, decisions = await execute(workflow('total == quantity * unit_price', 'check'), message, answer)
    assert decisions['calc'] == expected


@pytest.mark.parametrize('config,message', [
    ({'expression': 'a +'}, 'calc: expression: the expression ends too early.'),
    ({'expression': 'a > 1'}, 'calc: compute mode needs a number, not a comparison'),
    ({'expression': 'a + 1', 'mode': 'check'}, 'calc: check mode needs a comparison'),
    ({'expression': 'a', 'mode': 'guess'}, 'calc: mode: Input should be'),
])
def test_invalid_calculations_are_reported_at_validation(config, message):
    raw = workflow('a').model_dump(mode='json')
    raw['nodes'][2]['config'] = config
    errors = validate_workflow(Workflow.model_validate(raw))
    assert any(error.startswith(message) for error in errors), errors


@pytest.mark.asyncio
async def test_a_constant_fallback_for_a_missing_field_is_calculated():
    values, _ = await execute(workflow('coalesce(total, 0)'), 'nothing to see', '{"quantity": null, "unit_price": null, "total": null}')
    assert json.loads(values['calc']['text']) == {'result': 0}
    assert values['calc']['_provenance']['ports']['text'] == CALCULATED


def test_the_mode_is_enforced_at_run_time_as_well_as_at_validation():
    """Provenance re-runs a stored configuration, so run() checks the mode itself too."""
    with pytest.raises(CalculationError, match='compute needs a number, not a comparison'):
        run('a > 1', '{"a": 2}', 'compute')
    with pytest.raises(CalculationError, match='a check needs a comparison'):
        run('a + 1', '{"a": 2}', 'check')
