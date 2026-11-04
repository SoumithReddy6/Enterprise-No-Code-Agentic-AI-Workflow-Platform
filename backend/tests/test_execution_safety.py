"""Batch 1: approval pauses, uncertain writes, and item-level write recovery.

Every test here reproduces a defect found in the A3/A4 audit. Each one failed before
the fix and pins the property that must never regress.
"""
import asyncio
import json
import pytest
from cryptography.fernet import Fernet
from backend.app.models import Workflow
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.app.tool_service import UncertainWriteError


@pytest.fixture
def store(tmp_path):
    return Store(f'sqlite:///{tmp_path}/safety.db', Fernet.generate_key())


@pytest.fixture
def worker(store, monkeypatch):
    from backend.app import tool_service
    monkeypatch.setattr(tool_service, 'public_addresses', lambda host, port: ['93.184.216.34'])
    return Worker(store)


def http_connection(store):
    from backend.app.tool_service import ToolService
    return ToolService(store).save_connection(
        {'name': 'API', 'provider': 'http', 'endpoint': 'https://api.example.com',
         'secret': 'k'}, 'local')['id']


def jira_connection(store):
    from backend.app.tool_service import ToolService
    return ToolService(store).save_connection(
        {'name': 'Jira', 'provider': 'jira', 'endpoint': 'https://jira.example.com',
         'username': 'bot', 'secret': 't'}, 'local')['id']


def jira_payload(n):
    return json.dumps({'issues': [{'key': f'PROJ-{i}', 'fields': {
        'summary': f'Issue {i}', 'status': {'name': 'Open'},
        'assignee': {'displayName': 'Ana'}, 'updated': '2026-09-01T00:00:00Z'}} for i in range(n)]})


def batch_write_flow(jira_id, http_id, on_item_error='continue'):
    """jira search -> for each issue -> POST (a write) -> response."""
    return Workflow.model_validate({'version': 1, 'name': 'Batch write', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': jira_id, 'operation': 'search', 'jql': 'a=b'}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'},
         'config': {'body': 'send', 'max_items': 10, 'on_item_error': on_item_error}},
        {'id': 'send', 'type': 'tool_http',
         'config': {'connection_id': http_id, 'method': 'POST', 'enable_writes': True}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'tickets'},
                  {'id': 'b', 'source': 'tickets', 'target': 'each'},
                  {'id': 'c', 'source': 'each', 'target': 'out'},
                  {'id': 'd', 'source': 'each', 'target': 'send', 'kind': 'loop',
                   'sourceHandle': 'body', 'targetHandle': 'input'}]})


# --------------------------------------------------------------------------- F01

@pytest.mark.asyncio
async def test_loop_pauses_for_approval_instead_of_recording_a_failed_item(store, worker, monkeypatch):
    """A write inside a loop must raise the approval gate, not be swallowed as an item failure."""
    async def prepared(_p, tenant_id='local'): return jira_payload(2)
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    workflow = batch_write_flow(jira_connection(store), http_connection(store))
    row = store.create_run(workflow.model_dump(mode='json'), 'go', approval_required=True)
    await worker.execute(store.claim_next(worker.owner))

    saved = store.run(row['id'])
    assert saved['status'] == 'awaiting_approval', f"run finished as {saved['status']} without approval"
    assert saved.get('approvals'), 'no approval was recorded'
    assert not [r for r in saved.get('loop_progress', {}).get('each', {}).values()
                if r['status'] == 'failed'], 'a pause must not be recorded as an item failure'


# --------------------------------------------------------------------------- F02

@pytest.mark.asyncio
async def test_uncertain_write_is_not_recovered_by_on_error_continue(store, worker, monkeypatch):
    """on_error must not carry a run past an external outcome the platform cannot determine."""
    async def boom(*a, **k): raise RuntimeError('connection reset mid-write')
    monkeypatch.setattr(worker.tools, 'execute', boom)
    http_id = http_connection(store)
    workflow = Workflow.model_validate({'version': 1, 'name': 'Continue write', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'send', 'type': 'tool_http', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': http_id, 'method': 'POST', 'enable_writes': True,
                    'approval': False},
         'on_error': 'continue'},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'send'},
                  {'id': 'b', 'source': 'send', 'target': 'out'}]})
    row = store.create_run(workflow.model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))

    saved = store.run(row['id'])
    assert saved['status'] == 'failed', 'an uncertain write must stop the run'
    assert 'uncertain' in saved['error'].lower()


@pytest.mark.asyncio
async def test_uncertain_write_is_not_recovered_by_on_error_route(store, worker, monkeypatch):
    async def boom(*a, **k): raise RuntimeError('connection reset mid-write')
    monkeypatch.setattr(worker.tools, 'execute', boom)
    http_id = http_connection(store)
    workflow = Workflow.model_validate({'version': 1, 'name': 'Route write', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'send', 'type': 'tool_http', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': http_id, 'method': 'POST', 'enable_writes': True,
                    'approval': False},
         'on_error': 'route'},
        {'id': 'ok', 'type': 'response', 'inputs': {'text': 'input.message'}},
        {'id': 'fallback', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'send'},
                  {'id': 'b', 'source': 'send', 'target': 'ok'},
                  {'id': 'c', 'source': 'send', 'target': 'fallback', 'sourceHandle': 'error'}]})
    row = store.create_run(workflow.model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    saved = store.run(row['id'])
    assert saved['status'] == 'failed'
    assert 'uncertain' in saved['error'].lower()


# --------------------------------------------------------------------------- F12

@pytest.mark.asyncio
async def test_write_identity_is_per_action_not_per_loop(store, worker, monkeypatch):
    """A finished write settles the moment it returns. Only the action whose outcome is
    genuinely unknown blocks resume - previously the whole batch was held by one
    loop checkpoint, and then by one item."""
    async def prepared(_p, tenant_id='local'): return jira_payload(3)
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    calls = {'n': 0}
    async def send(node_type, settings, text, tenant_id='local'):
        calls['n'] += 1
        if calls['n'] == 2: raise asyncio.CancelledError()
        return 'sent'
    monkeypatch.setattr(worker.tools, 'execute', send)
    workflow = batch_write_flow(jira_connection(store), http_connection(store))
    row = store.create_run(workflow.model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))

    saved = store.run(row['id'])
    # Item 0's write returned, so its marker is gone. Item 1's was interrupted after
    # mark_write, so it is the only thing left in doubt.
    assert set(saved['write_nodes']) == {'each:1:send'}, saved['write_nodes']
    assert saved['loop_progress']['each']['0']['status'] == 'success'
    assert 'each' not in saved['checkpoints'], 'the loop itself never completed'
    with pytest.raises(ValueError, match='external write'):
        Store.check_resume_writes(saved)


@pytest.mark.asyncio
async def test_batch_is_resumable_once_every_write_has_settled(store, worker, monkeypatch):
    """With no action left in doubt, nothing blocks resume."""
    async def prepared(_p, tenant_id='local'): return jira_payload(2)
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    calls = {'n': 0, 'stop_after': 1}
    async def send(node_type, settings, text, tenant_id='local'):
        calls['n'] += 1
        return 'sent'
    monkeypatch.setattr(worker.tools, 'execute', send)
    workflow = batch_write_flow(jira_connection(store), http_connection(store))
    row = store.create_run(workflow.model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))

    saved = store.run(row['id'])
    assert saved['status'] == 'success'
    assert not saved.get('write_nodes'), f"every completed write must settle: {saved['write_nodes']}"
    Store.check_resume_writes(saved)


# --------------------------------------------------------------------------- B1-01

def agent_loop_flow(jira_id):
    return Workflow.model_validate({'version': 1, 'name': 'Agent batch', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': jira_id, 'operation': 'search', 'jql': 'a=b'}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'},
         'config': {'body': 'worker', 'max_items': 10}},
        {'id': 'worker', 'type': 'agent',
         'config': {'provider': 'ollama', 'model': 'llama3.1:latest', 'role': 'summarizer'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'tickets'},
                  {'id': 'b', 'source': 'tickets', 'target': 'each'},
                  {'id': 'c', 'source': 'each', 'target': 'out'},
                  {'id': 'd', 'source': 'each', 'target': 'worker', 'kind': 'loop',
                   'sourceHandle': 'body', 'targetHandle': 'input'}]})


@pytest.mark.asyncio
async def test_loop_item_model_usage_reaches_durable_events(store, worker, monkeypatch):
    """Usage is keyed by checkpoint_owner, which is now per item. Without per-item
    reporting the loop node's event carries none and token accounting loses every call."""
    from backend.app import registry
    async def prepared(_p, tenant_id='local'): return jira_payload(2)
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)

    async def model(inputs, config, ctx):
        registry.account_usage(ctx, {'prompt_tokens': 11, 'completion_tokens': 7}, config)
        return {'text': json.dumps({'action': 'final', 'text': 'summary'}), 'provider': 'ollama'}
    monkeypatch.setattr(registry, 'llm_node', model)

    store.allow_model('ollama', 'llama3.1:latest', '', 'local')
    workflow = agent_loop_flow(jira_connection(store))
    row = store.create_run(workflow.model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))

    saved = store.run(row['id'])
    assert saved['status'] == 'success', saved.get('error')
    reported = [e for e in saved['events'] if e.get('usage')]
    assert reported, 'no durable event carried model usage'
    total = sum(e['usage']['prompt_tokens'] + e['usage']['completion_tokens'] for e in reported)
    assert total == 2 * 18, f'expected both items accounted once, got {total}'
    assert {e.get('item_index') for e in reported} == {0, 1}, 'usage must be attributed per item'


# --------------------------------------------------------------------------- B1-02

def two_write_agent_flow(jira_id, http_id):
    """One agent with two write tools: the first pre-authorised, the second gated."""
    return Workflow.model_validate({'version': 1, 'name': 'Mixed approval', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': jira_id, 'operation': 'search', 'jql': 'a=b'}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'},
         'config': {'body': 'worker', 'max_items': 5}},
        {'id': 'worker', 'type': 'agent',
         'config': {'provider': 'ollama', 'model': 'llama3.1:latest', 'max_steps': 4}},
        {'id': 'ack', 'type': 'tool_http',
         'config': {'connection_id': http_id, 'method': 'POST', 'enable_writes': True,
                    'approval': False, 'path': '/ack', 'description': 'Acknowledges receipt.'}},
        {'id': 'reply', 'type': 'tool_http',
         'config': {'connection_id': http_id, 'method': 'POST', 'enable_writes': True,
                    'approval': True, 'path': '/reply', 'description': 'Sends the customer reply.'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'tickets'},
                  {'id': 'b', 'source': 'tickets', 'target': 'each'},
                  {'id': 'c', 'source': 'each', 'target': 'out'},
                  {'id': 'd', 'source': 'each', 'target': 'worker', 'kind': 'loop',
                   'sourceHandle': 'body', 'targetHandle': 'input'},
                  {'id': 'e', 'source': 'ack', 'target': 'worker', 'kind': 'tool',
                   'targetHandle': 'tools'},
                  {'id': 'f', 'source': 'reply', 'target': 'worker', 'kind': 'tool',
                   'targetHandle': 'tools'}]})


@pytest.mark.asyncio
async def test_completed_write_settles_so_a_gated_second_write_can_resume(store, worker, monkeypatch):
    """A finished write must settle on completion. Otherwise its stale marker makes the
    approved continuation look like an unresolved external outcome and the run cannot finish."""
    from backend.app import registry
    sent = []
    async def execute(node_type, settings, text, tenant_id='local'):
        sent.append(settings.get('path')); return 'ok'
    monkeypatch.setattr(worker.tools, 'execute', execute)
    async def execute_prepared(prep, tenant_id='local'):
        # Jira reads are GET and take the prepared path too; only POSTs are the writes.
        payload = prep.get('payload', {}) if isinstance(prep, dict) else {}
        if payload.get('method') == 'POST':
            sent.append(payload.get('path', '/reply')); return 'ok'
        return jira_payload(1)
    monkeypatch.setattr(worker.tools, 'execute_prepared', execute_prepared)

    steps = iter([json.dumps({'action': 'call', 'target': 'ack', 'input': 'received'}),
                  json.dumps({'action': 'call', 'target': 'reply', 'input': 'here is your answer'}),
                  json.dumps({'action': 'final', 'text': 'handled'})])
    async def model(inputs, config, ctx):
        return {'text': next(steps), 'provider': 'ollama'}
    monkeypatch.setattr(registry, 'llm_node', model)
    store.allow_model('ollama', 'llama3.1:latest', '', 'local')

    workflow = two_write_agent_flow(jira_connection(store), http_connection(store))
    row = store.create_run(workflow.model_dump(mode='json'), 'go', approval_required=True)
    await worker.execute(store.claim_next(worker.owner))

    paused = store.run(row['id'])
    assert paused['status'] == 'awaiting_approval', paused.get('error')
    assert '/ack' in sent, 'the pre-authorised write should have been sent'
    assert '/reply' not in sent, 'the gated write must wait'
    # The completed first write must not leave the item looking uncertain.
    assert not paused.get('write_nodes'), f"stale write marker: {paused['write_nodes']}"

    approval = paused['approvals'][0]
    from backend.app import approvals as approvals_module
    approvals_module.decide(store, row['id'], 'local', approval['id'], approval['digest'], 'approve')
    await worker.execute(store.claim_next(worker.owner))

    finished = store.run(row['id'])
    assert finished['status'] == 'success', finished.get('error')
    assert sent.count('/ack') == 1, f'the settled write must not repeat: {sent}'
    assert '/reply' in sent


# --------------------------------------------------------------------------- B1-04

@pytest.mark.asyncio
async def test_settled_write_is_not_replayed_when_the_worker_dies_before_checkpoint(store, worker, monkeypatch):
    """Clearing a write marker must durably record the completed action's result.

    Otherwise there is a window between settlement and the item checkpoint where resume
    is permitted but nothing records that the call already happened - and it is sent twice.
    """
    async def execute_prepared(prep, tenant_id='local'): return jira_payload(1)
    monkeypatch.setattr(worker.tools, 'execute_prepared', execute_prepared)
    delivered = []
    async def execute(node_type, settings, text, tenant_id='local'):
        delivered.append(text); return 'ok'
    monkeypatch.setattr(worker.tools, 'execute', execute)

    # Die in the window: after settlement commits, before the item result persists.
    original = store.settle_write
    def settle_then_die(run_id, owner, identifier, result=None):
        original(run_id, owner, identifier, result)   # commits settlement
        raise asyncio.CancelledError()                # ...then the worker dies
    monkeypatch.setattr(store, 'settle_write', settle_then_die)

    workflow = batch_write_flow(jira_connection(store), http_connection(store))
    row = store.create_run(workflow.model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))

    interrupted = store.run(row['id'])
    assert len(delivered) == 1, 'setup: exactly one delivery before the interruption'
    assert not interrupted.get('write_nodes'), 'the write settled'
    assert store.completed_action(row['id'], 'each:0:send') == 'ok', \
        'settlement must record the outcome in the same transaction'

    monkeypatch.setattr(store, 'settle_write', original)
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))

    assert store.run(row['id'])['status'] == 'success'
    assert len(delivered) == 1, f'the settled write was sent again: {delivered}'


def test_settlement_requires_a_recorded_outcome(store):
    """A marker must never clear without an outcome beside it, by construction."""
    row = store.create_run({'version': 1, 'name': 'x', 'nodes': [], 'edges': []}, 'go')
    for bad in (None, 0, b'ok', {'text': 'ok'}):
        with pytest.raises(ValueError, match='outcome as text'):
            store.settle_write(row['id'], 'anyone', 'n:0:send', bad)


# --------------------------------------------------------------------------- replay accounting

@pytest.mark.asyncio
async def test_replaying_a_settled_write_does_not_charge_it_again(store, worker, monkeypatch):
    """The write ran once and was settled. A resume that replays its stored result must
    neither send it again nor debit the budget for an action already reserved."""
    monkeypatch.setenv('AGENT_RUN_BUDGET', '5')
    async def execute_prepared(prep, tenant_id='local'): return jira_payload(1)
    monkeypatch.setattr(worker.tools, 'execute_prepared', execute_prepared)
    delivered = []
    async def execute(node_type, settings, text, tenant_id='local'):
        delivered.append(text); return 'ok'
    monkeypatch.setattr(worker.tools, 'execute', execute)
    original = store.settle_write
    def settle_then_die(run_id, owner, identifier, result):
        original(run_id, owner, identifier, result)
        raise asyncio.CancelledError()
    monkeypatch.setattr(store, 'settle_write', settle_then_die)

    workflow = batch_write_flow(jira_connection(store), http_connection(store))
    row = store.create_run(workflow.model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    before = store.run(row['id'])['accounting']['action_budget']['counter']

    monkeypatch.setattr(store, 'settle_write', original)
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    after = store.run(row['id'])

    assert after['status'] == 'success'
    assert len(delivered) == 1, 'the settled write was sent again'
    assert after['accounting']['action_budget']['counter'] == before, \
        f"replay charged an already-reserved action: {before} -> {after['accounting']['action_budget']['counter']}"
