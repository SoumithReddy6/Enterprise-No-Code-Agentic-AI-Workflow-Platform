"""The loop results contract, in serialized UTF-8 bytes of the results list.

* RETAINED_VALUE_BYTES bounds item values: the earliest are kept while the list fits.
* Outcome text (error, truncation_reason) is capped per entry at MAX_ENTRY_TEXT.
* HARD_RESULT_BYTES bounds the complete list, metadata included, and is never exceeded:
  room is always reserved for every remaining item in compact form.

The property tests feed hostile entries - large values, long errors made of characters
JSON escapes to six bytes, multi-byte text - and check the contract holds for any order.
"""
import asyncio
import json
import random
import pytest
from backend.app import iteration
from backend.app.iteration import (COMPACT_BYTES, DETAILS_OMITTED, HARD_RESULT_BYTES, MAX_ENTRY_TEXT,
                                   MAX_ITEMS_CEILING, RETAINED_VALUE_BYTES, ResultBudget, serialized_bytes)
from backend.tests.test_loop_completeness import durable_setup, jira_connection, loop_flow


def hostile(rng, index):
    text = lambda n: ''.join(rng.choice(['\x01', 'é', '"', '\\', 'a', '漢']) for _ in range(n))
    entry = {'index': index, 'status': rng.choice(['success', 'failed']), 'value': '', 'error': ''}
    if entry['status'] == 'success':
        entry['value'] = text(rng.choice([0, 10, 5000, 64000]))
        if rng.random() < 0.3: entry['truncation_reason'] = text(rng.choice([10, 600, 10000]))
    else:
        entry['error'] = text(rng.choice([10, 600, 10000]))
    return entry


def admit_all(entries, expected):
    budget = ResultBudget(expected)
    return [budget.admit(entry) for entry in entries], budget


def test_the_reserve_for_the_largest_loop_fits_inside_the_hard_ceiling():
    assert 2 + MAX_ITEMS_CEILING * COMPACT_BYTES < HARD_RESULT_BYTES
    assert RETAINED_VALUE_BYTES < HARD_RESULT_BYTES


@pytest.mark.parametrize('seed', range(6))
@pytest.mark.parametrize('items', [1, 10, MAX_ITEMS_CEILING])
def test_hostile_entries_never_exceed_the_hard_ceiling(seed, items):
    rng = random.Random(seed * 1000 + items)
    entries = [hostile(rng, i) for i in range(items)]
    results, budget = admit_all(entries, items)
    assert serialized_bytes(results) <= HARD_RESULT_BYTES
    assert budget.size == serialized_bytes(results), 'the running size is exact'
    assert [(r['index'], r['status']) for r in results] == [(e['index'], e['status']) for e in entries], 'index and status always survive'
    for entry in results:
        for key in ('error', 'truncation_reason'):
            assert len(entry.get(key, '')) <= MAX_ENTRY_TEXT
        if entry.get('details_omitted'): assert serialized_bytes(entry) + 2 <= COMPACT_BYTES
    kept = [bool(r['value']) for r in results if r['status'] == 'success' and not r.get('value_dropped')]
    first_drop = next((i for i, r in enumerate(results) if r.get('value_dropped')), len(results))
    assert not any(r['value'] for r in results[first_drop:]), 'retained values form a prefix'


@pytest.mark.parametrize('seed', range(4))
def test_readmitting_stored_entries_reproduces_them_exactly(seed):
    """What resume does: restored entries pass through admission again."""
    rng = random.Random(seed)
    entries = [hostile(rng, i) for i in range(300)]
    first, _ = admit_all(entries, 300)
    again, _ = admit_all(first, 300)
    assert again == first
    # Interrupted part way: the stored prefix, then the rest fresh, ends the same way.
    budget = ResultBudget(300)
    resumed = [budget.admit(e) for e in first[:137]] + [budget.admit(e) for e in entries[137:]]
    assert resumed == first


def test_long_outcome_text_is_capped_and_marked():
    results, _ = admit_all([{'index': 0, 'status': 'failed', 'value': '', 'error': 'e' * 5000},
                            {'index': 1, 'status': 'success', 'value': 'v', 'error': '', 'truncation_reason': 'r' * 900}], 2)
    assert results[0]['error'] == 'e' * (MAX_ENTRY_TEXT - 1) + '…'
    assert results[1]['truncation_reason'] == 'r' * (MAX_ENTRY_TEXT - 1) + '…'
    assert results[1]['value'] == 'v'


def test_compacted_entries_are_reported_and_keep_their_outcome(monkeypatch):
    """Details are kept whenever room remains: compacting item 1 frees the room item 2
    needs, so only item 1 loses its error text. Values, by contrast, form a prefix."""
    monkeypatch.setattr(iteration, 'HARD_RESULT_BYTES', 2 + 3 * COMPACT_BYTES + 400)
    entries = [{'index': i, 'status': 'failed', 'value': '', 'error': 'x' * 400} for i in range(3)]
    results, _ = admit_all(entries, 3)
    assert [r.get('details_omitted', False) for r in results] == [False, True, False]
    assert results[1] == {'index': 1, 'status': 'failed', 'value': '', 'error': DETAILS_OMITTED, 'details_omitted': True}
    assert serialized_bytes(results) <= iteration.HARD_RESULT_BYTES
    summary = iteration.completeness('each', 3, 3, results, 3)
    assert summary['details_omitted'] == 1 and summary['truncated'] is True and summary['failed'] == 3
    assert 'outcome details of 1 item(s) were omitted' in summary['truncation_reason']


# --------------------------------------------------------------------------- durably

@pytest.mark.asyncio
async def test_a_loop_stores_capped_errors_in_its_rows_and_checkpoint(tmp_path, monkeypatch):
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch, items=3)
    async def execute(node_type, settings, text, tenant_id='local'): raise ValueError('boom ' + 'x' * 5000)
    monkeypatch.setattr(worker.tools, 'execute', execute)
    row = store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(row['id'])
    errors = [r['error'] for r in run['checkpoints']['each']['results']] + [e['error'] for e in run['loop_progress']['each'].values()]
    assert len(errors) == 6 and all(len(e) == MAX_ENTRY_TEXT and e.endswith('…') and e.startswith('boom ') for e in errors)
    assert serialized_bytes(run['checkpoints']['each']['results']) <= HARD_RESULT_BYTES


@pytest.mark.parametrize('values', [False, True])
def test_the_worst_case_metadata_reaches_the_ceiling_and_stays_within_it(values):
    """At the production limits a random mix stays well under the ceiling, so this builds
    the worst case: every item carries maximal outcome text made entirely of characters
    JSON escapes to six bytes (~6 MB uncapped by the ceiling). Compaction must engage and
    the hard maximum must hold, with every index and status kept."""
    worst = '\x01' * 10_000
    entries = [{'index': i, 'status': 'failed' if i % 2 else 'success', 'value': ('v' * 5000 if values and i % 2 == 0 else ''),
                'error': worst if i % 2 else '', **({} if i % 2 else {'truncation_reason': worst})} for i in range(MAX_ITEMS_CEILING)]
    results, budget = admit_all(entries, MAX_ITEMS_CEILING)
    assert serialized_bytes(results) <= HARD_RESULT_BYTES
    assert sum(bool(r.get('details_omitted')) for r in results) > 0, 'the ceiling engaged'
    assert [(r['index'], r['status']) for r in results] == [(e['index'], e['status']) for e in entries]
    again, _ = admit_all(results, MAX_ITEMS_CEILING)
    assert again == results, 'replay reaches the same decisions'
