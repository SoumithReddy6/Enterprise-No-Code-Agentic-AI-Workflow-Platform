"""DEF-09: an error in the heartbeat query leaves the run's graph task running.

Worker._execute (worker.py:242-274) cancels the graph task only in its CancelledError
branch (worker.py:265). When cancel_requested or heartbeat raises (for example a lost
database connection), control goes to the generic branch at worker.py:270-274: the run is
finished as failed, and the task that executes the graph is never cancelled. It continues
with no heartbeat and no supervisor, and its call in flight completes after the run was
reported as failed.
"""
import asyncio

import pytest

from backend.tests.test_platform_integration import write_flow


@pytest.mark.xfail(strict=True, reason='DEF-09')
@pytest.mark.asyncio
async def test_def09_a_heartbeat_error_cancels_the_graph_task(client, monkeypatch):
    from backend.app.worker import Worker
    connection = client.post('/api/connections', json={'name': 'API', 'provider': 'http', 'endpoint': 'https://example.com'}).json()
    store = client.app.state.store
    worker = Worker(store)
    in_flight = asyncio.Event()
    release = asyncio.Event()
    completed = []

    async def slow_receiver(*args):
        in_flight.set()
        await release.wait()  # A cancelled task stops here and never records completion.
        completed.append('sent')
        return 'accepted'
    monkeypatch.setattr(worker.tools, 'execute', slow_receiver)
    real = store.heartbeat

    def heartbeat(id, owner, lease_seconds=30):
        if in_flight.is_set():
            raise RuntimeError('database connection lost')
        return real(id, owner, lease_seconds)
    monkeypatch.setattr(store, 'heartbeat', heartbeat)
    run_id = client.post('/api/runs', json={'workflow': write_flow(connection['id']).model_dump(), 'message': '{}'}).json()['id']
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run_id)['status'] == 'failed'  # Control: the worker gave the run up.
    release.set()
    await asyncio.sleep(0.2)
    assert completed == [], 'DEF-09: the graph task kept running after the worker finished the run as failed'
