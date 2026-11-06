"""TA05 independent terminal metadata probes; real temporary Store/approval transactions."""
import time

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from backend.app import approvals
from backend.app.storage import JobRecord, RunRecord
from backend.tests.test_accounting_durability import claimed, pause_request, snapshot


OLD = {'policy': 'admission', 'limit': 100, 'spent': 110, 'over': 10}


@pytest.mark.parametrize('path', ['paused-cancel', 'expiry'])
def test_remaining_terminal_paths_record_overshoot(tmp_path, monkeypatch, path):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
    store, id, owner = claimed(tmp_path)
    try:
        assert approvals.pause(store, id, owner, pause_request(), accounting=snapshot(110))
        if path == 'paused-cancel':
            assert store.request_cancel(id) == 'cancelled'
        else:
            with Session(store.engine) as session:
                pending = store.run(id)['approvals'][0]
                session.get(approvals.ApprovalRecord, pending['id']).expires_at = 0
                session.commit()
            approvals.expire_pending(store, id)
        result = store.run(id)
        assert result['status'] == 'cancelled' and result['token_limit'] == OLD
    finally:
        store.engine.dispose()


@pytest.mark.parametrize('limit,expected', [
    ('200', None), ('0', None), ('lots', None), ('120', None),
    ('100', {'policy': 'admission', 'limit': 100, 'spent': 120, 'over': 20}),
    ('50', {'policy': 'admission', 'limit': 50, 'spent': 120, 'over': 70}),
])
def test_terminal_cleanup_itself_replaces_old_metadata(tmp_path, monkeypatch, limit, expected):
    """Seed the helper's own contract directly, without resume's redundant removal."""
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', limit)
    store, id, owner = claimed(tmp_path)
    try:
        assert store.record_accounting(id, owner, snapshot(120))
        with Session(store.engine) as session:
            row = session.get(RunRecord, id)
            row.data = {**row.data, 'token_limit': OLD, 'accounting': snapshot(999)}
            session.commit()
        assert store.finish_run(id, owner, 'failed', error='fixture')
        result = store.run(id)
        assert result['status'] == 'failed' and result['accounting']['tokens_spent'] == 120
        if expected is None:
            assert 'token_limit' not in result
        else:
            assert result['token_limit'] == expected
    finally:
        store.engine.dispose()


@pytest.mark.parametrize('case', ['marked-null', 'marked-list', 'marked-boolean-total', 'legacy-list'])
def test_bad_accounting_cannot_block_finish_or_keep_old_metadata(tmp_path, monkeypatch, case):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
    store, id, owner = claimed(tmp_path)
    try:
        with Session(store.engine) as session:
            row = session.get(RunRecord, id)
            data = {**row.data, 'token_limit': OLD}
            if case == 'legacy-list':
                data.pop('accounting_contract', None)
                data['accounting'] = ['damaged']
                value = None
            elif case == 'marked-null':
                value = None
            elif case == 'marked-list':
                value = ['damaged']
            else:
                value = {**snapshot(110), 'tokens_spent': True}
            row.data, row.accounting = data, value
            session.commit()
        assert store.finish_run(id, owner, 'failed', error='fixture accounting failure')
        result = store.run(id)
        assert result['status'] == 'failed' and 'token_limit' not in result
    finally:
        store.engine.dispose()


def test_stale_worker_cannot_publish_terminal_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
    store, id, owner = claimed(tmp_path)
    try:
        assert store.record_accounting(id, owner, snapshot(110))
        with Session(store.engine) as session:
            job = session.get(JobRecord, id)
            job.owner, job.lease_until = 'other-worker', time.time() + 300
            session.commit()
        assert store.finish_run(id, owner, 'success', output='stale') is False
        result = store.run(id)
        assert result['status'] == 'running' and 'token_limit' not in result
        assert not result.get('finished_at')
    finally:
        store.engine.dispose()


def test_terminal_status_and_metadata_rollback_together(tmp_path, monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
    store, id, owner = claimed(tmp_path)
    def reject_commit(session):
        session.flush()  # Send both fields to the transaction, then force a rollback.
        raise RuntimeError('synthetic terminal commit failure')
    try:
        assert store.record_accounting(id, owner, snapshot(110))
        event.listen(Session, 'before_commit', reject_commit)
        try:
            with pytest.raises(RuntimeError, match='synthetic terminal commit failure'):
                store.finish_run(id, owner, 'success', output='not committed')
        finally:
            event.remove(Session, 'before_commit', reject_commit)
        result = store.run(id)
        assert result['status'] == 'running' and 'token_limit' not in result
        assert result['accounting']['tokens_spent'] == 110 and not result.get('finished_at')
    finally:
        store.engine.dispose()
