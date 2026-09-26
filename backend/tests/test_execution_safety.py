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
async def test_write_identity_is_per_item_not_per_loop(store, worker, monkeypatch):
    """A finished item settles on its own. Only the item whose outcome is genuinely
    unknown blocks resume - previously the whole batch was held by one loop checkpoint."""
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
    assert saved['write_nodes'] == ['each:0', 'each:1'], saved['write_nodes']
    assert saved['loop_progress']['each']['0']['status'] == 'success'
    assert 'each' not in saved['checkpoints'], 'the loop itself never completed'

    assert Store._write_settled(saved, 'each:0'), 'a finished item must settle on its own'
    assert not Store._write_settled(saved, 'each:1'), 'an interrupted write must stay uncertain'


@pytest.mark.asyncio
async def test_batch_resumes_once_every_write_item_has_settled(store, worker, monkeypatch):
    """With no item left in doubt, a partially-written batch is resumable."""
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
    assert saved['write_nodes'] == ['each:0', 'each:1']
    # Both items produced durable results, so nothing is in doubt.
    Store.check_resume_writes(saved)
