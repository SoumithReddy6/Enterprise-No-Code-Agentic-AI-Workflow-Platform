"""Independent audit: run with .venv/bin/python <this file>; exit 1 pins open defects."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.app.conditions import ConditionConfig, evaluate
from backend.app.compiler import compile_workflow
from backend.app.models import Workflow


def workflow():
    return Workflow.model_validate({
        'version': 1, 'name': 'Exact decimal routing audit',
        'nodes': [
            {'id': 'input', 'type': 'chat_input'},
            {'id': 'check', 'type': 'condition', 'inputs': {'value': 'input.message'},
             'config': {'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}},
            {'id': 'yes', 'type': 'response', 'inputs': {'text': 'input.message'}},
            {'id': 'no', 'type': 'response', 'inputs': {'text': 'input.message'}},
        ],
        'edges': [
            {'id': 'a', 'source': 'input', 'target': 'check'},
            {'id': 'b', 'source': 'check', 'target': 'yes', 'sourceHandle': 'true'},
            {'id': 'c', 'source': 'check', 'target': 'no', 'sourceHandle': 'false'},
        ],
    })


async def main():
    rows = []
    # The direct-text control uses identical decimal digits, bypassing JSON parsing.
    control = evaluate('1000.00000000000001', ConditionConfig(operator='gt', compare_to='1000'))
    rows.append({'case': 'decimal_text_control', 'expected': True, 'actual': control, 'passed': control is True})
    for literal, operator, target, expected in [
        ('1000.00000000000001', 'gt', '1000', True),
        ('9007199254740993.0', 'gt', '9007199254740992', True),
        ('0.100000000000000005', 'eq', '0.1', False),
        ('1e309', 'gt', '1e308', True),
    ]:
        message = '{"amount":' + literal + '}'
        try:
            actual = evaluate(message, ConditionConfig(operator=operator, compare_to=target,
                                                      compare_as='number', field='amount'))
        except Exception as exc:
            actual = f'{type(exc).__name__}: {exc}'
        rows.append({'case': literal, 'message': message, 'operator': operator,
                     'target': target, 'expected': expected, 'actual': actual,
                     'passed': actual is expected})
    events = []
    async def emit(event):
        events.append(event)
    result = await compile_workflow(workflow(), lambda _: '', emit,
                                    '{"amount":1000.00000000000001}').graph.ainvoke({'values': {}})
    actual = result['values']['check']['branch']
    rows.append({'case': 'compiled_branch', 'expected': 'true', 'actual': actual,
                 'executed': [e['node_id'] for e in events if e['status'] == 'success'],
                 'passed': actual == 'true'})
    # Follow the actual extraction-agent path with a deterministic provider response.
    # This is not a live model quality test: the exact generated tokens are the input.
    raw = workflow().model_dump(mode='json')
    raw['nodes'].insert(1, {'id': 'extract', 'type': 'agent',
        'config': {'provider': 'ollama', 'model': 'llama3.1:latest', 'role': 'extraction'},
        'inputs': {'input': 'input.message'}})
    raw['nodes'][2]['inputs']['value'] = 'extract.text'
    raw['edges'][0] = {'id': 'a', 'source': 'input', 'target': 'extract'}
    raw['edges'].append({'id': 'x', 'source': 'extract', 'target': 'check'})
    provider_text = '{"amount":1000.00000000000001}'
    async def model(*args, **kwargs):
        kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
        return provider_text
    with patch('backend.app.providers.ollama_chat', model):
        generated = await compile_workflow(Workflow.model_validate(raw), lambda _: '', emit,
                                            'Extract the purchase amount').graph.ainvoke({'values': {}})
    actual = generated['values']['check']['branch']
    rows.append({'case': 'extraction_agent_to_condition', 'provider_text': provider_text,
                 'extracted_text': generated['values']['extract']['text'],
                 'expected': 'true', 'actual': actual, 'passed': actual == 'true'})
    document = {'rows': rows, 'passed': all(r['passed'] for r in rows)}
    Path(__file__).with_name('conditions-results.json').write_text(json.dumps(document, indent=2) + '\n')
    print(json.dumps(document, indent=2))
    return 0 if document['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
