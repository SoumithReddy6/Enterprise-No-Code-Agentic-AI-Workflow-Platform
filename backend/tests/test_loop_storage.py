"""Loop item progress is stored as one row per item, not inside the run document.

Every event rewrites the run document in full, so progress kept there cost O(n^2) bytes
for an n-item batch. Rows make it one insert per item. Older runs keep their progress in
the document; every reader merges both stores, and a row wins for the same item.
"""
import asyncio
import json
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session
from backend.app import schema
from backend.app.storage import RunLoopItemRecord, RunRecord
from backend.tests.test_loop_completeness import durable_setup, jira_connection, loop_flow


def rows(store, run_id):
    with Session(store.engine) as s:
        return {(r.node_id, r.item_index): r.entry for r in
                s.scalars(select(RunLoopItemRecord).where(RunLoopItemRecord.run_id == run_id))}


def claimed_run(store):
    row = store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    store.claim_next('worker')
    return row['id']


def item_event(index, value='done', **extra):
    return {'kind': 'loop_item', 'status': 'info', 'node_id': 'each', 'item_index': index, 'transient': True,
            'item_result': {'index': index, 'status': 'success', 'value': value, 'error': '', **extra}}


# --------------------------------------------------------------------------- writes

def test_items_are_rows_and_the_run_document_does_not_grow(tmp_path, monkeypatch):
    store, *_ = durable_setup(tmp_path, monkeypatch)
    id = claimed_run(store)
    with Session(store.engine) as s: before = len(json.dumps(s.get(RunRecord, id).data))
    for index in range(5): assert store.worker_event(id, 'worker', item_event(index, 'v' * 5000))
    with Session(store.engine) as s: after = s.get(RunRecord, id).data
    assert 'loop_progress' not in after and len(json.dumps(after)) == before
    assert sorted(rows(store, id)) == [('each', i) for i in range(5)]


def test_a_repeated_item_event_updates_its_row_instead_of_duplicating_it(tmp_path, monkeypatch):
    store, *_ = durable_setup(tmp_path, monkeypatch)
    id = claimed_run(store)
    store.worker_event(id, 'worker', item_event(0, 'first'))
    store.worker_event(id, 'worker', item_event(0, 'second'))
    assert rows(store, id) == {('each', 0): {'index': 0, 'status': 'success', 'value': 'second', 'error': ''}}


def test_the_row_and_its_event_commit_together_or_not_at_all(tmp_path, monkeypatch):
    store, *_ = durable_setup(tmp_path, monkeypatch)
    id = claimed_run(store)
    events_before = len(store.run(id)['events'])
    # A value the JSON column cannot store fails the flush: the event must roll back with it.
    with pytest.raises(Exception):
        store.worker_event(id, 'worker', {**item_event(0), 'item_result': {'index': 0, 'value': {1, 2}}})
    assert rows(store, id) == {} and len(store.run(id)['events']) == events_before


def test_a_worker_without_the_lease_writes_neither(tmp_path, monkeypatch):
    store, *_ = durable_setup(tmp_path, monkeypatch)
    id = claimed_run(store)
    events_before = len(store.run(id)['events'])
    assert store.worker_event(id, 'someone-else', item_event(0)) is False
    assert rows(store, id) == {} and len(store.run(id)['events']) == events_before


# --------------------------------------------------------------------------- previews

def test_item_events_keep_previews_while_the_row_keeps_the_full_value(tmp_path, monkeypatch):
    from backend.app.iteration import PREVIEW_CHARS
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch)
    long = 'é' * (PREVIEW_CHARS + 500)
    async def execute(node_type, settings, text, tenant_id='local'): return long
    monkeypatch.setattr(worker.tools, 'execute', execute)
    row = store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    run = store.run(row['id'])
    assert run['status'] == 'success'
    body = [e for e in run['events'] if e.get('node_id') == 'work' and e.get('status') == 'success']
    items = [e for e in run['events'] if e.get('kind') == 'loop_item']
    assert body and items
    assert all(e['outputs']['text'] == long[:PREVIEW_CHARS] and e['preview_of'] == ['text'] for e in body)
    assert all(e['item_result']['value'] == long[:PREVIEW_CHARS] and e['preview_of'] == ['value'] for e in items)
    # The durable copies are whole: the item rows and the loop's checkpointed results.
    assert all(entry['value'] == long for entry in rows(store, row['id']).values())
    assert all(r['value'] == long for r in run['checkpoints']['each']['results'])


def test_non_item_events_are_never_shortened():
    from backend.app.iteration import PREVIEW_CHARS
    from backend.app.storage import observed
    long = 'x' * (PREVIEW_CHARS * 3)
    for event in ({'node_id': 'send', 'status': 'success', 'outputs': {'text': long}},
                  {'node_id': 'send', 'status': 'paused', 'approval': {'payload': long}},
                  {'kind': 'run', 'status': 'failed', 'error': long}):
        assert observed(event) is event


# --------------------------------------------------------------------------- reads

def seed_mixed(store, run_id, legacy, table):
    """Put `legacy` entries in the run document and `table` entries in rows."""
    with Session(store.engine) as s:
        record = s.get(RunRecord, run_id)
        record.data = {**record.data, 'loop_progress': {'each': {str(i): e for i, e in legacy.items()}}}
        for i, e in table.items(): s.merge(RunLoopItemRecord(run_id=run_id, node_id='each', item_index=i, entry=e))
        s.commit()


def entry(index, value):
    return {'index': index, 'status': 'success', 'value': value, 'error': ''}


@pytest.mark.parametrize('layout', ['legacy only', 'table only', 'mixed'])
def test_resume_replays_progress_from_either_store(tmp_path, monkeypatch, layout):
    """Items 0-2 finished earlier. Whichever store holds them, they are not re-executed,
    and where both hold an item the row wins."""
    store, worker, _, calls = durable_setup(tmp_path, monkeypatch, items=4)
    row = store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    done = {i: entry(i, f'earlier {i}') for i in range(3)}
    legacy, table = {'legacy only': (done, {}), 'table only': ({}, done),
                     'mixed': ({0: done[0], 1: entry(1, 'stale document copy')}, {1: done[1], 2: done[2]})}[layout]
    seed_mixed(store, row['id'], legacy, table)
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    run = store.run(row['id'])
    assert run['status'] == 'success'
    assert len(calls) == 1, f'{layout}: finished items were re-executed: {calls}'
    assert [r['value'] for r in run['checkpoints']['each']['results']] == ['earlier 0', 'earlier 1', 'earlier 2', 'done']


def test_every_reader_sees_the_merged_progress(tmp_path, monkeypatch):
    store, *_ = durable_setup(tmp_path, monkeypatch)
    row = store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    seed_mixed(store, row['id'], {0: entry(0, 'document'), 1: entry(1, 'document')}, {1: entry(1, 'row')})
    expected = {'each': {'0': entry(0, 'document'), '1': entry(1, 'row')}}
    assert store.run(row['id'])['loop_progress'] == expected
    assert store.claim_next('worker')['loop_progress'] == expected


def test_write_settlement_reads_table_only_progress(tmp_path, monkeypatch):
    """A legacy owner-style write marker is settled by its item's durable result. That
    result may now be a row only; without it the write is uncertain and resume refuses."""
    store, *_ = durable_setup(tmp_path, monkeypatch)
    row = store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    store.claim_next('worker')
    store.mark_write(row['id'], 'worker', 'each:1')
    store.finish_run(row['id'], 'worker', 'failed', error='interrupted')
    with pytest.raises(ValueError, match='external write'):
        store.resume_run(row['id'])
    seed_mixed(store, row['id'], {}, {1: entry(1, 'sent')})
    assert store.resume_run(row['id'])['status'] == 'queued'


# --------------------------------------------------------------------------- accounting

def document_bytes(store, id):
    with Session(store.engine) as s: return json.dumps(s.get(RunRecord, id).data)


def test_a_reservation_updates_the_accounting_column_not_the_document(tmp_path, monkeypatch):
    store, *_ = durable_setup(tmp_path, monkeypatch)
    id = claimed_run(store)
    before = document_bytes(store, id)
    snapshot = {'action_budget': {'remaining': 3, 'counter': 1, 'ceiling': 4, 'charged': ['each:0:work#0']}}
    store.reserve_accounting(id, 'worker', snapshot)
    store.record_accounting(id, 'worker', {**snapshot, 'tokens_spent': 5})
    assert document_bytes(store, id) == before
    assert store.run(id)['accounting'] == {**snapshot, 'tokens_spent': 5}


def test_legacy_document_accounting_is_read_until_the_column_is_written(tmp_path, monkeypatch):
    store, *_ = durable_setup(tmp_path, monkeypatch)
    id = claimed_run(store)
    legacy = {'action_budget': {'remaining': 1, 'counter': 3, 'ceiling': 4, 'charged': []}}
    with Session(store.engine) as s:
        record = s.get(RunRecord, id); record.data = {**record.data, 'accounting': legacy}; s.commit()
    assert store.run(id)['accounting'] == legacy
    # An unchanged snapshot is recognised against the legacy copy and not rewritten.
    store.record_accounting(id, 'worker', legacy)
    with Session(store.engine) as s: assert s.get(RunRecord, id).accounting is None
    current = {'action_budget': {**legacy['action_budget'], 'remaining': 0, 'counter': 4}}
    store.reserve_accounting(id, 'worker', current)
    assert store.run(id)['accounting'] == current


@pytest.mark.parametrize('stored', ['column', 'legacy document'])
def test_an_approval_pause_defers_its_reservation_in_the_column(tmp_path, monkeypatch, stored):
    """The paused action's reservation is recorded in the transaction that releases the
    lease; it must land where readers look, whichever store held the accounting."""
    from backend.app import approvals
    store, *_ = durable_setup(tmp_path, monkeypatch)
    id = claimed_run(store)
    accounting = {'action_budget': {'remaining': 2, 'counter': 2, 'ceiling': 4, 'charged': ['tickets#0', 'each:0:send#0']}}
    if stored == 'column':
        store.reserve_accounting(id, 'worker', accounting)
    else:
        with Session(store.engine) as s:
            record = s.get(RunRecord, id); record.data = {**record.data, 'accounting': accounting}; s.commit()
    pause = approvals.ApprovalPause('send', 'each:0', 'each:0:send', {'payload': {'to': 'a@example.test', 'body': 'x' * 5000}})
    approvals.pause(store, id, 'worker', pause)
    run = store.run(id)
    assert run['accounting']['action_budget']['deferred'] == ['each:0:send#0']
    assert run['approvals'][0]['payload']['body'] == 'x' * 5000, 'approval payloads are never previewed'


# --------------------------------------------------------------------------- migration

def test_0003_upgrades_from_0002_and_downgrades_cleanly(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/0003.db')
    with engine.begin() as conn:
        schema.upgrade(conn, '0002_truncation_source')
        conn.execute(text("INSERT INTO runs (id,tenant_id,data,created_at,status,name,citation_counter,grounded,abstained,truncated,truncation_source) "
                          "VALUES ('r','local',:data,'2026-09-01','failed','r',0,0,0,0,'')"),
                     {'data': json.dumps({'loop_progress': {'each': {'0': entry(0, 'kept')}}})})
    with engine.begin() as conn:
        schema.upgrade(conn, '0003_loop_storage')
        assert 'run_loop_items' in inspect(conn).get_table_names()
        assert 'accounting' in {c['name'] for c in inspect(conn).get_columns('runs')}
        assert conn.execute(text("SELECT accounting FROM runs WHERE id='r'")).scalar() is None
        # No backfill: existing document progress stays where it is and is dual-read.
        assert conn.execute(text('SELECT count(*) FROM run_loop_items')).scalar() == 0
        assert json.loads(conn.execute(text("SELECT data FROM runs WHERE id='r'")).scalar())['loop_progress']['each']['0']['value'] == 'kept'
    from alembic import command
    with engine.begin() as conn:
        command.downgrade(schema._config(conn), '0002_truncation_source')
        assert 'run_loop_items' not in inspect(conn).get_table_names()
        assert 'accounting' not in {c['name'] for c in inspect(conn).get_columns('runs')}
    with engine.begin() as conn:
        schema.upgrade(conn)
        assert schema.current_revision(conn) == schema.head_revision() == '0003_loop_storage'
