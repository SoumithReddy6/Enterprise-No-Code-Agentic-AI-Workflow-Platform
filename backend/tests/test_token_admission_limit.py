"""TA05: the run token limit is an admission limit, and a run that ends above it says so.

Before each model call, the call is refused once provider-reported spend has reached the
limit. A call that is let through is not aborted by the limit, so a run can end above it.
Every terminal writer (worker finish, cancellation, approval rejection or expiry) then
records token_limit {policy, limit, spent, over} when the durable spend is above a non-zero
limit, and removes any record from an earlier attempt.

Store and Worker are real. Only HTTP is synthetic, through the TA06 helpers.
"""
import asyncio

import httpx
import pytest

from backend.app import approvals, providers
from backend.tests.test_accounting_durability import claimed, pause_request, snapshot
from backend.tests.test_usage_status import answer, durable, flow, http, outcome, run


def record(limit, spent):
    return {'policy': 'admission', 'limit': limit, 'spent': spent, 'over': spent - limit}


async def sequential(tmp_path, monkeypatch, limit, counts, nodes):
    """Ollama calls with these prompt counts through `nodes` model nodes; returns the store, the run and the requests seen."""
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', str(limit))
    seen = http(monkeypatch, [answer('ollama', count, 0) for count in counts])
    store, worker, _ = durable(tmp_path)
    try:
        return store, worker, await run(store, worker, flow(count=nodes)), seen
    except BaseException:
        store.engine.dispose(); raise


# --------------------------------------------------------------------------- L01: original reproduction

@pytest.mark.asyncio
async def test_l01_final_call_over_the_limit_keeps_success_and_is_recorded(tmp_path, monkeypatch):
    """The finding: limit 100, calls of 80 and 30. No work was refused, so the run is not truncated."""
    store, _, result, seen = await sequential(tmp_path, monkeypatch, 100, [80, 30], 2)
    try:
        assert len(seen) == 2 and result['status'] == 'success' and result['truncated'] is False
        assert result['accounting']['tokens_spent'] == 110
        assert result.get('token_limit') == record(100, 110)
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- L02: refusal, then resume

@pytest.mark.asyncio
async def test_l02_refused_call_records_overshoot_and_resume_clears_it(tmp_path, monkeypatch):
    store, worker, result, seen = await sequential(tmp_path, monkeypatch, 100, [80, 30, 10], 3)
    try:
        assert len(seen) == 2, 'm3 is refused before a provider request'
        refused = outcome(result, 'm3')
        assert refused['status'] == 'failed' and refused.get('budget_exhausted') is True
        assert result['status'] == 'failed' and result.get('token_limit') == record(100, 110)
        # TA05-D02: under a raised limit the resumed run ends at 120, below it; no earlier record survives.
        monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '200')
        queued = store.resume_run(result['id'])
        assert queued['status'] == 'queued' and 'token_limit' not in queued
        await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 30)
        resumed = store.run(result['id'])
        assert len(seen) == 3 and resumed['status'] == 'success'
        assert resumed['accounting']['tokens_spent'] == 120
        assert 'token_limit' not in resumed
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- L03-L05: controls

@pytest.mark.asyncio
async def test_l03_exactly_at_the_limit_refuses_and_records_nothing(tmp_path, monkeypatch):
    store, _, result, seen = await sequential(tmp_path, monkeypatch, 100, [60, 40, 10], 3)
    try:
        assert len(seen) == 2 and outcome(result, 'm3').get('budget_exhausted') is True
        assert result['accounting']['tokens_spent'] == 100 and 'token_limit' not in result
    finally: store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [0, 200], ids=['unlimited', 'under'])
async def test_l04_no_overshoot_records_nothing(tmp_path, monkeypatch, limit):
    store, _, result, seen = await sequential(tmp_path, monkeypatch, limit, [80, 30], 2)
    try:
        assert len(seen) == 2 and result['status'] == 'success'
        assert result['accounting']['tokens_spent'] == 110 and 'token_limit' not in result
    finally: store.engine.dispose()


@pytest.mark.asyncio
async def test_l05_invalid_limit_still_reaches_a_terminal_state(tmp_path, monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', 'lots')
    seen = http(monkeypatch, [answer('ollama', 80, 0)])
    store, worker, _ = durable(tmp_path)
    try:
        result = await run(store, worker, flow(count=2))
        assert seen == [] and result['status'] == 'failed'
        assert 'RELAY_RUN_TOKEN_LIMIT must be an integer' in result['error']
        assert 'token_limit' not in result
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- L06-L08: every terminal writer

@pytest.mark.asyncio
async def test_l06_cancellation_after_the_crossing_call_is_recorded(tmp_path, monkeypatch):
    """The fixture requests cancellation while it returns the crossing response; finish_run then cancels."""
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
    store, worker, _ = durable(tmp_path)
    try:
        holder, replies = {}, [answer('ollama', 80, 0), answer('ollama', 30, 0)]
        async def handle(request):
            body = replies.pop(0)
            if not replies: store.request_cancel(holder['id'])
            return httpx.Response(200, json=body)
        transport = httpx.MockTransport(handle)
        monkeypatch.setattr(providers, 'client', lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False))
        holder['id'] = store.create_run(flow(count=2), 'go')['id']
        await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 30)
        result = store.run(holder['id'])
        assert result['status'] == 'cancelled' and result['accounting']['tokens_spent'] == 110
        assert result.get('token_limit') == record(100, 110)
    finally: store.engine.dispose()


def test_l07_queued_cancellation_of_paid_work_is_recorded(tmp_path, monkeypatch):
    """request_cancel finishes a queued run itself, without finish_run (TA05-D01)."""
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
    store, id, owner = claimed(tmp_path)
    try:
        assert store.record_accounting(id, owner, snapshot(110)) is True
        store.release(id, owner)
        assert store.request_cancel(id) == 'cancelled'
        result = store.run(id)
        assert result['status'] == 'cancelled' and result['accounting']['tokens_spent'] == 110
        assert result.get('token_limit') == record(100, 110)
    finally: store.engine.dispose()


def test_l08_rejected_approval_of_an_over_limit_run_is_recorded(tmp_path, monkeypatch):
    """approvals._close finishes a paused run itself, without finish_run (TA05-D01)."""
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
    store, id, owner = claimed(tmp_path)
    try:
        assert approvals.pause(store, id, owner, pause_request(), accounting=snapshot(110)) is True
        pending = store.run(id)['approvals'][0]
        approvals.decide(store, id, 'local', pending['id'], pending['digest'], 'reject')
        result = store.run(id)
        assert result['status'] == 'cancelled' and 'No external request was sent' in result['error']
        assert result['accounting']['tokens_spent'] == 110
        assert result.get('token_limit') == record(100, 110)
    finally: store.engine.dispose()
