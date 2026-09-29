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
