"""S09: a loop that did not run every item it was given must say so at run level.

The loop's completeness was only a transient loop_summary event, so a run that processed
1 of 3 items finished as an ordinary success. It is now a declared `summary` output,
checkpointed with the results, and the worker folds it into the run's truncated flag.
"""
import asyncio
import json
import pytest
from backend.app.models import Workflow
from backend.app.tool_service import TransientToolError
from backend.tests.test_iteration import issues, jira_connection, loop_flow, platform, run


def summary_of(result):
    return json.loads(result['values']['each']['summary'])


# --------------------------------------------------------------------------- node output

@pytest.mark.asyncio
async def test_a_complete_loop_reports_itself_complete():
    resolver, _ = platform(issues(3))
    summary = summary_of(await run(loop_flow(), resolver))
    assert summary['truncated'] is False and summary['truncation_reason'] == ''
    assert (summary['total'], summary['accepted'], summary['processed']) == (3, 3, 3)


@pytest.mark.asyncio
async def test_items_beyond_max_items_make_the_loop_incomplete():
    resolver, calls = platform(issues(10))
    events = []
    summary = summary_of(await run(loop_flow(max_items=4), resolver, events))
    assert len(calls) == 4
    assert summary['truncated'] is True and summary['limited'] is True
    assert 'accepted 4 of 10 items' in summary['truncation_reason'] and '6 did not run' in summary['truncation_reason']
    event = next(e for e in events if e.get('kind') == 'loop_summary')
    assert event['status'] == 'warning' and event['truncation_reason'] == summary['truncation_reason']


@pytest.mark.asyncio
async def test_stopping_early_makes_the_loop_incomplete():
    def flaky(index, text):
        if index == 2: raise TransientToolError('gone')
        return 'done'
    resolver, _ = platform(issues(6), flaky)
    summary = summary_of(await run(loop_flow(on_item_error='stop'), resolver))
    assert summary['truncated'] is True and summary['stopped'] is True
    assert 'stopped after item 2 failed; 3 accepted items did not run' in summary['truncation_reason']


@pytest.mark.asyncio
async def test_stopping_on_the_last_item_leaves_nothing_unrun():
    def last_fails(index, text):
        if index == 2: raise TransientToolError('gone')
        return 'done'
    resolver, _ = platform(issues(3), last_fails)
    summary = summary_of(await run(loop_flow(on_item_error='stop'), resolver))
    assert summary['stopped'] is False and summary['truncated'] is False and summary['failed'] == 1


@pytest.mark.asyncio
async def test_failed_items_under_continue_are_failures_not_truncation():
    """Every item was attempted; the failure count, not truncation, reports them."""
    def flaky(index, text):
        if index == 1: raise TransientToolError('gone')
        return 'done'
    resolver, _ = platform(issues(3), flaky)
    result = await run(loop_flow(), resolver)
    assert summary_of(result)['truncated'] is False and result['values']['each']['failed'] == '1'


@pytest.mark.asyncio
async def test_the_earliest_values_are_kept_and_later_ones_dropped(monkeypatch):
    """Each entry serializes to 63 bytes: '[' + two entries + ', ' + ']' is 130 and fits 150; a
    third does not. Dropped values are empty and marked; outcomes are always kept."""
    from backend.app import iteration
    monkeypatch.setattr(iteration, 'MAX_RESULT_BYTES', 150)
    resolver, _ = platform(issues(4), lambda index, text: 'done')
    result = await run(loop_flow(), resolver)
    results, summary = result['values']['each']['results'], summary_of(result)
    assert iteration.serialized_bytes(results[0]) == 63
    assert [bool(r['value']) for r in results] == [True, True, False, False]
    assert [r.get('value_dropped', False) for r in results] == [False, False, True, True]
    assert [r['status'] for r in results] == ['success'] * 4
    assert summary['values_dropped'] is True and summary['dropped_value_count'] == 2 and summary['truncated'] is True
    assert '2 later value(s) were dropped' in summary['truncation_reason']
    assert iteration.serialized_bytes(results[:2]) <= 150 < iteration.serialized_bytes(results[:2] + [{**results[2], 'value': 'done'}])


def test_the_bound_counts_serialized_utf8_bytes_not_characters(monkeypatch):
    """300 'é' are 300 characters but 600 UTF-8 bytes; the list must be measured as stored."""
    from backend.app import iteration
    monkeypatch.setattr(iteration, 'MAX_RESULT_BYTES', 500)
    budget = iteration.ResultBudget()
    entry = budget.admit({'index': 0, 'status': 'success', 'value': 'é' * 300, 'error': ''})
    assert entry['value'] == '' and entry['value_dropped'] is True
    ascii_budget = iteration.ResultBudget()
    assert ascii_budget.admit({'index': 0, 'status': 'success', 'value': 'e' * 300, 'error': ''})['value'] == 'e' * 300


def test_the_running_size_matches_the_serialized_list_exactly():
    from backend.app import iteration
    budget, results = iteration.ResultBudget(), []
    for i, value in enumerate(['a', 'bé', '', '{"k": [1, 2]}', 'ü' * 40]):
        results.append(budget.admit({'index': i, 'status': 'success' if value else 'failed', 'value': value, 'error': ''}))
        assert budget.size == iteration.serialized_bytes(results)


def test_a_value_is_never_cut_part_way(monkeypatch):
    """A value either survives whole or becomes '' - including JSON text, which a cut would corrupt."""
    from backend.app import iteration
    monkeypatch.setattr(iteration, 'MAX_RESULT_BYTES', 120)
    budget = iteration.ResultBudget()
    document = json.dumps({'rows': list(range(20))})
    for i in range(3):
        entry = budget.admit({'index': i, 'status': 'success', 'value': document, 'error': ''})
        assert entry['value'] in (document, '')
        if entry['value']: json.loads(entry['value'])


def test_once_a_value_is_dropped_later_small_values_are_dropped_too(monkeypatch):
    """Retained values always form a prefix, so a consumer never sees a gap in the middle."""
    from backend.app import iteration
    monkeypatch.setattr(iteration, 'MAX_RESULT_BYTES', 300)
    budget = iteration.ResultBudget()
    small = {'index': 2, 'status': 'success', 'value': 'z', 'error': ''}
    kept = [budget.admit({'index': i, 'status': 'success', 'value': v, 'error': ''})['value'] != ''
            for i, v in enumerate(['x' * 50, 'y' * 200])]
    # The small value would fit on its own, so only the prefix rule can drop it.
    assert budget.size + 2 + iteration.serialized_bytes(small) <= iteration.MAX_RESULT_BYTES
    kept.append(budget.admit(small)['value'] != '')
    assert kept == [True, False, False]


# --------------------------------------------------------------------------- action budget

@pytest.mark.asyncio
async def test_an_exhausted_action_budget_stops_the_loop_without_failed_items(monkeypatch):
    """Budget exhaustion is not an item failure: nothing was called, so nothing failed.
    The jira read takes one action, items 0 and 1 the next two, and item 2 is refused."""
    monkeypatch.setenv('AGENT_RUN_BUDGET', '3')
    resolver, calls = platform(issues(10))
    events = []
    result = await run(loop_flow(), resolver, events)
    results, summary = result['values']['each']['results'], summary_of(result)
    assert len(calls) == 2 and [r['status'] for r in results] == ['success', 'success']
    assert result['values']['each']['failed'] == '0'
    assert summary['budget_exhausted'] is True and summary['truncated'] is True and summary['stopped'] is False
    assert 'stopped at item 2' in summary['truncation_reason'] and 'AGENT_RUN_BUDGET' in summary['truncation_reason']
    assert '8 accepted items did not run' in summary['truncation_reason']
    assert not [e for e in events if e.get('status') == 'failed']
    assert [e['item_index'] for e in events if e.get('status') == 'not_run'] == [2]


@pytest.mark.asyncio
async def test_an_attempted_item_whose_retry_is_refused_is_a_real_failure_and_stops(monkeypatch):
    """The first attempt called the tool and failed; the budget then refused the retry.
    That item did fail - it is recorded - and iteration stops because the budget is gone."""
    from backend.app.tool_service import TransientToolError
    monkeypatch.setenv('AGENT_RUN_BUDGET', '2')
    def flaky(index, text): raise TransientToolError('unreachable')
    from backend.app.models import RetryPolicy
    resolver, calls = platform(issues(5), flaky)
    workflow = loop_flow()
    next(n for n in workflow.nodes if n.id == 'work').retry = RetryPolicy(attempts=2, base_delay=0)
    result = await run(workflow, resolver)
    results, summary = result['values']['each']['results'], summary_of(result)
    assert len(calls) == 1
    assert [r['status'] for r in results] == ['failed'] and result['values']['each']['failed'] == '1'
    assert summary['budget_exhausted'] is True and 'stopped at item 1' in summary['truncation_reason']
    assert '4 accepted items did not run' in summary['truncation_reason']


@pytest.mark.asyncio
async def test_an_attempted_failure_under_continue_still_continues(monkeypatch):
    def flaky(index, text):
        if index == 1: raise ValueError('bad input')
        return 'done'
    resolver, calls = platform(issues(4), flaky)
    result = await run(loop_flow(), resolver)
    assert len(calls) == 4 and summary_of(result)['budget_exhausted'] is False
    assert result['values']['each']['failed'] == '1'


@pytest.mark.asyncio
async def test_an_agent_body_that_stopped_early_makes_its_item_incomplete(monkeypatch):
    """The loop keeps only an item's text, which used to discard the agent's truncation."""
    from backend.app import iteration
    async def agent_body(node, text, ctx, identity, emit, **_):
        cut = identity.item_index == 1
        return {'text': 'partial' if cut else 'full', 'provider': 'demo', 'sources': '[]',
                'grounding': json.dumps({'truncated': cut, 'truncation_reason': 'Run-wide AGENT_RUN_BUDGET exhausted.' if cut else ''})}
    monkeypatch.setattr(iteration, 'invoke_attached', agent_body)
    resolver, _ = platform(issues(3))
    result = await run(loop_flow(), resolver)
    summary = summary_of(result)
    assert summary['truncated'] is True and summary['incomplete_items'] == 1
    assert 'AGENT_RUN_BUDGET' in summary['truncation_reason']
    marked = [r['index'] for r in result['values']['each']['results'] if r.get('truncation_reason')]
    assert marked == [1]


# --------------------------------------------------------------------------- run level, durably

def durable_setup(tmp_path, monkeypatch, items=3):
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from backend.app import tool_service
    from cryptography.fernet import Fernet
    monkeypatch.setattr(tool_service, 'public_addresses', lambda host, port: ['93.184.216.34'])
    store = Store(f'sqlite:///{tmp_path}/loop.db', Fernet.generate_key())
    worker = Worker(store)
    jira = json.dumps({'issues': [{'key': f'PROJ-{i}', 'fields': {
        'summary': f'Issue {i}', 'status': {'name': 'Open'},
        'assignee': {'displayName': 'Ana'}, 'updated': '2026-09-01T00:00:00Z'}} for i in range(items)]})
    async def prepared(_prepared, tenant_id='local'): return jira
    calls = []
    async def execute(node_type, settings, text, tenant_id='local'):
        calls.append(text)
        if text == 'go' and calls.count('go') == 1 and getattr(execute, 'interrupt', False):
            raise asyncio.CancelledError()  # A killed worker, after the loop checkpointed.
        return 'done'
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    monkeypatch.setattr(worker.tools, 'execute', execute)
    return store, worker, execute, calls


def loop_then_step(connection_id, max_items):
    """tickets -> each (limited) -> after -> out, so the run can stop after the loop."""
    document = loop_flow(max_items=max_items, connection_id=connection_id).model_dump(mode='json')
    document['nodes'].append({'id': 'after', 'type': 'tool_python', 'inputs': {'input': 'input.message'},
                              'config': {'code': 'print(input_text)', 'description': 'Runs after the loop.'}})
    document['edges'] = [e for e in document['edges'] if e['id'] != 'c'] + [
        {'id': 'c', 'source': 'each', 'target': 'after'}, {'id': 'f', 'source': 'after', 'target': 'out'}]
    return Workflow.model_validate(document)


@pytest.mark.asyncio
async def test_a_limited_loop_marks_the_durable_run_truncated(tmp_path, monkeypatch):
    """The S09 scenario at the worker boundary: 3 items, max_items 1."""
    store, worker, _, calls = durable_setup(tmp_path, monkeypatch)
    row = store.create_run(loop_flow(max_items=1, connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    finished = store.run(row['id'])
    assert finished['status'] == 'success' and len(calls) == 1
    assert finished['truncated'] is True
    assert 'each accepted 1 of 3 items' in finished['truncation_reason']
    listed = next(r for r in store.runs_page('local', None, 10)['items'] if r['id'] == row['id'])
    assert listed['truncated'] is True


@pytest.mark.asyncio
async def test_a_complete_loop_leaves_the_durable_run_untruncated(tmp_path, monkeypatch):
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch)
    row = store.create_run(loop_flow(connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    finished = store.run(row['id'])
    assert finished['status'] == 'success' and finished['truncated'] is False


@pytest.mark.asyncio
async def test_truncation_survives_resume_from_the_loop_checkpoint(tmp_path, monkeypatch):
    """The loop is restored from its checkpoint, not re-run; its summary must come with it."""
    store, worker, execute, calls = durable_setup(tmp_path, monkeypatch)
    execute.interrupt = True
    row = store.create_run(loop_then_step(jira_connection(store), max_items=2).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    first = store.run(row['id'])
    assert first['status'] != 'success' and 'summary' in first['checkpoints']['each']
    body_calls = len(calls) - 1

    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    second = store.run(row['id'])
    assert second['status'] == 'success'
    assert len(calls) - 2 == body_calls == 2, 'the loop body must not run again on resume'
    assert second['truncated'] is True and 'each accepted 2 of 3 items' in second['truncation_reason']


@pytest.mark.asyncio
async def test_a_checkpoint_from_before_the_summary_port_still_resumes(tmp_path, monkeypatch):
    """Runs checkpointed by the previous release have no summary; they must not crash."""
    from sqlalchemy.orm import Session
    from backend.app.storage import RunRecord
    store, worker, execute, _ = durable_setup(tmp_path, monkeypatch)
    execute.interrupt = True
    row = store.create_run(loop_then_step(jira_connection(store), max_items=2).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    with Session(store.engine) as s:
        record = s.get(RunRecord, row['id'])
        checkpoints = {**record.data['checkpoints']}
        checkpoints['each'] = {k: v for k, v in checkpoints['each'].items() if k != 'summary'}
        record.data = {**record.data, 'checkpoints': checkpoints}; s.commit()
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(row['id'])['status'] == 'success'


# --------------------------------------------------------------------------- replay equivalence
# A run interrupted after any item's durable progress commit, then resumed, must end
# exactly where an uninterrupted run ends: same results, same summary, same run-level
# verdict, and no item executed twice. Restored items go through the same aggregation as
# fresh ones, so the stop policy and the result bound apply to them too.

def crash_after_item(store, monkeypatch, index):
    """Stop the worker right after the progress event for `index` is committed."""
    original = store.worker_event
    def record(id, owner, event):
        result = original(id, owner, event)
        if event.get('kind') == 'loop_item' and event.get('item_index') == index:
            raise asyncio.CancelledError()
        return result
    monkeypatch.setattr(store, 'worker_event', record)
    return lambda: monkeypatch.setattr(store, 'worker_event', original)


CASES = {
    # name: (loop options, failing item index or None, byte bound or None, action budget or None)
    'stop after a failure': ({'on_item_error': 'stop'}, 1, None, None),
    'stop on the first item': ({'on_item_error': 'stop'}, 0, None, None),
    'continue past a failure': ({}, 1, None, None),
    'values shed for size': ({}, None, 150, None),
    'limited and shed': ({'max_items': 3}, None, 150, None),
    'action budget runs out': ({}, None, None, 3),
}


async def execute_case(tmp_path, monkeypatch, case, crash_at=None, items=4):
    from backend.app import iteration
    options, failing, bound, actions = CASES[case]
    if bound is not None: monkeypatch.setattr(iteration, 'MAX_RESULT_BYTES', bound)
    if actions is not None: monkeypatch.setenv('AGENT_RUN_BUDGET', str(actions))
    tmp_path.mkdir()
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch, items=items)
    calls = []
    async def execute(node_type, settings, text, tenant_id='local'):
        calls.append(json.loads(text)['key'])
        if failing is not None and len(calls) - 1 == failing and calls.count(calls[-1]) == 1:
            raise ValueError('item failed')
        return 'done'
    monkeypatch.setattr(worker.tools, 'execute', execute)
    row = store.create_run(loop_flow(connection_id=jira_connection(store), **options).model_dump(mode='json'), 'go')
    if crash_at is not None:
        restore = crash_after_item(store, monkeypatch, crash_at)
        await worker.execute(store.claim_next(worker.owner))
        assert store.run(row['id'])['status'] != 'success'
        restore()
        store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(row['id'])
    assert run['status'] == 'success', run.get('error')
    return run, calls


@pytest.mark.asyncio
@pytest.mark.parametrize('case', sorted(CASES))
async def test_resume_at_every_item_ends_exactly_like_an_uninterrupted_run(tmp_path, monkeypatch, case):
    baseline, baseline_calls = await execute_case(tmp_path / 'baseline', monkeypatch, case)
    for crash_at in range(len(baseline_calls)):
        resumed, calls = await execute_case(tmp_path / f'crash{crash_at}', monkeypatch, case, crash_at)
        where = f'{case}, interrupted after item {crash_at}'
        assert calls == baseline_calls, f'{where}: items ran {calls}, expected {baseline_calls}'
        assert resumed['checkpoints']['each'] == baseline['checkpoints']['each'], where
        assert (resumed['truncated'], resumed['truncation_reason']) == (baseline['truncated'], baseline['truncation_reason']), where


@pytest.mark.asyncio
async def test_the_equivalence_cases_cover_each_cause(tmp_path, monkeypatch):
    """Pins what the baselines demonstrate, so the equivalence test compares real outcomes."""
    expected = {'stop after a failure': ('stopped', 2), 'stop on the first item': ('stopped', 1),
                'continue past a failure': (None, 4), 'values shed for size': ('values_dropped', 4),
                'limited and shed': ('limited', 3), 'action budget runs out': ('budget_exhausted', 2)}
    for case, (cause, ran) in expected.items():
        run, calls = await execute_case(tmp_path / case.replace(' ', '-'), monkeypatch, case)
        summary = json.loads(run['checkpoints']['each']['summary'])
        assert len(calls) == ran, case
        assert run['truncated'] is (cause is not None) and (cause is None or summary[cause] is True), (case, summary)


# --------------------------------------------------------------------------- legacy checkpoints

async def legacy_resume(tmp_path, monkeypatch, max_items, items=3, agent_body=False):
    from sqlalchemy.orm import Session
    from backend.app.storage import RunRecord
    from backend.app import iteration
    store, worker, execute, _ = durable_setup(tmp_path, monkeypatch, items=items)
    execute.interrupt = True
    document = loop_then_step(jira_connection(store), max_items=max_items).model_dump(mode='json')
    if agent_body:
        body = next(n for n in document['nodes'] if n['id'] == 'work')
        body.update(type='agent', config={'provider': 'demo', 'role': 'reasoner'})
        async def agent(node, text, ctx, identity, emit, **_):
            return {'text': 'answer', 'provider': 'demo', 'sources': '[]', 'grounding': json.dumps({'truncated': False})}
        monkeypatch.setattr(iteration, 'invoke_attached', agent)
    row = store.create_run(document, 'go')
    await worker.execute(store.claim_next(worker.owner))
    with Session(store.engine) as s:
        record = s.get(RunRecord, row['id'])
        checkpoints = {**record.data['checkpoints']}
        checkpoints['each'] = {k: v for k, v in checkpoints['each'].items() if k != 'summary'}
        record.data = {**record.data, 'checkpoints': checkpoints}; s.commit()
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(row['id'])
    assert run['status'] == 'success', run.get('error')
    cached = next(e for e in run['events'] if e.get('node_id') == 'each' and e.get('cached'))
    return run, json.loads(cached['outputs']['summary'])


@pytest.mark.asyncio
async def test_a_legacy_limited_checkpoint_is_reconstructed_as_incomplete(tmp_path, monkeypatch):
    """Missing metadata is not evidence of completeness: the 2-of-3 limit is provable."""
    run, _ = await legacy_resume(tmp_path, monkeypatch, max_items=2)
    assert run['truncated'] is True and run['truncation_source'] == 'confirmed'
    assert 'each accepted 2 of 3 items' in run['truncation_reason']


@pytest.mark.asyncio
async def test_a_fully_provable_legacy_checkpoint_remains_complete(tmp_path, monkeypatch):
    """A tool body cannot report agent truncation, and its non-empty values prove nothing
    was dropped, so every property is provable and the loop is not truncated."""
    run, summary = await legacy_resume(tmp_path, monkeypatch, max_items=100)
    assert summary['reconstructed'] is True and summary['unverified'] == []
    assert run['truncated'] is False and run['truncation_source'] == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('max_items,source', [(100, 'legacy_checkpoint_unverified'), (2, 'confirmed')])
async def test_unverifiable_legacy_completeness_counts_as_incomplete(tmp_path, monkeypatch, max_items, source):
    """An agent body may have stopped early, and an older checkpoint never recorded it.
    With a provable limit as well, the confirmed cause outranks the unverifiable one."""
    from backend.app.iteration import LEGACY_UNVERIFIED
    run, summary = await legacy_resume(tmp_path, monkeypatch, max_items=max_items, agent_body=True)
    assert summary['unverified'] == ['agent_item_truncation']
    assert summary['truncation_source'] == source
    assert run['truncated'] is True and run['truncation_source'] == source
    assert LEGACY_UNVERIFIED in run['truncation_reason']
    assert ('accepted 2 of 3' in run['truncation_reason']) is (source == 'confirmed')


def reconstruct(results, items=('a', 'b'), body='tool_python'):
    from backend.app.iteration import reconstructed_summary
    from backend.app.registry import ForEachConfig
    return reconstructed_summary('each', list(items) if items is not None else None,
                                 ForEachConfig(body='work'), {'results': results}, body)


def test_a_blank_first_success_leaves_dropped_values_unverifiable():
    """A size drop blanks the first success and nothing refills it; an empty output
    looks the same, so the drop cannot be ruled out."""
    blank = reconstruct([{'index': 0, 'status': 'success', 'value': ''}, {'index': 1, 'status': 'success', 'value': 'x'}])
    assert blank['unverified'] == ['values_dropped'] and blank['truncated'] is True
    kept = reconstruct([{'index': 0, 'status': 'success', 'value': 'x'}, {'index': 1, 'status': 'success', 'value': ''}])
    assert kept['unverified'] == [] and kept['truncated'] is False


def test_no_successful_items_means_no_payload_could_be_lost():
    summary = reconstruct([{'index': 0, 'status': 'failed', 'value': '', 'error': 'e'},
                           {'index': 1, 'status': 'failed', 'value': '', 'error': 'e'}])
    assert 'values_dropped' not in summary['unverified']


@pytest.mark.parametrize('body,unverifiable', [('tool_python', False), ('tool_http', False), ('retrieve', False),
                                               ('agent', True), ('query', True)])
def test_only_bodies_that_report_grounding_leave_agent_truncation_unverifiable(body, unverifiable):
    summary = reconstruct([{'index': 0, 'status': 'success', 'value': 'x'}, {'index': 1, 'status': 'success', 'value': 'y'}], body=body)
    assert ('agent_item_truncation' in summary['unverified']) is unverifiable


def test_reconstruction_without_the_input_list_leaves_limit_and_stop_unverified():
    summary = reconstruct([{'index': 0, 'status': 'success', 'value': 'x'}], items=None)
    assert summary['unverified'] == ['limited', 'stopped']
    assert summary['truncated'] is True and summary['truncation_source'] == 'legacy_checkpoint_unverified'


@pytest.mark.parametrize('order', [('legacy', 'agent'), ('agent', 'legacy')])
def test_a_confirmed_cause_elsewhere_outranks_legacy_uncertainty(order):
    """One node's unverifiable completeness must not hide another node's confirmed cut."""
    from backend.app.worker import run_truncation
    workflow = loop_flow()
    legacy = {'results': [], 'failed': '0', 'summary': json.dumps(
        {'truncated': True, 'truncation_reason': 'unverified', 'truncation_source': 'legacy_checkpoint_unverified'})}
    agent = {'text': 't', 'grounding': json.dumps({'truncated': True, 'truncation_reason': 'budget'})}
    outputs = {'legacy': ('each', legacy), 'agent': ('tickets', agent)}
    values = dict(outputs[name] for name in order)
    result = run_truncation(workflow, values)
    assert result['truncation_source'] == 'confirmed'
    assert set(result['truncation_reason'].split('; ')) == {'unverified', 'budget'}
    only_legacy = run_truncation(workflow, {'each': legacy})
    assert only_legacy['truncation_source'] == 'legacy_checkpoint_unverified'
    assert run_truncation(workflow, {})['truncation_source'] == ''
