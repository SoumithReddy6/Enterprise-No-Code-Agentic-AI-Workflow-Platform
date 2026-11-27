"""For each reads a body's result from its declared result port.

Retrieve declares query, context and sources, and no text. For each read `text` and saved
'' for every Retrieve item, with status success. The result is now read from the declared
port (context for Retrieve, text for every other body type), a body without that port
fails its item, and a Retrieve item saved as '' by an earlier version is refused on resume.
Runtime tests use the real Worker, storage and an indexed knowledge base; only the Jira
transport is substituted.
"""
import asyncio
import copy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app import tool_service
from backend.app.models import Workflow
from backend.tests.test_iteration import issues, jira_connection, run
from backend.tests.test_repeat import kb_lab
from backend.tests.test_kb_services_integration import services  # noqa: F401 (fixture)

MISSING = "lookup: the body's context output is missing."
LOST = 'each cannot be restored: item 1 was saved without its Retrieve result by an earlier Relay version. Start a new run.'


def retrieve_loop(kb_id, connection_id='c1'):
    """jira search -> for each issue -> Retrieve -> response."""
    return {'version': 1, 'name': 'Lookups', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': connection_id, 'operation': 'search', 'jql': 'assignee=me'}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'}, 'config': {'body': 'lookup'}},
        {'id': 'lookup', 'type': 'retrieve', 'config': {'knowledge_base_id': kb_id, 'mode': 'keyword'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'tickets'},
                  {'id': 'b', 'source': 'tickets', 'target': 'each'},
                  {'id': 'c', 'source': 'each', 'target': 'out'},
                  {'id': 'd', 'source': 'each', 'target': 'lookup', 'kind': 'loop', 'sourceHandle': 'body', 'targetHandle': 'input'}]}


def codename_issues(n):
    return json.dumps({'issues': [{'key': f'PROJ-{i}', 'fields': {
        'summary': 'project codename', 'status': {'name': 'Open'}, 'assignee': {'displayName': 'Ana'},
        'updated': '2026-09-01T00:00:00Z'}} for i in range(n)]})


async def lab(tmp_path, monkeypatch, services, n=2):
    store, worker, kb, doc = await kb_lab(tmp_path, monkeypatch, services)
    monkeypatch.setattr(tool_service, 'public_addresses', lambda host, port: ['93.184.216.34'])
    async def prepared(_prepared, tenant_id='local'): return codename_issues(n)
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    raw = retrieve_loop(kb['id'], jira_connection(store))
    return store, worker, doc, raw


def crash_when(monkeypatch, store, predicate):
    original = store.__dict__.get('_unwrapped_worker_event') or store.worker_event
    store._unwrapped_worker_event = original
    def wrapped(id, owner, event, **kwargs):
        saved = original(id, owner, event, **kwargs)
        if predicate(event): raise asyncio.CancelledError()
        return saved
    monkeypatch.setattr(store, 'worker_event', wrapped)


def item_saved(index):
    return lambda e: e.get('kind') == 'loop_item' and e.get('node_id') == 'each' and e.get('item_index') == index


def each_checkpoint(e):
    return e.get('node_id') == 'each' and e.get('status') == 'success' and not e.get('transient')


async def attempt(store, worker, raw=None, id=None):
    if id is None: id = store.create_run(raw, 'go')['id']
    else: store.resume_run(id)
    await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 30)
    return id, store.run(id)


def edit_item_rows(store, id, change):
    """Change the saved item rows. A loop checkpoint refers to its rows by count and digest,
    so a checkpoint, when present, gets the digest of the changed rows: the result is what
    an earlier version saved, not a tampered checkpoint."""
    from backend.app.storage import RunLoopItemRecord, RunRecord, results_digest
    with Session(store.engine) as s:
        records = sorted(s.scalars(select(RunLoopItemRecord).where(RunLoopItemRecord.run_id == id, RunLoopItemRecord.node_id == 'each')),
                         key=lambda r: r.item_index)
        for record in records:
            record.entry = change(record.item_index, copy.deepcopy(record.entry))
        run = s.get(RunRecord, id)
        checkpoint = (run.data.get('checkpoints') or {}).get('each')
        if checkpoint is not None:
            data = copy.deepcopy(run.data)
            data['checkpoints']['each']['results']['sha256'] = results_digest([r.entry for r in records])
            run.data = data
        s.commit()


def blank_first(index, entry):
    return {**entry, 'value': ''} if index == 0 else entry


# --------------------------------------------------------------------------- F01-F02: the result

@pytest.mark.asyncio
async def test_f01_a_retrieve_body_result_is_its_context(tmp_path, monkeypatch, services):
    store, worker, doc, raw = await lab(tmp_path, monkeypatch, services)
    _, done = await attempt(store, worker, raw)
    assert done['status'] == 'success', done.get('error')
    results = done['checkpoints']['each']['results']
    assert [r['status'] for r in results] == ['success', 'success']
    for item in results:
        envelope = json.loads(item['value'])
        assert envelope['passages'][0]['document_id'] == doc['id']
        assert 'Bluebird' in envelope['passages'][0]['text']


@pytest.mark.asyncio
async def test_f02_the_item_label_is_the_context_label(tmp_path, monkeypatch, services):
    from backend.app import provenance
    store, worker, doc, raw = await lab(tmp_path, monkeypatch, services, n=1)
    _, done = await attempt(store, worker, raw)
    assert done['status'] == 'success', done.get('error')
    item = done['checkpoints']['each']['results'][0]
    # Jira items are source data; the envelope quotes the item and source passages.
    assert item['_provenance']['ports']['text'] == provenance.SOURCE
    assert provenance.item_label(item) == provenance.SOURCE


# --------------------------------------------------------------------------- F03: a missing port

@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'stop'])
async def test_f03_a_body_without_its_result_port_fails_its_item(monkeypatch, policy):
    from backend.app.registry import REGISTRY
    async def no_context(inputs, config, ctx):
        return {'query': inputs['query'], 'sources': '[]'}
    monkeypatch.setattr(REGISTRY['retrieve'], 'handler', no_context)
    async def resolver(action, *args):
        if action == 'tool': return {'text': '{}', 'items': issues(2), 'truncated': False, 'warnings': []}
        raise AssertionError(action)
    raw = retrieve_loop('fixture')
    raw['nodes'][2]['config']['on_item_error'] = policy
    events = []
    values = (await run(Workflow.model_validate(raw), resolver, events))['values']
    results = values['each']['results']
    expected = 2 if policy == 'continue' else 1
    assert [(r['status'], r['value'], r['error']) for r in results] == [('failed', '', MISSING)] * expected
    assert values['each']['failed'] == str(expected)
    failures = [e for e in events if e.get('node_id') == 'lookup' and e.get('status') == 'failed']
    assert [e['error'] for e in failures] == [MISSING] * expected


# --------------------------------------------------------------------------- F04-F05: restore

@pytest.mark.asyncio
@pytest.mark.parametrize('saved', ['row', 'checkpoint'])
async def test_f04_a_retrieve_item_saved_blank_is_refused_on_resume(tmp_path, monkeypatch, services, saved):
    store, worker, doc, raw = await lab(tmp_path, monkeypatch, services)
    crash_when(monkeypatch, store, item_saved(0) if saved == 'row' else each_checkpoint)
    id, first = await attempt(store, worker, raw)
    assert first['status'] != 'success'
    assert ('each' in first['checkpoints']) == (saved == 'checkpoint')
    # What an earlier Relay version saved for a Retrieve item.
    edit_item_rows(store, id, blank_first)
    crash_when(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'failed' and LOST in final_run['error'], final_run.get('error')


@pytest.mark.asyncio
@pytest.mark.parametrize('saved', ['row', 'checkpoint'])
async def test_f05_control_a_dropped_value_is_not_a_lost_result(tmp_path, monkeypatch, services, saved):
    store, worker, doc, raw = await lab(tmp_path, monkeypatch, services)
    crash_when(monkeypatch, store, item_saved(0) if saved == 'row' else each_checkpoint)
    id, _ = await attempt(store, worker, raw)
    def dropped(index, entry):
        return {**entry, 'value': '', 'value_dropped': True} if index == 0 else entry
    edit_item_rows(store, id, dropped)
    crash_when(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'success', final_run.get('error')


@pytest.mark.asyncio
async def test_f06_control_an_empty_text_result_is_restored(tmp_path, monkeypatch):
    """A text body may return '' legitimately; only a Retrieve item is a lost result."""
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from backend.tests.test_iteration import loop_flow
    monkeypatch.setattr(tool_service, 'public_addresses', lambda host, port: ['93.184.216.34'])
    store = Store(f'sqlite:///{tmp_path}/empty.db', Fernet.generate_key())
    worker = Worker(store)
    async def prepared(_prepared, tenant_id='local'): return codename_issues(2)
    async def body(node_type, settings, text, tenant_id='local'): return ''
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    monkeypatch.setattr(worker.tools, 'execute', body)
    raw = loop_flow(connection_id=jira_connection(store)).model_dump(mode='json')
    crash_when(monkeypatch, store, item_saved(0))
    id, first = await attempt(store, worker, raw)
    assert first['status'] != 'success' and first['loop_progress']['each']['0']['value'] == ''
    crash_when(monkeypatch, store, lambda e: False)
    _, final_run = await attempt(store, worker, id=id)
    assert final_run['status'] == 'success', final_run.get('error')
    assert [r['value'] for r in final_run['checkpoints']['each']['results']] == ['', '']
