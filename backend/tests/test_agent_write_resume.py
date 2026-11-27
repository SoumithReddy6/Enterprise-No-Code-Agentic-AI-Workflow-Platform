"""An Agent continues after a settled write without sending it again (DEF-01, DEF-02).

Card: docs/tasks/01-DEF-01-02-agent-write-resume.md. An attached tool call's invocation is
'<owner>:<step>:<target>' (agent_runtime.py), so a settled write is found again only at the
same step. Part A saves every open Agent frame on the call path in the settle_write commit,
so a resumed Agent replays the settled step with no model call. Part B stores the request
digest with the result, so a different request at the same invocation stops the run.
"""
import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.tests.defects.conftest import agent_write_flow, call, final, script_model

X = '{"ticket": "X"}'
Y = '{"ticket": "Y"}'
ACCEPTED = '"tool_result": "accepted"'


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.app.main import create_app
    monkeypatch.setenv('VECTOR_DATA_DIR', str(tmp_path / 'vectors'))
    app = create_app(f'sqlite:///{tmp_path}/resume.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    with TestClient(app) as c:
        yield c


def setup(client, monkeypatch):
    from backend.app import tool_service
    from backend.app.worker import Worker
    monkeypatch.setattr(tool_service, 'public_addresses', lambda host, port: ['93.184.216.34'])
    connection = client.post('/api/connections', json={'name': 'API', 'provider': 'http', 'endpoint': 'https://example.com'}).json()
    store = client.app.state.store
    worker = Worker(store)
    sent = []

    async def receiver(node_type, settings, input_text, tenant):
        sent.append(input_text)
        return 'accepted'
    monkeypatch.setattr(worker.tools, 'execute', receiver)
    return store, worker, connection['id'], sent


def model_by_prompt(monkeypatch, reply):
    """reply(prompt) -> text, or an Exception to raise. Records every prompt."""
    from backend.app import registry
    prompts = []

    async def model(inputs, config, ctx):
        prompts.append(inputs['prompt'])
        out = reply(inputs['prompt'])
        if isinstance(out, BaseException):
            raise out
        return {'text': out, 'provider': 'demo'}
    monkeypatch.setattr(registry, 'llm_node', model)
    return prompts


def start(client, workflow):
    created = client.post('/api/runs', json={'workflow': workflow.model_dump()})
    assert created.status_code == 201, created.text
    return created.json()['id']


async def first_attempt(client, monkeypatch):
    """Attempt 1 sends X at step 1 and settles it; the next model call fails, so the run fails."""
    store, worker, connection, sent = setup(client, monkeypatch)
    script_model(monkeypatch, [call('post', X), ValueError('Model provider is unavailable.')])
    run_id = start(client, agent_write_flow(connection))
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(run_id)
    assert sent == [X] and run['status'] == 'failed' and not run.get('write_nodes'), run
    return store, worker, run_id, sent


async def resume(client, store, worker, run_id):
    assert client.post(f'/api/runs/{run_id}/resume').status_code == 202
    await worker.execute(store.claim_next(worker.owner))
    return store.run(run_id)


def faithful(monkeypatch, restart):
    """A resumed model that gives the final answer once its transcript shows the write as done,
    and otherwise does the task again in a different step order (the DEF-01 trigger)."""
    pending = iter(restart)
    return model_by_prompt(monkeypatch, lambda p: final('done') if ACCEPTED in p else next(pending))


# --------------------------------------------------------------------------- Part A: DEF-01

@pytest.mark.asyncio
async def test_def01_a_settled_agent_write_is_not_sent_again_when_its_step_moves(client, monkeypatch):
    store, worker, run_id, sent = await first_attempt(client, monkeypatch)
    faithful(monkeypatch, [call('post', ''), call('post', X), final('done')])
    run = await resume(client, store, worker, run_id)
    assert run['status'] == 'success'
    assert sent == [X], f'DEF-01: the receiver got the same write {len(sent)} times: {sent}'


@pytest.mark.asyncio
async def test_settle_write_saves_the_open_frames_in_its_commit(client, monkeypatch):
    from backend.app.storage import Store
    commits = []
    original_settle = Store.settle_write
    original_commit = Session.commit

    def counting_commit(self):
        if commits and commits[-1] is not None:
            commits[-1] += 1
        return original_commit(self)

    def spied_settle(self, *args, **kwargs):
        commits.append(0)
        try:
            return original_settle(self, *args, **kwargs)
        finally:
            commits.append(None)
    monkeypatch.setattr(Session, 'commit', counting_commit)
    monkeypatch.setattr(Store, 'settle_write', spied_settle)
    store, worker, run_id, sent = await first_attempt(client, monkeypatch)
    frames = store.run(run_id)['agent_frames']
    assert set(frames) == {'agent:0:agent'}, frames
    frame = frames['agent:0:agent']
    assert frame['step'] == 0 and frame['action'] == json.loads(call('post', X)) and frame['invocation'] == 'agent:1:post'
    assert frame['checkpoint_owner'] == 'agent'
    assert [c for c in commits if c is not None] == [1], f'settle_write must commit once: {commits}'


@pytest.mark.asyncio
async def test_a_kill_between_send_and_settle_saves_no_frame_and_blocks_resume(client, monkeypatch):
    from backend.app.storage import Store
    store, worker, connection, sent = setup(client, monkeypatch)

    def killed(self, *args, **kwargs):
        raise RuntimeError('process killed before the settle commit')
    monkeypatch.setattr(Store, 'settle_write', killed)
    script_model(monkeypatch, [call('post', X)])
    run_id = start(client, agent_write_flow(connection))
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(run_id)
    assert sent == [X] and run['status'] == 'failed'
    assert not run.get('agent_frames') and run['write_nodes'] == ['agent:1:post'], run
    assert client.post(f'/api/runs/{run_id}/resume').status_code == 409


@pytest.mark.asyncio
async def test_resume_does_not_ask_the_model_for_the_settled_step(client, monkeypatch):
    store, worker, run_id, sent = await first_attempt(client, monkeypatch)
    prompts = faithful(monkeypatch, [call('post', ''), call('post', X), final('done')])
    run = await resume(client, store, worker, run_id)
    assert run['status'] == 'success' and sent == [X]
    assert ACCEPTED in prompts[0], 'the first resumed model call must already see the settled result'
    assert len(prompts) == 1


def specialist_flow(connection_id):
    """Input -> Agent -> specialist Agent (with the HTTP POST tool) ; Agent -> Response."""
    from backend.app.models import Workflow
    return Workflow.model_validate({'version': 1, 'name': 'Specialist write', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': {'provider': 'demo'}},
        {'id': 'spec', 'type': 'agent', 'config': {'provider': 'demo'}},
        {'id': 'post', 'type': 'tool_http', 'config': {'method': 'POST', 'enable_writes': True, 'approval': False,
                                                         'connection_id': connection_id, 'body': '{input}'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'agent.text'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'agent'}, {'id': 'b', 'source': 'agent', 'target': 'out'},
                  {'id': 's', 'source': 'agent', 'target': 'spec', 'kind': 'agent', 'sourceHandle': 'agents', 'targetHandle': 'input'},
                  {'id': 't', 'source': 'post', 'target': 'spec', 'kind': 'tool', 'targetHandle': 'tools'}]})


@pytest.mark.asyncio
async def test_a_specialist_write_saves_a_frame_for_each_agent_on_the_path(client, monkeypatch):
    store, worker, connection, sent = setup(client, monkeypatch)
    failed = []

    def attempt1(prompt):
        if '"post"' in prompt:  # The specialist: its attached target is the tool.
            return final('posted') if ACCEPTED in prompt else call('post', X)
        if 'tool_result' in prompt and not failed:  # The outer Agent, after the specialist returned.
            failed.append(1)
            return ValueError('Model provider is unavailable.')
        return call('spec', 'post the ticket')
    model_by_prompt(monkeypatch, attempt1)
    run_id = start(client, specialist_flow(connection))
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(run_id)
    assert sent == [X] and run['status'] == 'failed', run
    assert set(run['agent_frames']) == {'agent:0:agent', 'agent:1:spec'}, run['agent_frames']

    outer = iter([call('spec', ''), call('spec', 'post the ticket')])

    def attempt2(prompt):
        if '"post"' in prompt:
            return final('posted') if ACCEPTED in prompt else call('post', X)
        return final('done') if 'posted' in prompt else next(outer)
    model_by_prompt(monkeypatch, attempt2)
    run = await resume(client, store, worker, run_id)
    assert run['status'] == 'success' and sent == [X], sent


def loop_agent_flow(jira_id, http_id):
    """jira search -> for each issue -> Agent (with the HTTP POST tool) -> response; stop on a failed item."""
    from backend.app.models import Workflow
    return Workflow.model_validate({'version': 1, 'name': 'Loop agent write', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': jira_id, 'operation': 'search', 'jql': 'a=b'}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'},
         'config': {'body': 'agent', 'max_items': 10, 'on_item_error': 'stop'}},
        {'id': 'agent', 'type': 'agent', 'config': {'provider': 'demo'}},
        {'id': 'post', 'type': 'tool_http', 'config': {'method': 'POST', 'enable_writes': True, 'approval': False,
                                                         'connection_id': http_id, 'body': '{input}'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'tickets'},
                  {'id': 'b', 'source': 'tickets', 'target': 'each'},
                  {'id': 'c', 'source': 'each', 'target': 'out'},
                  {'id': 'd', 'source': 'each', 'target': 'agent', 'kind': 'loop', 'sourceHandle': 'body', 'targetHandle': 'input'},
                  {'id': 't', 'source': 'post', 'target': 'agent', 'kind': 'tool', 'targetHandle': 'tools'}]})


@pytest.mark.asyncio
async def test_a_loop_item_agent_write_resumes_inside_the_item(client, monkeypatch):
    from backend.tests.test_execution_safety import jira_payload
    store, worker, http_id, sent = setup(client, monkeypatch)
    jira = client.post('/api/connections', json={'name': 'Jira', 'provider': 'jira', 'endpoint': 'https://jira.example.com',
                                                 'username': 'bot', 'secret': 't'}).json()['id']

    async def prepared(_p, tenant_id='local'):
        return jira_payload(2)
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)

    def item(prompt):
        return 'B' if 'PROJ-1' in prompt else 'A'
    failed = []

    def attempt1(prompt):
        if ACCEPTED in prompt:
            if item(prompt) == 'B' and not failed:
                failed.append(1)
                # The run stops after item B's write settled (a control-flow error the item
                # cannot record as its own failure).
                from backend.app.accounting_state import AccountingUnavailable
                return AccountingUnavailable('Accounting store unavailable.')
            return final('ok')
        return call('post', item(prompt))
    model_by_prompt(monkeypatch, attempt1)
    run_id = start(client, loop_agent_flow(jira, http_id))
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(run_id)
    assert sent == ['A', 'B'] and run['status'] == 'failed', (sent, run['status'], run.get('error'))
    frames = run.get('agent_frames', {})
    assert '0' in run['loop_progress']['each'] and 'each:0:0:agent' not in frames, 'a finished item keeps no frame'
    assert frames['each:1:0:agent']['invocation'] == 'each:1:1:post', frames

    restart = iter([call('post', ''), call('post', 'B')])
    model_by_prompt(monkeypatch, lambda p: final('ok') if ACCEPTED in p else next(restart))
    run = await resume(client, store, worker, run_id)
    assert run['status'] == 'success' and sent == ['A', 'B'], (sent, run.get('error'))


@pytest.mark.asyncio
async def test_an_approved_write_saves_frames_at_complete(client, monkeypatch):
    """Approval path: after complete, a failure before the Agent checkpoint, then resume: no
    second approval and no second request. (The pause already saves the frame, so this is a
    guard for that path, not a new failure.)"""
    from backend.app.approvals import decide
    store, worker, connection, sent = setup(client, monkeypatch)
    workflow = agent_write_flow(connection)
    workflow.nodes[2].config['approval'] = True
    failed = []

    async def approved_receiver(prepared, tenant):
        sent.append(prepared['payload'].get('body') if isinstance(prepared.get('payload'), dict) else prepared)
        return 'accepted'
    monkeypatch.setattr(worker.tools, 'execute_prepared', approved_receiver)

    def reply(prompt):
        if ACCEPTED in prompt:
            if not failed:
                failed.append(1)
                return ValueError('Model provider is unavailable.')
            return final('done')
        return call('post', X)
    model_by_prompt(monkeypatch, reply)
    run_id = start(client, workflow)
    await worker.execute(store.claim_next(worker.owner))
    approval = store.run(run_id)['approvals'][0]
    decide(store, run_id, 'local', approval['id'], approval['digest'], 'approve')
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(run_id)
    assert len(sent) == 1 and run['status'] == 'failed', (sent, run['status'], run.get('error'))
    assert run['agent_frames']['agent:0:agent']['step'] == 0
    run = await resume(client, store, worker, run_id)
    assert run['status'] == 'success' and len(sent) == 1 and len(run['approvals']) == 1, (sent, run['status'], run.get('error'))


# --------------------------------------------------------------------------- Part B: DEF-02

@pytest.mark.asyncio
async def test_def02_a_stored_write_result_is_not_returned_for_a_different_request(client, monkeypatch):
    store, worker, run_id, sent = await first_attempt(client, monkeypatch)
    script_model(monkeypatch, [call('post', Y), final('done')])
    run = await resume(client, store, worker, run_id)
    reported_success = any(e.get('node_id') == 'post' and e.get('status') == 'success' for e in run['events'][-6:])
    assert Y in sent or run['status'] == 'failed', (
        f'DEF-02: run {run["status"]}, tool success reported={reported_success}, but the receiver got only {sent}')


def drop_frames(store, run_id):
    """Force the state an older build leaves: a settled result with no saved frame."""
    from backend.app.storage import RunRecord
    with Session(store.engine) as s:
        row = s.get(RunRecord, run_id)
        row.data = {k: v for k, v in row.data.items() if k != 'agent_frames'}
        s.commit()


@pytest.mark.asyncio
async def test_a_different_request_at_the_same_invocation_stops_the_run(client, monkeypatch):
    store, worker, run_id, sent = await first_attempt(client, monkeypatch)
    drop_frames(store, run_id)
    prompts = model_by_prompt(monkeypatch, lambda p: final('done') if 'tool_error' in p or ACCEPTED in p else call('post', Y))
    run = await resume(client, store, worker, run_id)
    assert sent == [X], sent
    assert run['status'] == 'failed', run['status']
    assert 'differs from the request that was already sent' in run['error'], run['error']
    assert len(prompts) == 1, 'the Agent must not continue after the mismatch'


@pytest.mark.asyncio
async def test_an_old_result_row_without_a_digest_is_served_as_before(client, monkeypatch):
    store, worker, run_id, sent = await first_attempt(client, monkeypatch)
    drop_frames(store, run_id)
    with store.engine.begin() as connection:
        connection.execute(text('UPDATE run_action_results SET payload_sha256 = NULL WHERE run_id = :id'), {'id': run_id})
    script_model(monkeypatch, [call('post', Y), final('done')])
    run = await resume(client, store, worker, run_id)
    assert run['status'] == 'success' and sent == [X], (run['status'], sent)
