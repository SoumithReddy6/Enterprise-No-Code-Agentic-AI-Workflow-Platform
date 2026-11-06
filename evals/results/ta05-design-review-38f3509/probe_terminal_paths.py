"""TA05 design probes: real Store/approvals and isolated temporary accounting fixtures.

The finish_run spy observes terminal routing; it does not add the future metadata.
The resume fixture seeds a prospective token_limit through the existing updates API
to demonstrate merge/retention, not to claim TA05 is already implemented.
"""
import json
import logging
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from backend.app import approvals
from backend.tests.test_accounting_durability import claimed, pause_request, snapshot


def terminal_path(root, name):
    directory = root / name
    directory.mkdir()
    store, id, owner = claimed(directory)
    calls = []
    original = store.finish_run
    def observed(*args, **kwargs):
        calls.append(args[2])
        return original(*args, **kwargs)
    store.finish_run = observed
    try:
        assert store.record_accounting(id, owner, snapshot(110)) is True
        if name == 'worker-finish-control':
            store.finish_run(id, owner, 'failed', error='synthetic completed attempt')
        elif name == 'queued-cancel':
            store.release(id, owner)
            store.request_cancel(id)
        else:
            assert approvals.pause(store, id, owner, pause_request(), accounting=snapshot(110)) is True
            row = store.run(id)['approvals'][0]
            if name == 'paused-cancel':
                store.request_cancel(id)
            elif name == 'approval-reject':
                approvals.decide(store, id, 'local', row['id'], row['digest'], 'reject')
            elif name == 'approval-expire':
                with Session(store.engine) as session:
                    session.get(approvals.ApprovalRecord, row['id']).expires_at = 0
                    session.commit()
                approvals.expire_pending(store, id)
            else:
                raise AssertionError(name)
        result = store.run(id)
        assert result['accounting']['tokens_spent'] == 110
        assert result['status'] == ('failed' if name == 'worker-finish-control' else 'cancelled')
        assert len(calls) == (1 if name == 'worker-finish-control' else 0)
        return {'case': name, 'status': result['status'], 'durable_tokens': 110,
                'finish_run_calls': len(calls),
                'sole_proposed_hook_is_reached': bool(calls)}
    finally:
        store.engine.dispose()


def retained_record(root, name, new_limit):
    directory = root / name
    directory.mkdir()
    store, id, owner = claimed(directory)
    patch = pytest.MonkeyPatch()
    previous = {'policy': 'admission', 'limit': 100, 'spent': 110, 'over': 10}
    try:
        assert store.record_accounting(id, owner, snapshot(110)) is True
        # Seed a prior terminal record via the real existing update interface. This is
        # the state the proposed feature creates after its first over-limit attempt.
        assert store.finish_run(id, owner, 'failed', token_limit=previous) is True
        store.resume_run(id)
        queued = store.run(id)
        next_owner = 'ta05-resume'
        assert store.claim_next(next_owner, lease_seconds=300)['id'] == id
        assert store.record_accounting(id, next_owner, snapshot(120)) is True
        patch.setenv('RELAY_RUN_TOKEN_LIMIT', new_limit)
        # Under/disabled/invalid means the proposed helper returns no record. Merely
        # omitting a new token_limit argument does not delete the prior value.
        assert store.finish_run(id, next_owner, 'success', output='done') is True
        result = store.run(id)
        assert result['accounting']['tokens_spent'] == 120
        assert result['token_limit'] == previous
        return {'case': name, 'limit_at_next_finish': new_limit, 'durable_tokens': 120,
                'record_after_resume': queued.get('token_limit'),
                'record_after_finish_with_no_new_record': result['token_limit'],
                'required_by_draft': 'token_limit omitted'}
    finally:
        patch.undo()
        store.engine.dispose()


def main():
    logging.getLogger('relay.journal').setLevel(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix='ta05-review-') as directory:
        root = Path(directory)
        terminal = [terminal_path(root, name) for name in (
            'worker-finish-control', 'queued-cancel', 'paused-cancel',
            'approval-reject', 'approval-expire')]
        resumed = [retained_record(root, name, value) for name, value in (
            ('resume-under-limit', '200'), ('resume-unlimited', '0'),
            ('resume-invalid-setting', 'lots'))]
    destination = Path(__file__).with_name('probe-results.json')
    destination.write_text(json.dumps({'application_baseline': '38f3509',
                                      'terminal_paths': terminal, 'retention_cases': resumed}, indent=2) + '\n')
    print(json.dumps({'terminal_paths_checked': len(terminal),
                      'paths_bypassing_finish_run': sum(not r['sole_proposed_hook_is_reached'] for r in terminal),
                      'retained_record_cases': len(resumed), 'assertions': 'passed'}, indent=2))


if __name__ == '__main__':
    main()
