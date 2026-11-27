"""Claim-path defects in Store.claim_next (storage.py:669-686)."""
import pytest
from sqlalchemy.orm import Session

from backend.tests.defects.conftest import expire_lease

WORKFLOW = {'version': 1, 'name': 'Echo', 'nodes': [
    {'id': 'input', 'type': 'chat_input'}, {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
    'edges': [{'id': 'a', 'source': 'input', 'target': 'out'}]}


@pytest.mark.xfail(strict=True, reason='DEF-04')
def test_def04_a_run_that_never_finishes_is_not_claimed_without_limit(client):
    """DEF-04: execution_jobs has no attempt count (storage.py:112-119), so a run whose
    worker dies on every attempt is claimed again after each lease expiry, without end."""
    store = client.app.state.store
    run_id = store.create_run(WORKFLOW, 'hello')['id']
    claims = 0
    for attempt in range(25):
        job = store.claim_next(f'worker-{attempt}')
        if job is None:
            break
        claims += 1
        expire_lease(store, run_id)  # The worker died: no finish, no release.
    assert claims <= 10, f'DEF-04: the same run was claimed {claims} times and is still offered'
    assert store.run(run_id)['status'] == 'failed'


@pytest.mark.xfail(strict=True, reason='DEF-05')
def test_def05_a_cancelled_run_is_not_claimed_and_started_again(client):
    """DEF-05: cancel on a running job only sets cancel_requested (storage.py:779). When that
    worker is dead, the next claim ignores the flag (storage.py:671-676): it hands the run
    to a new worker, sets it running and writes a 'running' event."""
    store = client.app.state.store
    run_id = store.create_run(WORKFLOW, 'hello')['id']
    assert store.claim_next('dead-worker')
    assert store.request_cancel(run_id) == 'running'  # The flag is set; the worker never reads it.
    expire_lease(store, run_id)
    job = store.claim_next('new-worker')
    assert job is None, 'DEF-05: a run with cancel_requested was claimed and set running again'
    assert store.run(run_id)['status'] == 'cancelled'


@pytest.mark.xfail(strict=True, reason='DEF-07')
def test_def07_one_job_without_a_run_does_not_block_the_queue(client):
    """DEF-07: no foreign key ties execution_jobs to runs. A job row with no run makes
    claim_next raise at storage.py:679 on every poll, and it is always the first candidate
    (ORDER BY created), so no later run is ever claimed."""
    from backend.app.storage import JobRecord
    store = client.app.state.store
    with Session(store.engine) as s:
        s.add(JobRecord(run_id='orphan', status='queued', created=0))
        s.commit()
    run_id = store.create_run(WORKFLOW, 'hello')['id']
    try:
        job = store.claim_next('worker')
    except Exception as exc:
        pytest.fail(f'DEF-07: claim_next raised {type(exc).__name__} and the valid run stays queued')
    assert job and job['id'] == run_id
