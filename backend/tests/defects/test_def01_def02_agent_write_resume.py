"""DEF-01 and DEF-02: an Agent's settled write is keyed by its step position only.

The invocation of an attached tool call is '<owner>:<step>:<target>' (agent_runtime.py:346).
A settled write is found again only by that id, with no comparison of the request
(worker.py:198-199). An Agent keeps no saved steps except at an approval pause
(agent_runtime.py:257,408), so after a failure it starts at step 0 again and asks the model
again. The model's second answer decides the step of each call.

Both tests: attempt 1 sends write X at step 1 and settles it; the model call for step 2 then
fails, so the run fails with no unsettled write and the API allows resume.
"""
import pytest

from backend.tests.defects.conftest import agent_write_flow, call, final, script_model

X = '{"ticket": "X"}'
Y = '{"ticket": "Y"}'


async def first_attempt(client, monkeypatch):
    from backend.app.worker import Worker
    connection = client.post('/api/connections', json={'name': 'API', 'provider': 'http', 'endpoint': 'https://example.com'}).json()
    store = client.app.state.store
    worker = Worker(store)
    sent = []

    async def receiver(node_type, settings, input_text, tenant):
        sent.append(input_text)
        return 'accepted'
    monkeypatch.setattr(worker.tools, 'execute', receiver)
    script_model(monkeypatch, [call('post', X), ValueError('Model provider is unavailable.')])
    created = client.post('/api/runs', json={'workflow': agent_write_flow(connection['id']).model_dump()})
    assert created.status_code == 201, created.text
    run_id = created.json()['id']
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(run_id)
    # Control: the write was sent once and settled, and the run failed for the model error.
    assert sent == [X] and run['status'] == 'failed' and not run.get('write_nodes'), run
    assert client.post(f'/api/runs/{run_id}/resume').status_code == 202
    return store, worker, run_id, sent


@pytest.mark.xfail(strict=True, reason='DEF-01')
@pytest.mark.asyncio
async def test_def01_a_settled_agent_write_is_not_sent_again_when_its_step_moves(client, monkeypatch):
    store, worker, run_id, sent = await first_attempt(client, monkeypatch)
    # On resume the model first gives an empty input (a tool_error that only advances the
    # step), then the same write X. X is now at step 2: invocation 'agent:2:post'.
    script_model(monkeypatch, [call('post', ''), call('post', X), final('done')])
    await worker.execute(store.claim_next(worker.owner))
    assert sent == [X], f'DEF-01: the receiver got the same write {len(sent)} times: {sent}'


@pytest.mark.xfail(strict=True, reason='DEF-02')
@pytest.mark.asyncio
async def test_def02_a_stored_write_result_is_not_returned_for_a_different_request(client, monkeypatch):
    store, worker, run_id, sent = await first_attempt(client, monkeypatch)
    # On resume the model asks for a different write Y at the same step.
    script_model(monkeypatch, [call('post', Y), final('done')])
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(run_id)
    reported_success = any(e.get('node_id') == 'post' and e.get('status') == 'success' for e in run['events'][-6:])
    # Correct outcomes: Y is sent, or the run stops. Never: success for Y with nothing sent.
    assert Y in sent or run['status'] == 'failed', (
        f'DEF-02: run {run["status"]}, tool success reported={reported_success}, but the receiver got only {sent}')
