"""A6.1 Part 2: the Repeat the editor builds and shows, held to the real backend (K01, K02).

Design: docs/superpowers/specs/2026-11-13-bounded-loops-editor-design.md (revision 3, approved).
The browser tests serve recorded backend results instead of a live backend; these tests keep
the recordings true. Regenerate after an intended change with RELAY_REGENERATE_FIXTURES=1.

* frontend/tests/browser/repeat-issues.json (K01): the Repeat that browser test B01 builds
  from the default Q&A draft (valid under contract 2), and four broken Repeats with the
  located issues /api/validate returns for them (B05).
* frontend/tests/browser/repeat-run-events.json (K02): the events of a real two-iteration
  Repeat run (rejected, then accepted) as they are stored and streamed (storage.observed),
  and the normalized /api/validate responses for the three workflows browser test B02
  imports. `seq` and `timestamp` are added for the mock; they and `duration_ms` vary between
  runs and are not compared.
"""
import asyncio
import copy
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.storage import observed
from backend.tests.test_repeat import Script, run_mem, standard, verdict

BROWSER = Path(__file__).resolve().parents[2] / 'frontend/tests/browser'
ISSUES = BROWSER / 'repeat-issues.json'
EVENTS = BROWSER / 'repeat-run-events.json'
VARIABLE = ('seq', 'timestamp', 'duration_ms')
EDGE_FIELDS = ('id', 'source', 'target', 'kind', 'sourceHandle', 'targetHandle')


def regenerate():
    return bool(os.environ.get('RELAY_REGENERATE_FIXTURES'))


def lab(tmp_path, name):
    return create_app(f'sqlite:///{tmp_path}/{name}.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)


def node(workflow, id):
    return next(n for n in workflow['nodes'] if n['id'] == id)


# --------------------------------------------------------------------------- K01

def checker_without_schema(valid):
    """The judge has no output schema, so the stop rule cannot be checked."""
    workflow = copy.deepcopy(valid)
    del node(workflow, 'judge')['config']['output_schema']
    return workflow


def write_in_body(valid):
    """The body is an HTTP POST with writes enabled."""
    workflow = copy.deepcopy(valid)
    body = node(workflow, 'agent')
    body.update(type='tool_http', label='Post update', inputs={},
                config={'connection_id': 'sink', 'method': 'POST', 'enable_writes': True, 'approval': False})
    return workflow


def rule_field_not_required(valid):
    """The stop rule reads `approved`, which the judge's schema does not require."""
    workflow = copy.deepcopy(valid)
    node(workflow, 'repeat')['config']['until'] = {'operator': 'eq', 'field': 'approved', 'compare_to': 'true'}
    return workflow


def old_edges_kept(valid):
    """B01 without its first step: the draft's reader -> Agent and Agent -> Response stay."""
    workflow = copy.deepcopy(valid)
    workflow['edges'] += [{'id': 'reader-agent', 'source': 'reader', 'target': 'agent', 'sourceHandle': 'continue'},
                          {'id': 'agent-response', 'source': 'agent', 'target': 'response'}]
    return workflow


BROKEN = {'checker_without_schema': checker_without_schema, 'write_in_body': write_in_body,
          'rule_field_not_required': rule_field_not_required, 'old_edges_kept': old_edges_kept}


def test_k01_the_editor_repeat_validates_and_broken_repeats_report_located_issues(tmp_path):
    fixture = json.loads(ISSUES.read_text())
    model = fixture['model']
    assert fixture['valid']['contract'] == 2
    with TestClient(lab(tmp_path, 'k01')) as client:
        assert client.post('/api/models', json={'provider': model['provider'], 'model': model['model']}).status_code == 201
        valid = client.post('/api/validate', json=fixture['valid']).json()
        assert valid['valid'], valid['issues']
        broken = []
        for name, build in BROKEN.items():
            workflow = build(fixture['valid'])
            body = client.post('/api/validate', json=workflow).json()
            assert not body['valid'], name
            broken.append({'name': name, 'workflow': workflow, 'issues': body['issues']})
    if regenerate():
        ISSUES.write_text(json.dumps({**fixture, 'broken': broken}, indent=1, ensure_ascii=False) + '\n')
    assert fixture['broken'] == broken


# --------------------------------------------------------------------------- K02

REPEAT_LAYOUT = {'input': (300, 40), 'r': (300, 240), 'writer': (680, 140), 'judge': (680, 360), 'out': (300, 460)}
FOR_EACH_LAYOUT = {'input': (300, 40), 'tickets': (300, 220), 'each': (300, 400), 'summarize': (680, 400), 'out': (300, 600)}


def laid_out(workflow, layout):
    """Positions, so the browser test can see where each line attaches (no node overlaps)."""
    workflow = copy.deepcopy(workflow)
    for n in workflow['nodes']:
        n['position'] = dict(zip('xy', layout[n['id']]))
    return workflow


def with_output_handle(workflow):
    """The Repeat's normal outgoing edge saved with the accepted sourceHandle 'output'."""
    workflow = copy.deepcopy(workflow)
    edge = next(e for e in workflow['edges'] if (e['source'], e['target']) == ('r', 'out'))
    edge['sourceHandle'] = 'output'
    return workflow


def for_each_legacy():
    """A For each saved in the older form: its loop edge and its flow edge name no source port."""
    return {'version': 1, 'name': 'For each, older form', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': 'c1', 'operation': 'search', 'jql': 'assignee=me'}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'}, 'config': {'body': 'summarize'}},
        {'id': 'summarize', 'type': 'agent', 'config': {'provider': 'ollama', 'model': 'fixture', 'system': 'Summarize.'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'each.summary'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'tickets'},
                  {'id': 'b', 'source': 'tickets', 'target': 'each'},
                  {'id': 'c', 'source': 'each', 'target': 'out'},
                  {'id': 'd', 'source': 'each', 'target': 'summarize', 'kind': 'loop'}]}


def recorded_run(monkeypatch):
    Script(monkeypatch, writer=['draft one', 'draft two'], judge=[verdict(False), verdict(True, 'ok')])
    raw = standard()
    values, exc, events = asyncio.run(run_mem(raw))
    assert exc is None, exc
    return raw, json.loads(values['r']['summary']), [observed(e) for e in events]


def stable(events):
    return [{k: v for k, v in e.items() if k not in VARIABLE} for e in events]


def edges_of(workflow):
    return [{k: e.get(k) for k in EDGE_FIELDS} for e in workflow['edges']]


def test_k02_the_run_events_and_normalized_imports_are_real(tmp_path, monkeypatch):
    fixture = json.loads(EVENTS.read_text())
    raw, summary, events = recorded_run(monkeypatch)
    assert summary['stopped_by'] == 'rule' and summary['iterations'] == 2
    files = {'repeat': laid_out(raw, REPEAT_LAYOUT), 'repeat_output': laid_out(with_output_handle(raw), REPEAT_LAYOUT),
             'for_each_legacy': laid_out(for_each_legacy(), FOR_EACH_LAYOUT)}
    with TestClient(lab(tmp_path, 'k02')) as client:
        imports = [{'name': name, 'file': file, 'normalized': client.post('/api/validate', json=file).json()['workflow']}
                   for name, file in files.items()]
    if regenerate():
        stamped = [{**e, 'seq': i + 1, 'timestamp': '2026-11-13T00:00:%02dZ' % i} for i, e in enumerate(events)]
        EVENTS.write_text(json.dumps({'workflow': raw, 'summary': summary, 'events': stamped, 'imports': imports},
                                     indent=1, ensure_ascii=False) + '\n')
        fixture = json.loads(EVENTS.read_text())
    assert fixture['workflow'] == raw and fixture['summary'] == summary
    assert stable(fixture['events']) == stable(events)
    assert [i['name'] for i in fixture['imports']] == list(files)
    for recorded, fresh in zip(fixture['imports'], imports):
        assert recorded['file'] == fresh['file']
        assert edges_of(recorded['normalized']) == edges_of(fresh['normalized']), recorded['name']
        assert recorded['normalized'] == fresh['normalized'], recorded['name']
    # The forms B02 reads: loop edges into `input`, a Repeat flow edge from `output`, and an
    # older For each whose loop and flow edges name no source port.
    normalized = {i['name']: edges_of(i['normalized']) for i in fixture['imports']}
    assert {e['targetHandle'] for e in normalized['repeat'] if e['kind'] == 'loop'} == {'input'}
    assert [e['sourceHandle'] for e in normalized['repeat_output'] if (e['source'], e['target']) == ('r', 'out')] == ['output']
    assert [(e['kind'], e['sourceHandle']) for e in normalized['for_each_legacy'] if e['source'] == 'each'] == [
        ('flow', None), ('loop', None)]
