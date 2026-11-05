"""Independent TA07 checks; real Store/Worker with synthetic HTTP only."""
import asyncio

import httpx
import pytest

from backend.app import providers
from backend.tests.test_cache_input_tokens import claude, openai, cache_of
from backend.tests.test_usage_status import counted, durable, expect, flow, http, outcome, run
from backend.tests.test_usage_attribution import call, hold, team
from backend.tests.test_accounting_durability import reopen


def test_claude_completeness_truth_table():
    # Inputs and expected results are literal contract cases, including known zero,
    # missing base/output, malformed containers and a known partial cache contribution.
    rows = [
        ({}, {}, 'unreported'),
        ({'output_tokens': 0}, {'completion_tokens': 0}, 'partial'),
        ({'input_tokens': 0, 'output_tokens': 0}, {'prompt_tokens': 0, 'completion_tokens': 0}, 'reported'),
        ({'input_tokens': None, 'cache_read_input_tokens': 0}, {'prompt_tokens': 0}, 'partial'),
        ({'input_tokens': True, 'cache_read_input_tokens': 6, 'cache_creation_input_tokens': 4, 'output_tokens': 0},
         {'prompt_tokens': 10, 'completion_tokens': 0, 'cache_read_tokens': 6, 'cache_write_tokens': 4}, 'partial'),
        ({'input_tokens': 7, 'cache_read_input_tokens': True, 'cache_creation_input_tokens': 4, 'output_tokens': 5},
         {'prompt_tokens': 11, 'completion_tokens': 5, 'cache_write_tokens': 4}, 'partial'),
        ({'input_tokens': 7, 'cache_read_input_tokens': -1, 'cache_creation_input_tokens': 4, 'output_tokens': 5},
         {'prompt_tokens': 11, 'completion_tokens': 5, 'cache_write_tokens': 4}, 'partial'),
        ({'input_tokens': 7, 'cache_read_input_tokens': {}, 'cache_creation_input_tokens': 4, 'output_tokens': 5},
         {'prompt_tokens': 11, 'completion_tokens': 5, 'cache_write_tokens': 4}, 'partial'),
        ({'input_tokens': 7, 'cache_read_input_tokens': None, 'cache_creation_input_tokens': None, 'output_tokens': 0},
         {'prompt_tokens': 7, 'completion_tokens': 0}, 'reported'),
        ({'input_tokens': 7, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0},
         {'prompt_tokens': 7}, 'partial'),
        ({'cache_read_input_tokens': [], 'cache_creation_input_tokens': {}}, {}, 'unreported'),
        ({'input_tokens': 1, 'cache_creation_input_tokens': 4, 'cache_creation':
          {'ephemeral_5m_input_tokens': 2, 'ephemeral_1h_input_tokens': 2}, 'output_tokens': 0},
         {'prompt_tokens': 5, 'completion_tokens': 0, 'cache_write_tokens': 4}, 'reported'),
    ]
    for raw, wanted, status in rows:
        total, parts, complete = providers._claude_input(raw)
        usage = providers.CallUsage()
        providers.record_usage(usage, total, raw.get('output_tokens'), parts, complete)
        assert dict(usage) == wanted, raw
        assert usage.status() == status, raw


def test_inclusive_total_detail_boundaries():
    rows = [
        ((10, 6, 4), {'cache_read_tokens': 6, 'cache_write_tokens': 4}),
        ((9, 6, 4), {}),
        ((0, 0, 0), {}),
        ((None, 6, 4), {}),
        ((True, 0, 0), {}),
        ((100, True, 20), {}),
        ((100, 60, None), {'cache_read_tokens': 60}),
        ((100, None, 20), {'cache_write_tokens': 20}),
        ((100, [], 20), {}),
        ((100, {}, 20), {}),
        ((100, -1, 20), {}),
        ((100, '60', 20), {}),
    ]
    for args, expected in rows:
        assert providers._cache_parts(*args) == expected, args


@pytest.mark.asyncio
@pytest.mark.parametrize('with_output', [True, False], ids=['complete', 'missing-output'])
async def test_rejected_answer_keeps_cache_usage(tmp_path, monkeypatch, with_output):
    body = claude(input_tokens=10, cache_read_input_tokens=600, cache_creation_input_tokens=400,
                  **({'output_tokens': 5} if with_output else {}))
    body['content'] = []
    http(monkeypatch, [body])
    store, worker, credential = durable(tmp_path, 'claude')
    try:
        result = await run(store, worker, flow('claude', credential))
        assert result['status'] == 'failed' and 'no text' in result['error']
        assert counted(store, result['id']) == expect(calls=1, partial=0 if with_output else 1,
                                                     tokens=(1010, 5 if with_output else 0))
        assert cache_of(outcome(result)['usage']) == {'cache_read_tokens': 600, 'cache_write_tokens': 400}
        assert result['accounting']['tokens_spent'] == (1015 if with_output else 1010)
    finally:
        store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['malformed-container', 'exact-boundary', 'null-read'])
async def test_openai_optional_details_preserve_authoritative_total(tmp_path, monkeypatch, case):
    details, expected = {
        'malformed-container': (['cached_tokens'], {}),
        'exact-boundary': ({'cached_tokens': 60, 'cache_write_tokens': 40}, {'cache_read_tokens': 60, 'cache_write_tokens': 40}),
        'null-read': ({'cached_tokens': None, 'cache_write_tokens': 20}, {'cache_write_tokens': 20}),
    }[case]
    http(monkeypatch, [openai(details)])
    store, worker, credential = durable(tmp_path, 'openai')
    try:
        result = await run(store, worker, flow('openai', credential))
        assert result['status'] == 'success'
        assert counted(store, result['id']) == expect(calls=1, tokens=(100, 50))
        assert cache_of(outcome(result)['usage']) == expected
    finally:
        store.engine.dispose()


@pytest.mark.asyncio
async def test_nested_cache_attribution_on_cancellation(tmp_path, monkeypatch):
    first = claude(input_tokens=1, cache_read_input_tokens=10, output_tokens=1)
    second = claude(cache_read_input_tokens=20, cache_creation_input_tokens=3, output_tokens=2)
    first['content'][0]['text'] = call('helper')
    second['content'][0]['text'] = call('grand')
    http(monkeypatch, [first, second, asyncio.CancelledError()])
    store, worker, credential = durable(tmp_path, 'claude')
    document = team(depth=2)
    for node in document['nodes']:
        if node['type'] == 'agent':
            node['config'] = {'provider': 'claude', 'model': 'claude-fixture', 'credential_id': credential}
    try:
        result = await run(store, worker, document)
        assert counted(store, result['id']) == expect(calls=2, partial=1, unanswered=1, tokens=(34, 3))
        assert cache_of(outcome(result, 'agent')['usage']) == {'cache_read_tokens': 10}
        assert cache_of(outcome(result, 'helper')['usage']) == {'cache_read_tokens': 20, 'cache_write_tokens': 3}
        assert cache_of(outcome(result, 'grand')['usage']) == {}
        assert cache_of(result['accounting']['usage']['agent']) == {'cache_read_tokens': 30, 'cache_write_tokens': 3}
        assert result['accounting']['tokens_spent'] == 37
    finally:
        store.engine.dispose()


@pytest.mark.asyncio
async def test_resume_reuses_paid_cache_usage_without_recounting(tmp_path, monkeypatch):
    replies = [claude(input_tokens=10, cache_read_input_tokens=600, cache_creation_input_tokens=400, output_tokens=5),
               claude(input_tokens=1, cache_read_input_tokens=1, output_tokens=1)]
    seen, active = [], []
    async def transport(request):
        seen.append(request.url.path)
        active.append(asyncio.current_task())
        return httpx.Response(200, json=replies.pop(0))
    monkeypatch.setattr(providers, 'client', lambda timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(transport), timeout=timeout, trust_env=False))
    store, worker, credential = durable(tmp_path, 'claude')
    committed, release = hold(monkeypatch, store, lambda e: e.get('node_id') == 'm1' and e.get('status') == 'success')
    row = store.create_run(flow('claude', credential, count=2), 'go')
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
        assert counted(store, row['id']) == expect(calls=2, tokens=(1012, 6))
        assert result['accounting']['tokens_spent'] == 1018
        assert cache_of(outcome(result, 'm2')['usage']) == {'cache_read_tokens': 1}
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        store.engine.dispose()
