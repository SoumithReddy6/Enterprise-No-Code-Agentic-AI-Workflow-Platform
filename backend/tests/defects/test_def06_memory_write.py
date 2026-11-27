"""DEF-06: an Agent's memory write has no fence and is not tied to the Agent's checkpoint.

worker.py:219 calls MemoryService.write (agent_memory.py:19-27) directly: no lease check,
its own commit. agent_runtime.py:486-487 calls it before the Agent returns, so before the
Agent's checkpoint commits (storage.py:703-738). A failure between the two leaves the memory
entry without the checkpoint, and the resumed attempt writes it again.
"""
import json

import pytest

from backend.tests.defects.conftest import final, script_model


@pytest.mark.xfail(strict=True, reason='DEF-06')
@pytest.mark.asyncio
async def test_def06_one_run_adds_one_memory_entry_when_the_checkpoint_commit_fails_once(client, monkeypatch):
    from backend.app.models import Workflow
    from backend.app.worker import Worker
    from backend.app.agent_memory import MemoryService
    graph = Workflow.model_validate({'version': 1, 'name': 'Memory', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': {'provider': 'demo', 'role': 'memory', 'memory_key': 'k'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'agent.text'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'agent'}, {'id': 'b', 'source': 'agent', 'target': 'out'}]})
    store = client.app.state.store
    worker = Worker(store)
    created = client.post('/api/runs', json={'workflow': graph.model_dump(), 'message': 'remember this'})
    assert created.status_code == 201, created.text
    run_id = created.json()['id']
    real = store.worker_event
    failed = []

    def commit_fails_once(id, owner, event, **kwargs):
        # The database refuses the Agent's checkpoint commit once, as a lost connection would.
        if event.get('node_id') == 'agent' and event.get('status') == 'success' and not failed:
            failed.append(1)
            raise RuntimeError('database connection lost')
        return real(id, owner, event, **kwargs)
    monkeypatch.setattr(store, 'worker_event', commit_fails_once)
    script_model(monkeypatch, [final('noted')])
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(run_id)
    assert failed and run['status'] == 'failed' and 'agent' not in run['checkpoints'], run['status']
    assert client.post(f'/api/runs/{run_id}/resume').status_code == 202
    script_model(monkeypatch, [final('noted')])
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run_id)['status'] == 'success'
    entries = json.loads(MemoryService(store).read('k', 'local'))
    assert len(entries) == 1, f'DEF-06: one run wrote {len(entries)} memory entries'
