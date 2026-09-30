"""A loop's checkpoint references its item rows instead of embedding its results.

run_loop_items is the authoritative copy of every item result. The stored checkpoint
holds a compact marker {stored_in, count, sha256} beside the summary, and every reader
sees the public shape {results, failed, summary} rebuilt from the rows. Checkpoints
written before this still embed their results and keep loading. Rows that are missing or
altered make the checkpoint visibly unavailable; they never become an empty loop.
"""
import asyncio
import json
import pytest
from sqlalchemy.orm import Session
from backend.app.models import Workflow
from backend.app.storage import LOOP_RESULTS_STORE, RunLoopItemRecord, RunRecord, results_digest
from backend.tests.test_loop_completeness import durable_setup, jira_connection, loop_flow


def raw(store, run_id):
    with Session(store.engine) as s: return s.get(RunRecord, run_id).data


def run_to_end(store, worker, workflow):
    row = store.create_run(workflow.model_dump(mode='json'), 'go')
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    return row['id']


def two_loops(connection_id):
    """each processes the tickets; second iterates each.results - a downstream binding."""
    document = loop_flow(connection_id=connection_id).model_dump(mode='json')
    document['nodes'] += [{'id': 'second', 'type': 'for_each', 'inputs': {'items': 'each.results'}, 'config': {'body': 'work2'}},
                          {'id': 'work2', 'type': 'tool_python', 'config': {'code': 'print(input_text)', 'description': 'Reads one result.'}}]
    document['edges'] = [e for e in document['edges'] if e['id'] != 'c'] + [
        {'id': 'c', 'source': 'each', 'target': 'second'}, {'id': 'f', 'source': 'second', 'target': 'out'},
        {'id': 'g', 'source': 'second', 'target': 'work2', 'kind': 'loop', 'sourceHandle': 'body', 'targetHandle': 'input'}]
    return Workflow.model_validate(document)


# --------------------------------------------------------------------------- the stored shape

def test_the_stored_checkpoint_and_event_are_compact_and_readers_see_full_results(tmp_path, monkeypatch):
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch, items=3)
    id = run_to_end(store, worker, loop_flow(connection_id=jira_connection(store)))
    stored = raw(store, id)['checkpoints']['each']
    public = store.run(id)['checkpoints']['each']
    assert stored['results']['stored_in'] == LOOP_RESULTS_STORE and stored['results']['count'] == 3
    assert stored['summary'] == public['summary'] and stored['failed'] == public['failed']
    assert [r['index'] for r in public['results']] == [0, 1, 2] and all(r['value'] == 'done' for r in public['results'])
    assert stored['results']['sha256'] == results_digest(public['results'])
    event = next(e for e in store.run(id)['events'] if e.get('node_id') == 'each' and e['status'] == 'success')
    assert event['outputs']['results'] == stored['results'], 'the event log must not keep a second full copy'


def test_an_empty_loop_keeps_its_trivial_results_embedded(tmp_path, monkeypatch):
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch, items=0)
    id = run_to_end(store, worker, loop_flow(connection_id=jira_connection(store)))
    assert raw(store, id)['checkpoints']['each']['results'] == []


# --------------------------------------------------------------------------- downstream and resume

def execute_two_loops(tmp_path, monkeypatch, crash_after_each):
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch, items=3)
    calls = []
    async def execute(node_type, settings, text, tenant_id='local'):
        calls.append(text); return f'done {len(calls)}'
    monkeypatch.setattr(worker.tools, 'execute', execute)
    original = store.worker_event
    if crash_after_each:
        def crash(id, owner, event):
            persisted = original(id, owner, event)
            if event.get('node_id') == 'each' and event['status'] == 'success' and not event.get('transient'):
                raise asyncio.CancelledError()
            return persisted
        monkeypatch.setattr(store, 'worker_event', crash)
    row = store.create_run(two_loops(jira_connection(store)).model_dump(mode='json'), 'go')
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    if crash_after_each:
        assert raw(store, row['id'])['checkpoints']['each']['results']['stored_in'] == LOOP_RESULTS_STORE
        monkeypatch.setattr(store, 'worker_event', original)
        store.resume_run(row['id'])
        asyncio.run(worker.execute(store.claim_next(worker.owner)))
    return store.run(row['id']), calls


def test_a_downstream_binding_receives_identical_results_fresh_and_resumed(tmp_path, monkeypatch):
    """second binds each.results. Resumed after each's compact checkpoint, it must see
    exactly what an uninterrupted run passed it."""
    (tmp_path / 'fresh').mkdir(); (tmp_path / 'resumed').mkdir()
    fresh, fresh_calls = execute_two_loops(tmp_path / 'fresh', monkeypatch, False)
    resumed, resumed_calls = execute_two_loops(tmp_path / 'resumed', monkeypatch, True)
    assert fresh['status'] == resumed['status'] == 'success'
    assert resumed_calls == fresh_calls, 'no item repeated, and second received the same entries'
    assert [json.loads(text)['value'] for text in fresh_calls[3:]] == ['done 1', 'done 2', 'done 3']
    assert resumed['checkpoints'] == fresh['checkpoints']


# --------------------------------------------------------------------------- compatibility

def test_a_legacy_checkpoint_with_embedded_results_still_loads_and_resumes(tmp_path, monkeypatch):
    (tmp_path / 'b').mkdir()
    store, worker, _, _ = durable_setup(tmp_path / 'b', monkeypatch, items=3)
    calls = []
    async def execute(node_type, settings, text, tenant_id='local'):
        calls.append(text); return f'done {len(calls)}'
    monkeypatch.setattr(worker.tools, 'execute', execute)
    original = store.worker_event
    def crash(id, owner, event):
        persisted = original(id, owner, event)
        if event.get('node_id') == 'each' and event['status'] == 'success' and not event.get('transient'): raise asyncio.CancelledError()
        return persisted
    monkeypatch.setattr(store, 'worker_event', crash)
    row = store.create_run(two_loops(jira_connection(store)).model_dump(mode='json'), 'go')
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    embedded = store.run(row['id'])['checkpoints']['each']
    with Session(store.engine) as s:  # Rewrite as the previous release stored it.
        record = s.get(RunRecord, row['id'])
        record.data = {**record.data, 'checkpoints': {**record.data['checkpoints'], 'each': embedded}}
        s.query(RunLoopItemRecord).filter(RunLoopItemRecord.run_id == row['id']).delete(); s.commit()
    assert store.run(row['id'])['checkpoints']['each'] == embedded
    monkeypatch.setattr(store, 'worker_event', original)
    store.resume_run(row['id'])
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    run = store.run(row['id'])
    assert run['status'] == 'success' and len(calls) == 6
    assert [json.loads(text)['value'] for text in calls[3:]] == ['done 1', 'done 2', 'done 3']


def test_results_restored_from_document_progress_stay_embedded(tmp_path, monkeypatch):
    """Items finished under the previous release have no row, so the rows cannot prove
    the results; the checkpoint keeps them embedded."""
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch, items=3)
    row = store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    with Session(store.engine) as s:
        record = s.get(RunRecord, row['id'])
        record.data = {**record.data, 'loop_progress': {'each': {'0': {'index': 0, 'status': 'success', 'value': 'from the document', 'error': ''}}}}
        s.commit()
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    stored = raw(store, row['id'])['checkpoints']['each']['results']
    assert isinstance(stored, list) and stored[0]['value'] == 'from the document' and len(stored) == 3


# --------------------------------------------------------------------------- failure is visible

@pytest.mark.parametrize('damage', ['missing', 'altered'])
def test_damaged_rows_make_the_checkpoint_unavailable_never_empty(tmp_path, monkeypatch, damage):
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch, items=3)
    calls = []
    async def execute(node_type, settings, text, tenant_id='local'):
        calls.append(text); return 'done'
    monkeypatch.setattr(worker.tools, 'execute', execute)
    original = store.worker_event
    def crash(id, owner, event):
        persisted = original(id, owner, event)
        if event.get('node_id') == 'each' and event['status'] == 'success' and not event.get('transient'): raise asyncio.CancelledError()
        return persisted
    monkeypatch.setattr(store, 'worker_event', crash)
    row = store.create_run(two_loops(jira_connection(store)).model_dump(mode='json'), 'go')
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    with Session(store.engine) as s:
        item = s.get(RunLoopItemRecord, (row['id'], 'each', 1))
        if damage == 'missing': s.delete(item)
        else: item.entry = {**item.entry, 'value': 'tampered'}
        s.commit()
    shown = store.run(row['id'])['checkpoints']['each']['results']
    assert isinstance(shown, dict) and 'cannot be restored' in shown['unavailable']
    assert ('1 of 3 stored item results are missing' in shown['unavailable']) is (damage == 'missing')
    monkeypatch.setattr(store, 'worker_event', original)
    store.resume_run(row['id'])
    before = len(calls)
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    run = store.run(row['id'])
    assert run['status'] == 'failed' and 'cannot be restored' in run['error']
    assert len(calls) == before, 'nothing downstream ran on unrestorable results'


def test_the_loop_checkpoint_is_written_only_under_the_lease(tmp_path, monkeypatch):
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch, items=1)
    row = store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    store.claim_next('worker')
    store.worker_event(row['id'], 'worker', {'kind': 'loop_item', 'status': 'info', 'node_id': 'each', 'item_index': 0, 'transient': True,
                                             'item_result': {'index': 0, 'status': 'success', 'value': 'done', 'error': ''}})
    success = {'node_id': 'each', 'status': 'success', 'outputs': {'results': [{'index': 0, 'status': 'success', 'value': 'done', 'error': ''}],
                                                                   'failed': '0', 'summary': '{}'}}
    assert store.worker_event(row['id'], 'intruder', success) is False
    assert 'each' not in raw(store, row['id']).get('checkpoints', {})
    assert store.worker_event(row['id'], 'worker', success) is True
    assert raw(store, row['id'])['checkpoints']['each']['results']['count'] == 1
