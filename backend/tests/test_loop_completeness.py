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
async def test_dropped_values_make_the_loop_incomplete(monkeypatch):
    from backend.app import iteration
    monkeypatch.setattr(iteration, 'MAX_RESULT_BYTES', 150)
    resolver, _ = platform(issues(3))
    result = await run(loop_flow(), resolver)
    summary = summary_of(result)
    assert summary['values_dropped'] is True and summary['truncated'] is True
    assert 'item values were dropped' in summary['truncation_reason']
    assert all(r['value'] == '' for r in result['values']['each']['results'])


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
    # name: (loop options, failing item index or None, byte bound or None)
    'stop after a failure': ({'on_item_error': 'stop'}, 1, None),
    'stop on the first item': ({'on_item_error': 'stop'}, 0, None),
    'continue past a failure': ({}, 1, None),
    'values shed for size': ({}, None, 150),
    'limited and shed': ({'max_items': 3}, None, 150),
}


async def execute_case(tmp_path, monkeypatch, case, crash_at=None, items=4):
    from backend.app import iteration
    options, failing, bound = CASES[case]
    if bound is not None: monkeypatch.setattr(iteration, 'MAX_RESULT_BYTES', bound)
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
                'limited and shed': ('limited', 3)}
    for case, (cause, ran) in expected.items():
        run, calls = await execute_case(tmp_path / case.replace(' ', '-'), monkeypatch, case)
        summary = json.loads(run['checkpoints']['each']['summary'])
        assert len(calls) == ran, case
        assert run['truncated'] is (cause is not None) and (cause is None or summary[cause] is True), (case, summary)


# --------------------------------------------------------------------------- legacy checkpoints

async def legacy_resume(tmp_path, monkeypatch, max_items, items=3):
    from sqlalchemy.orm import Session
    from backend.app.storage import RunRecord
    store, worker, execute, _ = durable_setup(tmp_path, monkeypatch, items=items)
    execute.interrupt = True
    row = store.create_run(loop_then_step(jira_connection(store), max_items=max_items).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    with Session(store.engine) as s:
        record = s.get(RunRecord, row['id'])
        checkpoints = {**record.data['checkpoints']}
        checkpoints['each'] = {k: v for k, v in checkpoints['each'].items() if k != 'summary'}
        record.data = {**record.data, 'checkpoints': checkpoints}; s.commit()
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    return store.run(row['id'])


@pytest.mark.asyncio
async def test_a_legacy_limited_checkpoint_is_reconstructed_as_incomplete(tmp_path, monkeypatch):
    """Missing metadata is not evidence of completeness: the 2-of-3 limit is provable."""
    run = await legacy_resume(tmp_path, monkeypatch, max_items=2)
    assert run['status'] == 'success' and run['truncated'] is True
    assert 'each accepted 2 of 3 items' in run['truncation_reason']


@pytest.mark.asyncio
async def test_a_legacy_checkpoint_names_what_it_cannot_verify(tmp_path, monkeypatch):
    run = await legacy_resume(tmp_path, monkeypatch, max_items=100)
    summary = json.loads(next(e for e in run['events'] if e.get('node_id') == 'each' and e.get('cached'))['outputs']['summary'])
    assert run['truncated'] is False and summary['truncated'] is False
    assert summary['reconstructed'] is True
    assert set(summary['unverified']) == {'values_dropped', 'incomplete_items'}


def test_reconstruction_without_the_input_list_leaves_limit_and_stop_unverified():
    from backend.app.iteration import reconstructed_summary
    from backend.app.registry import ForEachConfig
    summary = reconstructed_summary('each', None, ForEachConfig(body='work'), {'results': [{'index': 0, 'status': 'success'}]})
    assert {'limited', 'stopped'} <= set(summary['unverified'])
