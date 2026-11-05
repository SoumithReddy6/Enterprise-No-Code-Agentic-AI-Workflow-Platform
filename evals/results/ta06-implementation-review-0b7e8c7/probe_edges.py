"""Independent TA06 implementation checks. Real Store/Worker; no paid model calls.

Run with --import-mode=importlib; no builder evidence is changed. The two URL
rows deliberately pin the no-delivery requirement; patched controls change only
the in-process exception classification, never repository application code.
"""
import asyncio
import json

import httpcore
import httpx
import pytest

from backend.app import providers
from backend.app import observability
from backend.tests.test_usage_status import (
    answer, counted, durable, expect, flow, http, outcome, run, status_of,
)
from backend.tests.test_usage_attribution import (
    call, hold, team,
)
from backend.tests.test_accounting_durability import reopen, PENDING
from backend.tests.test_token_budget_recovery import plain_agent


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', [
    'read-then-connect', 'http-error-then-connect', 'pool-timeout',
    'write-timeout', 'read-error', 'read-then-partial',
])
async def test_retry_status_is_logical_not_per_attempt(tmp_path, monkeypatch, scenario):
    replies, expected = {
        'read-then-connect': ([httpx.ReadTimeout, httpx.ConnectError], expect(unanswered=1)),
        'http-error-then-connect': ([httpx.Response(503), httpx.ConnectError], expect(calls=1, unreported=1)),
        'pool-timeout': ([httpx.PoolTimeout] * 3, expect()),
        'write-timeout': ([httpx.WriteTimeout] * 3, expect(unanswered=1)),
        'read-error': ([httpx.ReadError], expect(unanswered=1)),
        'read-then-partial': ([httpx.ReadTimeout, answer('ollama', completion=3)], expect(calls=1, partial=1, tokens=(0, 3))),
    }[scenario]
    seen = http(monkeypatch, replies)
    store, worker, _ = durable(tmp_path)
    try:
        result = await run(store, worker, flow())
        assert len(seen) == len(replies)
        assert counted(store, result['id']) == expected
    finally:
        store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['ollama', 'claude', 'openai'])
async def test_boolean_is_missing_but_zero_is_reported(tmp_path, monkeypatch, provider):
    http(monkeypatch, [answer(provider, prompt=True, completion=0)])
    store, worker, credential = durable(tmp_path, provider)
    try:
        result = await run(store, worker, flow(provider, credential))
        assert result['status'] == 'success'
        assert counted(store, result['id']) == expect(calls=1, partial=1)
    finally:
        store.engine.dispose()


@pytest.mark.asyncio
async def test_nested_statuses_do_not_overlap_on_cancellation(tmp_path, monkeypatch):
    http(monkeypatch, [answer('ollama', text=call('helper')),
                      answer('ollama', completion=5, text=call('grand')),
                      asyncio.CancelledError()])
    store, worker, _ = durable(tmp_path)
    try:
        result = await run(store, worker, team(depth=2))
        assert counted(store, result['id']) == expect(calls=2, partial=1, unreported=1, unanswered=1, tokens=(0, 5))
        assert status_of(outcome(result, 'agent')) == dict(calls=1, partial_calls=0, unreported_calls=1, unanswered_calls=0)
        assert status_of(outcome(result, 'helper')) == dict(calls=1, partial_calls=1, unreported_calls=0, unanswered_calls=0)
        assert status_of(outcome(result, 'grand')) == dict(calls=0, partial_calls=0, unreported_calls=0, unanswered_calls=1)
    finally:
        store.engine.dispose()


@pytest.mark.asyncio
async def test_unreported_usage_still_sets_pending_barrier(tmp_path, monkeypatch):
    seen = http(monkeypatch, [answer('ollama', text='not JSON')])
    store, worker, _ = durable(tmp_path)
    original = store.reserve_accounting
    def refuse_after_call(id, owner, value):
        if any(v.get('calls') for v in value.get('usage', {}).values()):
            return False
        return original(id, owner, value)
    def unavailable(*args, **kwargs):
        raise OSError('synthetic failed fast save')
    monkeypatch.setattr(store, 'record_accounting', unavailable)
    monkeypatch.setattr(store, 'reserve_accounting', refuse_after_call)
    try:
        document = plain_agent('fail', output_schema={'type': 'object', 'required': ['amount'],
            'properties': {'amount': {'type': 'number'}}}).model_dump(mode='json')
        result = await run(store, worker, document)
        assert result['status'] == 'failed' and PENDING in result['error']
        assert len(seen) == 1, 'a zero-known-token call must not bypass the pending-save barrier'
        assert counted(store, result['id']) == expect(calls=1, unreported=1)
    finally:
        store.engine.dispose()


@pytest.mark.asyncio
async def test_checkpointed_unknown_usage_is_not_recounted_on_resume(tmp_path, monkeypatch):
    seen, active = [], []
    replies = [answer('ollama'), answer('ollama', completion=2)]
    async def transport(request):
        seen.append(request.url.path)
        active.append(asyncio.current_task())
        return httpx.Response(200, json=replies.pop(0))
    monkeypatch.setattr(providers, 'client', lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(transport), timeout=timeout, trust_env=False))
    store, worker, _ = durable(tmp_path)
    committed, release = hold(monkeypatch, store, lambda e: e.get('node_id') == 'm1' and e.get('status') == 'success')
    row = store.create_run(flow(count=2), 'go')
    task = asyncio.create_task(worker.execute(store.claim_next(worker.owner)))
    try:
        assert await asyncio.to_thread(committed.wait, 10)
        store.request_cancel(row['id'])
        active[-1].cancel()
        release.set()
        await asyncio.wait_for(task, 15)
        assert 'm1' in store.run(row['id'])['checkpoints'] and len(seen) == 1
        reopen(store, row['id'])
        await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 30)
        result = store.run(row['id'])
        assert result['status'] == 'success' and len(seen) == 2
        assert counted(store, row['id']) == expect(calls=2, partial=1, unreported=1, tokens=(0, 2))
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('url', ['ftp://127.0.0.1:9', 'localhost:11434'])
@pytest.mark.parametrize('patched', [False, True], ids=['current', 'exclude-unsupported-control'])
async def test_unsupported_protocol_cannot_reach_a_provider(tmp_path, monkeypatch, url, patched):
    """HTTPX rejects the protocol before a network connection, even with a real client."""
    network = []
    async def unexpected_connect(*args, **kwargs):
        network.append(True)
        raise AssertionError('this URL must be rejected before any connect')
    monkeypatch.setattr(httpcore.AnyIOBackend, 'connect_tcp', unexpected_connect)
    monkeypatch.setenv('OLLAMA_BASE_URL', url)
    if patched:
        monkeypatch.setattr(providers, 'UNDELIVERED', (*providers.UNDELIVERED, httpx.UnsupportedProtocol))
    store, worker, _ = durable(tmp_path)
    try:
        result = await run(store, worker, flow())
        assert result['status'] == 'failed' and not network
        assert counted(store, result['id']) == expect(), counted(store, result['id'])
    finally:
        store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('patched', [False, True], ids=['current', 'allow-status-control'])
async def test_real_journal_retains_managed_status(tmp_path, monkeypatch, caplog, patched):
    """Capture the actual filtered JSON journal, rather than replacing journal()."""
    if patched:
        monkeypatch.setattr(observability, 'FIELDS', observability.FIELDS | {'usage_status'})
    http(monkeypatch, [answer('ollama')])
    store, worker, _ = durable(tmp_path)
    try:
        result = await run(store, worker, flow())
        assert result['status'] == 'success'
        lines = [json.loads(r.message) for r in caplog.records if r.name == 'relay.journal']
        model = [r for r in lines if r['event'] == 'model.usage']
        assert len(model) == 1
        assert model[0].get('usage_status') == 'unreported', model[0]
        assert 'prompt_tokens' not in model[0] and 'completion_tokens' not in model[0]
    finally:
        store.engine.dispose()
