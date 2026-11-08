"""A5.3 Part 2: the forks the editor builds and shows, held to the real validator (K01).

Design: docs/superpowers/specs/2026-11-07-parallel-branches-editor-design.md (revision 2).
frontend/tests/browser/parallel-issues.json holds the fork the editor builds in browser test
B01 (the default Q&A draft with two Agent branches and a Join after its Agent) and three
broken forks with the located issues /api/validate returns for them. The browser tests serve
these results instead of a live backend, so this test keeps them true.
Regenerate the issues after an intended message change with RELAY_REGENERATE_FIXTURES=1.
"""
import copy
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.main import create_app

FIXTURE = Path(__file__).resolve().parents[2] / 'frontend/tests/browser/parallel-issues.json'


def without_edge(workflow, source, target):
    workflow['edges'] = [e for e in workflow['edges'] if (e['source'], e['target']) != (source, target)]
    return workflow


def branch_ends_early(valid):
    """Branch B has no edge to the Join."""
    return without_edge(copy.deepcopy(valid), 'branchB', 'join')


def write_in_branch(valid):
    """Branch B is an HTTP POST with writes enabled."""
    workflow = copy.deepcopy(valid)
    node = next(n for n in workflow['nodes'] if n['id'] == 'branchB')
    node.update(type='tool_http', label='Post update', inputs={'input': 'agent.text'},
                config={'connection_id': 'sink', 'method': 'POST', 'enable_writes': True, 'approval': False})
    next(n for n in workflow['nodes'] if n['id'] == 'response')['inputs'] = {'text': 'branchA.text'}
    return workflow


def nine_branches(valid):
    """Seven more Agent branches, nine in all."""
    workflow = copy.deepcopy(valid)
    template = next(n for n in workflow['nodes'] if n['id'] == 'branchA')
    for i in range(3, 10):
        workflow['nodes'].append({**copy.deepcopy(template), 'id': f'branch{i}', 'label': f'Branch {i}',
                                  'position': {'x': 120 * i, 'y': 550}})
        workflow['edges'] += [{'id': f'agent-branch{i}', 'source': 'agent', 'target': f'branch{i}'},
                              {'id': f'branch{i}-join', 'source': f'branch{i}', 'target': 'join'}]
    return workflow


BROKEN = {'branch_ends_early': branch_ends_early, 'write_in_branch': write_in_branch, 'nine_branches': nine_branches}


def test_k01_the_editor_fork_validates_and_broken_forks_report_located_issues(tmp_path):
    fixture = json.loads(FIXTURE.read_text())
    model = fixture['model']
    app = create_app(f'sqlite:///{tmp_path}/k01.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    with TestClient(app) as client:
        assert client.post('/api/models', json={'provider': model['provider'], 'model': model['model']}).status_code == 201
        valid = client.post('/api/validate', json=fixture['valid']).json()
        assert valid['valid'], valid['issues']
        broken = []
        for name, build in BROKEN.items():
            workflow = build(fixture['valid'])
            body = client.post('/api/validate', json=workflow).json()
            assert not body['valid']
            broken.append({'name': name, 'workflow': workflow, 'issues': body['issues']})
    if os.environ.get('RELAY_REGENERATE_FIXTURES'):
        FIXTURE.write_text(json.dumps({**fixture, 'broken': broken}, indent=1, ensure_ascii=False) + '\n')
    assert fixture['broken'] == broken
