"""Which outputs each node may bind, and where each validation message points.

The editor offers exactly the outputs bindable_outputs returns, using its own copy of the
rule. Both are held to one fixture: this test keeps it equal to the backend rule, and the
frontend test requires the editor to reproduce it, so the two cannot drift apart.
Regenerate after an intended rule change with RELAY_REGENERATE_FIXTURES=1.
"""
import json
import os
from pathlib import Path

from backend.app.compiler import bindable_outputs, locate, validate_workflow
from backend.app.models import Workflow
from backend.app.registry import REGISTRY

FIXTURE = Path(__file__).resolve().parents[2] / 'frontend/tests/fixtures/bindable-outputs.json'


def node(id, type, inputs=None, **extra):
    return {'id': id, 'type': type, 'inputs': inputs or {}, **extra}


def edge(source, target, handle=None, kind=None):
    return {'id': f'{source}-{target}-{handle or ""}', 'source': source, 'target': target,
            **({'sourceHandle': handle} if handle else {}), **({'kind': kind} if kind else {})}


def flow(*names):
    return [edge(a, b) for a, b in zip(names, names[1:])]


CASES = {
    'linear': {'nodes': [node('input', 'chat_input'), node('agent', 'agent', {'input': 'input.message'}),
                         node('out', 'response', {'text': 'agent.text'})],
               'edges': flow('input', 'agent', 'out')},
    'condition_branches': json.loads((Path(__file__).resolve().parents[2] / 'examples/purchase-routing.json').read_text()),
    'branches_rejoin': {'nodes': [node('input', 'chat_input'), node('check', 'condition', {'value': 'input.message'}, config={'contains': 'x'}),
                                  node('yes', 'prompt', {'message': 'input.message'}), node('no', 'prompt', {'message': 'input.message'}),
                                  node('join', 'prompt', {'message': 'input.message'}), node('out', 'response', {'text': 'join.text'})],
                        'edges': [edge('input', 'check'), edge('check', 'yes', 'true'), edge('check', 'no', 'false'),
                                  edge('yes', 'join'), edge('no', 'join'), edge('join', 'out')]},
    'error_route': {'nodes': [node('input', 'chat_input'), node('fetch', 'prompt', {'message': 'input.message'}, on_error='route'),
                              node('ok', 'response', {'text': 'fetch.text'}), node('fallback', 'response', {'text': 'input.message'})],
                    'edges': [edge('input', 'fetch'), edge('fetch', 'ok'), edge('fetch', 'fallback', 'error')]},
    'continue_on_error': {'nodes': [node('input', 'chat_input'), node('step', 'prompt', {'message': 'input.message'}, on_error='continue'),
                                    node('out', 'response', {'text': 'input.message'})],
                          'edges': flow('input', 'step', 'out')},
    'attached_tool': {'nodes': [node('input', 'chat_input'), node('agent', 'agent', {'input': 'input.message'}),
                                node('calc', 'tool_python', {}, config={'code': 'print(1)'}), node('out', 'response', {'text': 'agent.text'})],
                      'edges': flow('input', 'agent', 'out') + [{'id': 'tool', 'source': 'calc', 'target': 'agent', 'kind': 'tool', 'targetHandle': 'tools'}]},
}


def workflow(case):
    return Workflow.model_validate({'version': 1, 'name': 'case', **case})


def current():
    types = sorted({n['type'] for case in CASES.values() for n in case['nodes']})
    return {'outputs': {t: list(REGISTRY[t].outputs) for t in types},
            'cases': {name: {'workflow': {'nodes': case['nodes'], 'edges': case['edges']},
                             'bindable': bindable_outputs(workflow(case))} for name, case in CASES.items()}}


def test_the_editor_fixture_matches_the_backend_rule():
    if os.environ.get('RELAY_REGENERATE_FIXTURES'):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(current(), indent=1) + '\n')
    assert json.loads(FIXTURE.read_text()) == current()


def test_bindable_outputs_follow_every_path_rule():
    rejoin = bindable_outputs(workflow(CASES['branches_rejoin']))
    assert rejoin['join'] == ['input.message', 'check.branch'], 'a branch node did not run on every path to the join'
    assert rejoin['yes'] == ['input.message', 'check.branch']
    routes = bindable_outputs(workflow(CASES['error_route']))
    assert 'fetch.text' in routes['ok'] and 'fetch.text' not in routes['fallback'], 'a failed node provides no outputs'
    assert 'step.text' not in bindable_outputs(workflow(CASES['continue_on_error']))['out']
    example = bindable_outputs(workflow(CASES['condition_branches']))
    assert 'extract.text' in example['auto_note'], 'the agent before the condition is bindable on both branches'
    assert 'review_note.text' not in example['auto'], 'the other branch is not'
    assert 'calc' not in bindable_outputs(workflow(CASES['attached_tool'])), 'an attached tool is not part of the flow'


def test_every_offered_binding_validates_and_others_do_not():
    """What the editor offers is exactly what validation accepts."""
    for name in ('linear', 'condition_branches', 'branches_rejoin'):
        case = CASES[name]
        offered = bindable_outputs(workflow(case))
        for target in case['nodes']:
            if not target['inputs']:
                continue
            port = next(iter(target['inputs']))
            candidates = {f'{n["id"]}.{p}' for n in case['nodes'] for p in REGISTRY[n['type']].outputs}
            for ref in sorted(candidates):
                changed = json.loads(json.dumps(case))
                next(n for n in changed['nodes'] if n['id'] == target['id'])['inputs'][port] = ref
                rejected = any(e.startswith(f'{target["id"]}: invalid binding') for e in validate_workflow(workflow(changed)))
                assert rejected == (ref not in offered[target['id']]), (name, target['id'], ref)


def test_messages_are_located_by_actual_node_ids():
    wf = workflow(CASES['condition_branches'])
    assert locate(['auto_note: invalid binding message = ; use a declared output', 'Add a response output node.',
                   'check: compare_to: String should have at least 1 character', 'nowhere: not a node id'], wf) == [
        {'node_id': 'auto_note', 'message': 'invalid binding message = ; use a declared output'},
        {'node_id': None, 'message': 'Add a response output node.'},
        {'node_id': 'check', 'message': 'compare_to: String should have at least 1 character'},
        {'node_id': None, 'message': 'nowhere: not a node id'}]


def test_the_validate_endpoint_returns_located_issues(tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    broken = json.loads((Path(__file__).resolve().parents[2] / 'examples/purchase-routing.json').read_text())
    next(n for n in broken['nodes'] if n['id'] == 'auto_note')['inputs']['message'] = ''
    broken['nodes'][1]['config']['provider'] = 'demo'
    with TestClient(create_app(f'sqlite:///{tmp_path}/v.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)) as client:
        body = client.post('/api/validate', json=broken).json()
    assert not body['valid']
    assert {'node_id': 'auto_note', 'message': 'invalid binding message = ; use a declared output from a guaranteed upstream node.'} in body['issues']
    assert [i['message'] for i in body['issues']] == [e.partition(': ')[2] if i['node_id'] else e for e, i in zip(body['errors'], body['issues'])]


EXAMPLE_CATALOG = Path(__file__).resolve().parents[2] / 'frontend/tests/browser/example-catalog.json'


def test_the_browser_catalog_fixture_matches_the_served_definitions():
    """The editor browser tests serve these definitions as /api/nodes; they must not drift."""
    example = json.loads((Path(__file__).resolve().parents[2] / 'examples/purchase-routing.json').read_text())
    served = [json.loads(json.dumps(REGISTRY[t].public())) for t in sorted({n['type'] for n in example['nodes']})]
    if os.environ.get('RELAY_REGENERATE_FIXTURES'):
        EXAMPLE_CATALOG.write_text(json.dumps(served, indent=1, ensure_ascii=False) + '\n')
    assert json.loads(EXAMPLE_CATALOG.read_text()) == served
