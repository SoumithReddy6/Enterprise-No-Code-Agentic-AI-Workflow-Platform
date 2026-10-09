"""Decision provenance, phase 3: before a run, say which decisions can only ever rest on a
model's guess, and make sure that prediction matches what the run-time labels do."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.compiler import compile_workflow
from backend.app.models import Workflow
from backend.app.provenance import (STATIC_GUESSED, STATIC_RUNTIME, STATIC_TRUSTED, TRUSTED, GUESSED,
                                    decision_warnings, static_label, value_label)

STRUCTURED = {'provider': 'ollama', 'model': 'llama3.1:latest', 'role': 'extraction',
              'output_schema': {'type': 'object', 'properties': {'amount': {'type': ['number', 'null']}, 'item': {'type': ['string', 'null']}}}}
FREE = {'provider': 'ollama', 'model': 'llama3.1:latest'}


def graph(*middle, decide='step.text', field='', operator='gt', check=None):
    """input -> middle nodes in a line -> a decision node -> two responses."""
    nodes = [{'id': 'input', 'type': 'chat_input'}, *middle]
    if check:
        nodes.append({'id': 'check', 'type': 'calculate', 'inputs': {'value': decide}, 'config': {'expression': check, 'mode': 'check'}})
        nodes.append({'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}})
        names = [n['id'] for n in nodes]
        edges = [{'id': f'e{i}', 'source': a, 'target': b} for i, (a, b) in enumerate(zip(names, names[1:]))]
    else:
        config = {'operator': operator, 'compare_to': '1000', 'field': field} if operator != 'contains' else {'contains': 'yes'}
        nodes += [{'id': 'decide', 'type': 'condition', 'inputs': {'value': decide}, 'config': config},
                  {'id': 'yes', 'type': 'response', 'inputs': {'text': 'input.message'}},
                  {'id': 'no', 'type': 'response', 'inputs': {'text': 'input.message'}}]
        names = [n['id'] for n in nodes[:-2]]
        edges = [{'id': f'e{i}', 'source': a, 'target': b} for i, (a, b) in enumerate(zip(names, names[1:]))]
        edges += [{'id': 't', 'source': 'decide', 'target': 'yes', 'sourceHandle': 'true'},
                  {'id': 'f', 'source': 'decide', 'target': 'no', 'sourceHandle': 'false'}]
    return Workflow.model_validate({'version': 1, 'name': 'Static', 'nodes': nodes, 'edges': edges})


def agent(config, source='input.message'):
    return {'id': 'step', 'type': 'agent', 'inputs': {'input': source}, 'config': config}


CASES = {
    # name: (workflow, expected static label of the decided value, model answer)
    'trigger message': (graph(decide='input.message', operator='contains'), STATIC_TRUSTED, None),
    'structured field': (graph(agent(STRUCTURED), field='amount'), STATIC_RUNTIME, '{"amount": 1200, "item": null}'),
    'structured, whole text': (graph(agent(STRUCTURED), operator='contains'), STATIC_GUESSED, '{"amount": 1200, "item": null}'),
    'free text': (graph(agent(FREE), operator='contains'), STATIC_GUESSED, 'yes, it is urgent'),
    'prompt over the message': (graph({'id': 'step', 'type': 'prompt', 'inputs': {'message': 'input.message'}, 'config': {'template': 'Q: {message}'}},
                                      operator='contains'), STATIC_TRUSTED, None),
    'calculation over fields': (graph(agent(STRUCTURED), {'id': 'calc', 'type': 'calculate', 'inputs': {'value': 'step.text'},
                                                          'config': {'expression': 'amount * 2'}}, decide='calc.text', field='result'),
                                STATIC_RUNTIME, '{"amount": 600, "item": null}'),
    'calculation over free text': (graph(agent(FREE), {'id': 'calc', 'type': 'calculate', 'inputs': {'value': 'step.text'},
                                                       'config': {'expression': 'amount * 2'}}, decide='calc.text', field='result'),
                                   STATIC_GUESSED, '{"amount": 600}'),
    'calculation of constants': (graph({'id': 'calc', 'type': 'calculate', 'inputs': {'value': 'input.message'},
                                        'config': {'expression': '600 * 2'}}, decide='calc.text', field='result'), STATIC_TRUSTED, None),
}


@pytest.mark.parametrize('name', CASES)
def test_static_labels(name):
    workflow, expected, _ = CASES[name]
    decide = next(n for n in workflow.nodes if n.id in ('decide', 'check'))
    nodes = {n.id: n for n in workflow.nodes}
    assert static_label(nodes, decide.inputs['value'], decide.config.get('field', '')) == expected


def test_retrieval_and_loop_outputs():
    nodes = {n.id: n for n in Workflow.model_validate({'version': 1, 'name': 'x', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'search', 'type': 'retrieve', 'inputs': {'query': 'input.message'}, 'config': {}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'input.message'}, 'config': {'body': 'body'}},
        {'id': 'body', 'type': 'agent', 'inputs': {}, 'config': FREE},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'search.context'}}], 'edges': []}).nodes}
    assert static_label(nodes, 'search.context') == STATIC_TRUSTED
    assert static_label(nodes, 'search.query') == STATIC_TRUSTED
    assert static_label(nodes, 'each.results') == STATIC_GUESSED
    assert static_label(nodes, 'each.summary') == STATIC_TRUSTED
    assert static_label(nodes, 'missing.text') == STATIC_GUESSED


@pytest.mark.asyncio
@pytest.mark.parametrize('name', CASES)
async def test_the_static_label_never_contradicts_the_run(name):
    """trusted before a run is trusted in it; guessed before a run is guessed in it."""
    workflow, expected, answer = CASES[name]
    async def model(*args, **kwargs):
        kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
        return answer
    events = []
    async def emit(event): events.append(event)
    with patch('backend.app.providers.ollama_chat', model):
        # A Calculate node needs a JSON object, even when it reads no field.
        message = '{"note": "no fields used"}' if name == 'calculation of constants' else 'I need it for $1,200, yes'
        values = (await compile_workflow(workflow, lambda _: '', emit, message).graph.ainvoke({'values': {}}))['values']
    decide = next(n for n in workflow.nodes if n.id in ('decide', 'check'))
    label, _ = value_label(values, decide.inputs['value'], decide.config.get('field', ''))
    if expected == STATIC_TRUSTED:
        assert label in TRUSTED, label
    elif expected == STATIC_GUESSED:
        assert label == GUESSED, label


def test_only_decisions_that_are_always_guessed_are_warned_about():
    assert decision_warnings(CASES['structured field'][0]) == []
    assert decision_warnings(CASES['trigger message'][0]) == []
    warnings = decision_warnings(CASES['free text'][0])
    assert len(warnings) == 1 and warnings[0].startswith("decide: decides on step.text, which is always a model's guess")
    check = graph(agent(FREE), decide='step.text', check='amount == 3')
    assert decision_warnings(check)[0].startswith("check: checks values that are always a model's guess")
    assert decision_warnings(graph(agent(STRUCTURED), decide='step.text', check='amount == 3')) == []


def test_the_purchase_example_has_no_warnings():
    example = json.loads((Path(__file__).resolve().parents[2] / 'examples/purchase-routing.json').read_text())
    assert decision_warnings(Workflow.model_validate(example)) == []


def test_the_validate_endpoint_locates_decision_warnings(tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    workflow = CASES['free text'][0].model_dump(mode='json')
    workflow['nodes'][1]['config'] = {'provider': 'demo', 'model': 'demo'}
    with TestClient(create_app(f'sqlite:///{tmp_path}/v.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)) as client:
        body = client.post('/api/validate', json=workflow).json()
    located = [i for i in body['warning_issues'] if i['node_id'] == 'decide']
    assert located and located[0]['message'].startswith("decides on step.text, which is always a model's guess"), body
