"""Check current SQLite fencing/atomic storage of the proposed marker as an entry key.

Uses a temporary database only. Does not implement loop replay or TA04 accounting.
Run: .venv/bin/python -I <file> "$PWD"
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys

sys.path.insert(0, sys.argv[1])

from cryptography.fernet import Fernet
from sqlalchemy import event
from sqlalchemy.orm import Session
from backend.app.storage import Store
from backend.tests.test_iteration import loop_flow


with TemporaryDirectory(prefix='relay-ta04-marker-') as directory:
    url = 'sqlite:///' + str(Path(directory) / 'test.db')
    key = Fernet.generate_key()
    store = Store(url, key)
    run = store.create_run(loop_flow().model_dump(), 'facts')
    store.claim_next('owner')
    entry = {'index': 1, 'status': 'failed', 'value': '',
             'error': 'Run token budget exhausted', 'budget_halt': True}
    item_event = {'kind': 'loop_item', 'status': 'info', 'node_id': 'each',
                  'item_index': 1, 'item_result': entry, 'transient': True}
    assert store.worker_event(run['id'], 'stale', item_event) is False
    before = store.run(run['id'])

    def fail_commit(session):
        raise RuntimeError('injected before commit')

    event.listen(Session, 'before_commit', fail_commit, once=True)
    try:
        store.worker_event(run['id'], 'owner', item_event)
    except RuntimeError as exc:
        assert str(exc) == 'injected before commit'
    else:
        raise AssertionError('Commit injection did not run')
    after = store.run(run['id'])
    assert after.get('loop_progress', {}) == before.get('loop_progress', {})
    assert after['events'] == before['events']
    assert store.worker_event(run['id'], 'owner', item_event) is True
    store.engine.dispose()
    reopened = Store(url, key)
    restored = reopened.run(run['id'])
    assert restored['loop_progress']['each']['1'] == entry
    assert any(e.get('kind') == 'loop_item' for e in restored['events'])
    reopened.engine.dispose()
    print(json.dumps({'stale_owner_refused': True, 'failed_commit_saved_neither_row_nor_event': True,
                      'marker_and_entry_survive_database_reopen': True}, indent=2))
