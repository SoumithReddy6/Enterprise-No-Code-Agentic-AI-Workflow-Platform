"""An incomplete run records each cause: which node, what kind, how certain, and why.

The joined truncation_reason cannot tell a capped tool from a stopped loop. The run also
stores truncation_causes, one per node, with kind loop, agent, tool or budget and the
source (confirmed, or legacy_checkpoint_unverified), which the run view renders.
"""
import json
import pytest
from backend.app.execution_policy import TRUNCATION_KEY, truncation
from backend.app.models import Workflow
from backend.app.worker import run_truncation
from backend.tests.test_loop_completeness import loop_flow


def kinds(values, workflow=None):
    return [(c['node_id'], c['kind'], c['source']) for c in run_truncation(workflow or loop_flow(), values)['truncation_causes']]


def test_each_kind_is_named_from_where_it_was_reported():
    legacy = {'truncated': True, 'truncation_reason': 'older checkpoint', 'truncation_source': 'legacy_checkpoint_unverified'}
    values = {
        'each': {'results': [], 'failed': '0', 'summary': json.dumps(legacy)},
        'agent': {'text': 'partial', 'grounding': json.dumps({'truncated': True, 'truncation_reason': 'step budget'})},
        'tickets': {'text': '{}', 'items': [], TRUNCATION_KEY: truncation('tickets: Jira returned 100 of 200 matching issues.')},
        'refused': {TRUNCATION_KEY: truncation('refused did not run: Run-wide agent action budget exhausted.')},
        'fine': {'text': 'ok'},
    }
    assert kinds(values) == [('each', 'loop', 'legacy_checkpoint_unverified'), ('agent', 'agent', 'confirmed'),
                             ('tickets', 'tool', 'confirmed'), ('refused', 'budget', 'confirmed')]
    verdict = run_truncation(loop_flow(), values)
    assert verdict['truncation_source'] == 'confirmed', 'a confirmed cause outranks legacy uncertainty'
    assert [c['reason'] for c in verdict['truncation_causes']][2] == 'tickets: Jira returned 100 of 200 matching issues.'


def test_a_complete_run_has_no_causes():
    verdict = run_truncation(loop_flow(), {'tickets': {'text': '{}', 'items': []}, 'out': {'text': 'ok'}})
    assert verdict['truncated'] is False and verdict['truncation_causes'] == []


def test_a_cause_reason_is_bounded():
    values = {'agent': {'text': 'x', 'grounding': json.dumps({'truncated': True, 'truncation_reason': 'r' * 5000})}}
    assert len(run_truncation(loop_flow(), values)['truncation_causes'][0]['reason']) == 1000


# --------------------------------------------------------------------------- durably

@pytest.mark.asyncio
async def test_a_capped_search_is_stored_as_a_tool_cause(tmp_path, monkeypatch):
    from backend.tests.test_tool_truncation import jira_connection, page, search_flow, with_jira
    store, worker = with_jira(tmp_path, monkeypatch, page(100, total=200))
    row = store.create_run(search_flow(jira_connection(store)).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(row['id'])['truncation_causes'] == [{'node_id': 'tickets', 'kind': 'tool', 'source': 'confirmed',
        'reason': 'tickets: Jira returned 100 of 200 matching issues; stopped by further result pages that were not fetched.'}]


@pytest.mark.asyncio
async def test_a_recovered_budget_refusal_is_stored_as_a_budget_cause(tmp_path, monkeypatch):
    from backend.tests.test_budget_recovery import budget_flow, durable
    monkeypatch.setenv('AGENT_RUN_BUDGET', '1')
    store, worker, _ = durable(tmp_path, monkeypatch)
    row = store.create_run(budget_flow('continue').model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    [cause] = store.run(row['id'])['truncation_causes']
    assert (cause['node_id'], cause['kind'], cause['source']) == ('second', 'budget', 'confirmed') and 'AGENT_RUN_BUDGET' in cause['reason']


@pytest.mark.asyncio
async def test_a_limited_loop_is_stored_as_a_loop_cause_and_resume_clears_it(tmp_path, monkeypatch):
    from backend.tests.test_loop_completeness import durable_setup, jira_connection
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch)
    row = store.create_run(loop_flow(max_items=1, connection_id=jira_connection(store)).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    [cause] = store.run(row['id'])['truncation_causes']
    assert (cause['node_id'], cause['kind']) == ('each', 'loop') and 'accepted 1 of 3 items' in cause['reason']
    from sqlalchemy.orm import Session
    from backend.app.storage import RunRecord
    with Session(store.engine) as s:  # Resume needs a failed or cancelled run.
        record = s.get(RunRecord, row['id']); record.data = {**record.data, 'status': 'failed'}; record.status = 'failed'; s.commit()
    store.resume_run(row['id'])
    assert store.run(row['id'])['truncation_causes'] == []
