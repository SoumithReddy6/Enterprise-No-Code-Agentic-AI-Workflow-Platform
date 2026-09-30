"""A graph node refused by the run-wide action budget must never look like completed work.

With on_error continue or route, a refused node recovers and the run succeeds. Before
this, nothing recorded why it failed, so a run that skipped work for lack of budget was
indistinguishable from a complete one. on_error fail still fails the run.
"""
import asyncio
import json
import pytest
from backend.app.compiler import compile_workflow
from backend.app.models import RetryPolicy, Workflow
from backend.app.tool_service import TransientToolError
from backend.app.worker import run_truncation


def tool(id, **extra):
    return {'id': id, 'type': 'tool_python', 'inputs': {'input': 'input.message'},
            'config': {'code': 'print(input_text)', 'description': 'Echoes.'}, **extra}


def respond(id, source='input.message'):
    return {'id': id, 'type': 'response', 'inputs': {'text': source}}


def budget_flow(policy):
    """first spends the only action; second is refused and applies `policy`."""
    nodes = [{'id': 'input', 'type': 'chat_input'}, tool('first'), tool('second', on_error=policy)]
    edges = [{'id': 'a', 'source': 'input', 'target': 'first'}, {'id': 'b', 'source': 'first', 'target': 'second'}]
    if policy == 'route':
        nodes += [respond('ok', 'second.text'), respond('fallback')]
        edges += [{'id': 'c', 'source': 'second', 'target': 'ok'},
                  {'id': 'd', 'source': 'second', 'target': 'fallback', 'sourceHandle': 'error'}]
    else:
        nodes.append(respond('out'))
        edges.append({'id': 'c', 'source': 'second', 'target': 'out'})
    return Workflow.model_validate({'version': 1, 'name': 'Budget', 'nodes': nodes, 'edges': edges})


async def execute(workflow, calls, error=None):
    events = []
    async def resolver(action, node_type, settings, text, identity):
        calls.append(identity.node_id)
        if error is not None and identity.node_id == 'second': raise error
        return 'ok'
    async def emit(event): events.append(dict(event))
    result = await compile_workflow(workflow, message='go', platform_resolver=resolver, emit=emit).graph.ainvoke({'values': {}})
    return result, events


# --------------------------------------------------------------------------- recovery policies

@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'route'])
async def test_a_refused_node_that_recovers_marks_the_run_incomplete(monkeypatch, policy):
    monkeypatch.setenv('AGENT_RUN_BUDGET', '1')
    workflow, calls = budget_flow(policy), []
    result, events = await execute(workflow, calls)
    assert calls == ['first'], 'the refused node must not reach the tool'
    if policy == 'route': assert 'fallback' in result['values'] and 'ok' not in result['values']
    verdict = run_truncation(workflow, result['values'])
    assert verdict['truncated'] is True and verdict['truncation_source'] == 'confirmed'
    assert 'second' in verdict['truncation_reason'] and 'AGENT_RUN_BUDGET' in verdict['truncation_reason']


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'route'])
async def test_the_recovered_event_keeps_the_typed_exhaustion(monkeypatch, policy):
    monkeypatch.setenv('AGENT_RUN_BUDGET', '1')
    _, events = await execute(budget_flow(policy), [])
    failed = next(e for e in events if e.get('node_id') == 'second' and e.get('status') == 'failed')
    assert failed['recovered'] is True and failed['budget_exhausted'] is True
    assert 'Run-wide agent action budget exhausted' in failed['error'], 'not a generic error'


@pytest.mark.asyncio
async def test_on_error_fail_still_fails_the_run(monkeypatch):
    monkeypatch.setenv('AGENT_RUN_BUDGET', '1')
    events = []
    with pytest.raises(ValueError, match='action budget exhausted'):
        await execute(budget_flow('fail'), [])


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'route'])
async def test_an_ordinary_recovered_error_is_a_failure_not_truncation(monkeypatch, policy):
    """The node ran and failed; the workflow chose to carry on. Nothing was skipped for
    lack of budget, so this is not truncation."""
    workflow = budget_flow(policy)
    result, events = await execute(workflow, [], error=ValueError('tool unavailable'))
    assert run_truncation(workflow, result['values'])['truncated'] is False
    failed = next(e for e in events if e.get('node_id') == 'second' and e.get('status') == 'failed')
    assert 'budget_exhausted' not in failed


@pytest.mark.asyncio
async def test_a_retry_refused_after_a_real_attempt_is_still_reported(monkeypatch):
    """The first attempt called the tool and failed transiently; the budget then refused
    the retry. The node did run, but its work is incomplete for lack of budget."""
    monkeypatch.setenv('AGENT_RUN_BUDGET', '2')
    workflow = budget_flow('continue')
    next(n for n in workflow.nodes if n.id == 'second').retry = RetryPolicy(attempts=2, base_delay=0)
    calls = []
    async def resolver(action, node_type, settings, text, identity):
        calls.append(identity.node_id)
        if identity.node_id == 'second': raise TransientToolError('busy')
        return 'ok'
    result = await compile_workflow(workflow, message='go', platform_resolver=resolver).graph.ainvoke({'values': {}})
    assert calls == ['first', 'second']
    verdict = run_truncation(workflow, result['values'])
    assert verdict['truncated'] is True and 'second' in verdict['truncation_reason']
    assert '1 attempt' in verdict['truncation_reason']


# --------------------------------------------------------------------------- durably, and across resume

def durable(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store = Store(f'sqlite:///{tmp_path}/budget.db', Fernet.generate_key())
    worker = Worker(store)
    calls = []
    async def execute_tool(node_type, settings, text, tenant_id='local'):
        calls.append(text); return 'ok'
    monkeypatch.setattr(worker.tools, 'execute', execute_tool)
    return store, worker, calls


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'route'])
async def test_the_durable_run_records_the_confirmed_truncation(tmp_path, monkeypatch, policy):
    monkeypatch.setenv('AGENT_RUN_BUDGET', '1')
    store, worker, calls = durable(tmp_path, monkeypatch)
    row = store.create_run(budget_flow(policy).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(row['id'])
    assert run['status'] == 'success' and len(calls) == 1
    assert run['truncated'] is True and run['truncation_source'] == 'confirmed'
    assert 'second' in run['truncation_reason'] and 'AGENT_RUN_BUDGET' in run['truncation_reason']


@pytest.mark.asyncio
async def test_resume_after_the_recovery_reaches_the_same_verdict(tmp_path, monkeypatch):
    """Recovered nodes are not checkpointed, so second runs again on resume. The budget
    it was refused is persisted, so it is refused again - never granted afresh - and the
    resumed run ends exactly like the uninterrupted one."""
    monkeypatch.setenv('AGENT_RUN_BUDGET', '1')
    (tmp_path / 'a').mkdir()
    baseline_store, baseline_worker, _ = durable(tmp_path / 'a', monkeypatch)
    row = baseline_store.create_run(budget_flow('continue').model_dump(mode='json'), 'go')
    await baseline_worker.execute(baseline_store.claim_next(baseline_worker.owner))
    baseline = baseline_store.run(row['id'])

    (tmp_path / 'b').mkdir()
    store, worker, calls = durable(tmp_path / 'b', monkeypatch)
    original = store.worker_event
    def crash(id, owner, event):
        persisted = original(id, owner, event)
        if event.get('node_id') == 'second' and event.get('recovered'): raise asyncio.CancelledError()
        return persisted
    monkeypatch.setattr(store, 'worker_event', crash)
    row = store.create_run(budget_flow('continue').model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(row['id'])['status'] != 'success'
    monkeypatch.setattr(store, 'worker_event', original)
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    resumed = store.run(row['id'])
    assert resumed['status'] == 'success' and len(calls) == 1, 'first is restored, second is refused again'
    assert resumed['accounting']['action_budget']['remaining'] == 0
    assert resumed['truncated'] is True and 'second' in resumed['truncation_reason']
    for key in ('truncated', 'truncation_reason', 'truncation_source', 'output'):
        assert resumed[key] == baseline[key], key
