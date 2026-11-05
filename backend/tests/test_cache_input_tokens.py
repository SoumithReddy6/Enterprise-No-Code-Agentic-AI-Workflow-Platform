"""TA07: Claude cache input counts in the prompt total once, and cache counts are kept as a breakdown.

prompt_tokens means all input processed for the call, on every provider. Claude reports
input in three disjoint parts (input, cache read, cache creation), so its total is their
sum. OpenAI and Ollama already report a total that includes cached input; their cache
counts are details of it. The breakdown (cache_read_tokens, cache_write_tokens) never adds
to a total, to tokens_spent or to SQL rows.

Store and Worker are real. Only HTTP is synthetic, through the TA06 helpers.
"""
import json
import logging

import pytest

from backend.app.operator_metrics import metrics as operator_metrics
from backend.tests.test_usage_status import answer, counted, durable, expect, flow, http, outcome, run, status_of

CACHE = ('cache_read_tokens', 'cache_write_tokens')


def claude(**usage):
    """A Claude Messages body with exactly these usage fields."""
    return answer('claude', usage=usage)


def openai(details='omit', prompt=100, completion=50):
    usage = {'prompt_tokens': prompt, 'completion_tokens': completion}
    if details != 'omit': usage['prompt_tokens_details'] = details
    return answer('openai', usage=usage)


def cache_of(usage):
    return {key: usage[key] for key in CACHE if key in (usage or {})}


async def single(tmp_path, monkeypatch, provider, body):
    """One model call through the real Worker: its run record and its m1 outcome event."""
    http(monkeypatch, [body])
    store, worker, credential = durable(tmp_path, provider)
    try:
        record = await run(store, worker, flow(provider, credential))
        assert record['status'] == 'success'
        return store, record, outcome(record)
    except BaseException:
        store.engine.dispose(); raise


def journal_lines(caplog, event):
    return [line for line in (json.loads(r.getMessage()) for r in caplog.records if r.name == 'relay.journal')
            if line.get('event') == event]


# --------------------------------------------------------------------------- C01: original reproduction

@pytest.mark.asyncio
async def test_c01_claude_cache_input_counts_once_and_reaches_the_ceiling(tmp_path, monkeypatch, caplog):
    """The finding's response: input 10, cache creation 400, cache read 600, output 5.
    It spends 1,015 of a 1,000-token ceiling, so the second model call is refused."""
    caplog.set_level(logging.INFO, logger='relay.journal')
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '1000')
    seen = http(monkeypatch, [claude(input_tokens=10, cache_creation_input_tokens=400,
                                     cache_read_input_tokens=600, output_tokens=5)])
    store, worker, credential = durable(tmp_path, 'claude')
    try:
        record = await run(store, worker, flow('claude', credential, count=2))
        first = outcome(record)
        assert first['status'] == 'success'
        assert (first['usage']['prompt_tokens'], first['usage']['completion_tokens']) == (1010, 5)
        assert status_of(first) == {'calls': 1, 'partial_calls': 0, 'unreported_calls': 0, 'unanswered_calls': 0}
        assert cache_of(first['usage']) == {'cache_read_tokens': 600, 'cache_write_tokens': 400}
        assert counted(store, record['id']) == expect(calls=1, tokens=(1010, 5))
        rows = [r for r in operator_metrics(store)['tokens'] if r['provider'] == 'claude']
        assert rows == [{'provider': 'claude', 'model': 'claude-fixture', 'prompt_tokens': 1010,
                         'completion_tokens': 5, 'calls': 1}], 'the breakdown does not reach SQL'
        accounting = record['accounting']
        assert accounting['tokens_spent'] == 1015, 'the breakdown is not added again'
        by_model = accounting['usage']['m1']['by_model']
        assert [cache_of(r) for r in by_model] == [{'cache_read_tokens': 600, 'cache_write_tokens': 400}]
        usage_lines = journal_lines(caplog, 'model.usage')
        assert [(l.get('prompt_tokens'), l.get('completion_tokens'), l.get('cache_read_tokens'),
                 l.get('cache_write_tokens'), l.get('usage_status')) for l in usage_lines] == [(1010, 5, 600, 400, 'reported')]
        # The ceiling sees the corrected total: m2 is refused by the typed budget error, before any request.
        assert len(seen) == 1, 'the second call must be refused before a provider request'
        refused = outcome(record, 'm2')
        assert refused['status'] == 'failed' and refused.get('budget_exhausted') is True
        assert 'Run token budget exhausted (1,015 of 1,000' in refused['error']
        assert record['status'] == 'failed'
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- C02: no cache tokens

@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['absent', 'null'])
async def test_c02_claude_without_cache_tokens_is_unchanged(tmp_path, monkeypatch, case):
    extra = {} if case == 'absent' else {'cache_read_input_tokens': None, 'cache_creation_input_tokens': None}
    store, record, event = await single(tmp_path, monkeypatch, 'claude', claude(input_tokens=10, output_tokens=5, **extra))
    try:
        assert counted(store, record['id']) == expect(calls=1, tokens=(10, 5))
        assert status_of(event) == {'calls': 1, 'partial_calls': 0, 'unreported_calls': 0, 'unanswered_calls': 0}
        assert cache_of(event['usage']) == {}
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- C03: invalid cache category

@pytest.mark.asyncio
async def test_c03_invalid_claude_cache_field_gives_a_partial_subtotal(tmp_path, monkeypatch):
    body = claude(input_tokens=10, cache_read_input_tokens='600', cache_creation_input_tokens=400, output_tokens=5)
    store, record, event = await single(tmp_path, monkeypatch, 'claude', body)
    try:
        assert counted(store, record['id']) == expect(calls=1, partial=1, tokens=(410, 5))
        assert status_of(event) == {'calls': 1, 'partial_calls': 1, 'unreported_calls': 0, 'unanswered_calls': 0}
        assert cache_of(event['usage']) == {'cache_write_tokens': 400}, 'the invalid category gets no value'
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- C04: OpenAI details

@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['read-write', 'read-only'])
async def test_c04_openai_cache_details_are_parts_of_the_total(tmp_path, monkeypatch, case):
    details = {'cached_tokens': 60, 'cache_write_tokens': 20} if case == 'read-write' else {'cached_tokens': 60}
    store, record, event = await single(tmp_path, monkeypatch, 'openai', openai(details))
    try:
        assert counted(store, record['id']) == expect(calls=1, tokens=(100, 50)), 'no double count'
        assert status_of(event) == {'calls': 1, 'partial_calls': 0, 'unreported_calls': 0, 'unanswered_calls': 0}
        assert record['accounting']['tokens_spent'] == 150
        wanted = {'cache_read_tokens': 60, **({'cache_write_tokens': 20} if case == 'read-write' else {})}
        assert cache_of(event['usage']) == wanted
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- C05: impossible breakdown

@pytest.mark.asyncio
async def test_c05_openai_cache_above_the_total_is_dropped(tmp_path, monkeypatch):
    store, record, event = await single(tmp_path, monkeypatch, 'openai', openai({'cached_tokens': 150}))
    try:
        assert counted(store, record['id']) == expect(calls=1, tokens=(100, 50))
        assert status_of(event) == {'calls': 1, 'partial_calls': 0, 'unreported_calls': 0, 'unanswered_calls': 0}
        assert cache_of(event['usage']) == {}
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- C06: Ollama cached prompt

@pytest.mark.asyncio
async def test_c06_ollama_cached_count_is_part_of_the_prompt(tmp_path, monkeypatch):
    body = answer('ollama', 6024, 12); body['prompt_eval_cached_count'] = 6016
    store, record, event = await single(tmp_path, monkeypatch, 'ollama', body)
    try:
        assert counted(store, record['id']) == expect(calls=1, tokens=(6024, 12))
        assert status_of(event) == {'calls': 1, 'partial_calls': 0, 'unreported_calls': 0, 'unanswered_calls': 0}
        assert cache_of(event['usage']) == {'cache_read_tokens': 6016}
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- C07: journal filter

def test_c07_journal_keeps_only_valid_status_and_cache_counts(caplog):
    """TA06 re-check note: a non-string status is dropped and never raises. Cache counts pass
    only as valid integer counts."""
    from backend.app.observability import journal
    caplog.set_level(logging.INFO, logger='relay.journal')
    for status in (None, ['reported'], {'status': 'reported'}):
        caplog.clear(); journal(event='model.usage', usage_status=status)
        assert 'usage_status' not in json.loads(caplog.records[-1].getMessage()), repr(status)
    caplog.clear(); journal(event='model.usage', usage_status='reported', cache_read_tokens=600, cache_write_tokens=0)
    line = json.loads(caplog.records[-1].getMessage())
    assert (line.get('usage_status'), line.get('cache_read_tokens'), line.get('cache_write_tokens')) == ('reported', 600, 0)
    for bad in ('600', True, -1, 1.5, [600]):
        caplog.clear(); journal(event='model.usage', cache_read_tokens=bad, cache_write_tokens=bad)
        line = json.loads(caplog.records[-1].getMessage())
        assert 'cache_read_tokens' not in line and 'cache_write_tokens' not in line, repr(bad)


# --------------------------------------------------------------------------- C08: empty Claude usage

@pytest.mark.asyncio
async def test_c08_empty_claude_usage_stays_unreported(tmp_path, monkeypatch, caplog):
    """Absent cache defaults are not evidence: they must not make a prompt count of 0 (TA07-D01)."""
    caplog.set_level(logging.INFO, logger='relay.journal')
    store, record, event = await single(tmp_path, monkeypatch, 'claude', claude())
    try:
        assert counted(store, record['id']) == expect(calls=1, unreported=1)
        assert status_of(event) == {'calls': 1, 'partial_calls': 0, 'unreported_calls': 1, 'unanswered_calls': 0}
        assert cache_of(event['usage']) == {}
        # Node totals start at zero; the call's own line shows that no count was recorded.
        assert [sorted(set(l) & {'prompt_tokens', 'completion_tokens', *CACHE}) + [l.get('usage_status')]
                for l in journal_lines(caplog, 'model.usage')] == [['unreported']]
    finally: store.engine.dispose()
