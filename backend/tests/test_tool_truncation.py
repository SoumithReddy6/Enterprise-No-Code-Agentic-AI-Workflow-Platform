"""A tool that caps its result makes the run incomplete, and says why.

A Jira search keeps at most 100 issues, within a 64,000-byte budget, from one page. When
it stops short, the node's outputs carry confirmed truncation metadata under the reserved
key: checkpointed with the outputs, so a resumed run reports it too. Agents and loop
bodies that call such a tool carry it into their own truncation.
"""
import asyncio
import json
import pytest
from backend.app.compiler import compile_workflow
from backend.app.execution_policy import TRUNCATION_KEY
from backend.app.models import Workflow
from backend.app.tool_service import jira_items
from backend.app.worker import run_truncation
from backend.tests.test_loop_completeness import durable_setup, jira_connection, loop_flow


def issue(i, summary='Issue'):
    return {'key': f'PROJ-{i}', 'fields': {'summary': f'{summary} {i}', 'status': {'name': 'Open'},
                                           'assignee': {'displayName': 'Ana'}, 'updated': '2026-09-01T00:00:00Z'}}


def page(n, total=None, **extra):
    return json.dumps({'issues': [issue(i) for i in range(n)], **({'total': total, 'startAt': 0} if total is not None else {}), **extra})


# --------------------------------------------------------------------------- the reason

@pytest.mark.parametrize('body,expected', [
    (page(100, total=200), 'Jira returned 100 of 200 matching issues; stopped by further result pages that were not fetched.'),
    (page(150), 'Jira returned 100 of 150 matching issues; stopped by the 100-item result limit.'),
    (page(1, nextPageToken='next'), 'Jira returned 1 matching issue, and more exist; stopped by further result pages that were not fetched.'),
])
def test_the_projection_names_how_much_it_returned_and_why(body, expected):
    result = jira_items(body, 'search', 'https://jira.example.com')
    assert result['truncated'] is True and result['truncation_reason'] == expected


def test_the_byte_budget_is_named_when_it_is_what_stopped_the_projection():
    body = json.dumps({'issues': [issue(i, 's' * 900) for i in range(80)]})
    result = jira_items(body, 'search', 'https://jira.example.com')
    assert result['truncated'] is True and 'the 64,000-byte result budget' in result['truncation_reason']
    assert result['truncation_reason'].startswith(f"Jira returned {len(result['items'])} of 80 matching issues")


def test_a_complete_result_has_no_reason():
    result = jira_items(page(3, total=3), 'search', 'https://jira.example.com')
    assert result['truncated'] is False and 'truncation_reason' not in result


# --------------------------------------------------------------------------- run level, durably

def search_flow(connection_id):
    return Workflow.model_validate({'version': 1, 'name': 'Search', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': connection_id, 'operation': 'search', 'jql': 'assignee=me'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'tickets'}, {'id': 'b', 'source': 'tickets', 'target': 'out'}]})


def with_jira(tmp_path, monkeypatch, body):
    store, worker, _, _ = durable_setup(tmp_path, monkeypatch)
    async def prepared(_prepared, tenant_id='local'): return body
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    return store, worker


@pytest.mark.asyncio
async def test_a_search_returning_100_of_200_marks_the_run_incomplete(tmp_path, monkeypatch):
    """The acceptance case."""
    store, worker = with_jira(tmp_path, monkeypatch, page(100, total=200))
    row = store.create_run(search_flow(jira_connection(store)).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(row['id'])
    assert run['status'] == 'success' and run['truncated'] is True and run['truncation_source'] == 'confirmed'
    assert run['truncation_reason'] == 'tickets: Jira returned 100 of 200 matching issues; stopped by further result pages that were not fetched.'
    assert run['checkpoints']['tickets'][TRUNCATION_KEY]['truncated'] is True


@pytest.mark.asyncio
async def test_a_complete_search_leaves_the_run_complete(tmp_path, monkeypatch):
    store, worker = with_jira(tmp_path, monkeypatch, page(3, total=3))
    row = store.create_run(search_flow(jira_connection(store)).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(row['id'])
    assert run['truncated'] is False and TRUNCATION_KEY not in run['checkpoints']['tickets']


@pytest.mark.asyncio
async def test_the_truncation_survives_resume_from_the_checkpoint(tmp_path, monkeypatch):
    """tickets is restored from its checkpoint, not re-run; the metadata must come with it."""
    store, worker = with_jira(tmp_path, monkeypatch, page(100, total=200))
    calls = []
    async def prepared(_prepared, tenant_id='local'):
        calls.append(1); return page(100, total=200)
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    original = store.worker_event
    def crash(id, owner, event):
        persisted = original(id, owner, event)
        if event.get('node_id') == 'tickets' and event['status'] == 'success': raise asyncio.CancelledError()
        return persisted
    monkeypatch.setattr(store, 'worker_event', crash)
    row = store.create_run(search_flow(jira_connection(store)).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    monkeypatch.setattr(store, 'worker_event', original)
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(row['id'])
    assert run['status'] == 'success' and len(calls) == 1, 'the search is restored, not repeated'
    assert run['truncated'] is True and 'Jira returned 100 of 200' in run['truncation_reason']


# --------------------------------------------------------------------------- through a loop and an agent

@pytest.mark.asyncio
async def test_a_loop_body_whose_search_is_capped_makes_its_item_incomplete(tmp_path, monkeypatch):
    store, worker = with_jira(tmp_path, monkeypatch, page(2, total=2))
    document = loop_flow(connection_id=jira_connection(store)).model_dump(mode='json')
    body = next(n for n in document['nodes'] if n['id'] == 'work')
    body.update(type='tool_jira', config={'connection_id': document['nodes'][1]['config']['connection_id'], 'operation': 'search', 'jql': 'parent=x'})
    capped = iter([page(2, total=2), page(1, total=5), page(1, total=1)])
    async def prepared(_prepared, tenant_id='local'): return next(capped)
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    row = store.create_run(document, 'go')
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(row['id'])
    summary = json.loads(run['checkpoints']['each']['summary'])
    assert summary['incomplete_items'] == 1 and 'work: Jira returned 1 of 5' in summary['truncation_reason']
    assert [bool(r.get('truncation_reason')) for r in run['checkpoints']['each']['results']] == [True, False]
    assert run['truncated'] is True and 'each: 1 item(s) returned incomplete results' in run['truncation_reason']


@pytest.mark.asyncio
async def test_an_agent_answering_from_a_capped_search_is_incomplete_and_told_so(monkeypatch):
    from backend.tests.test_agent_tools import scripted
    seen = []
    scripted(monkeypatch, [json.dumps({'action': 'call', 'target': 'search', 'input': 'open bugs'}),
                           json.dumps({'action': 'final', 'text': 'There are 100 open bugs.'})], seen)
    workflow = Workflow.model_validate({'version': 1, 'name': 'Agent search', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': {'provider': 'demo'}},
        {'id': 'search', 'type': 'tool_jira', 'config': {'connection_id': 'c1', 'operation': 'search', 'jql': 'type=bug'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'agent.text'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'agent'}, {'id': 'b', 'source': 'agent', 'target': 'out'},
                  {'id': 't', 'source': 'search', 'target': 'agent', 'kind': 'tool', 'targetHandle': 'tools'}]})
    async def platform(action, node_type, settings, text, identity):
        return {'text': page(100, total=240), **jira_items(page(100, total=240), 'search', 'https://jira.example.com')}
    result = await compile_workflow(workflow, message='How many open bugs?', platform_resolver=platform).graph.ainvoke({'values': {}})
    grounding = json.loads(result['values']['agent']['grounding'])
    assert grounding['truncated'] is True and 'Jira returned 100 of 240' in grounding['truncation_reason']
    assert 'truncated' in seen[-1], 'the model is told the tool result is incomplete'
    verdict = run_truncation(workflow, result['values'])
    assert verdict['truncated'] is True and 'search: Jira returned 100 of 240' in verdict['truncation_reason']


# --------------------------------------------------------------------------- the contract

@pytest.mark.asyncio
@pytest.mark.parametrize('extra,valid', [({TRUNCATION_KEY: {'truncated': True, 'truncation_reason': 'r', 'truncation_source': 'confirmed'}}, True),
                                         ({TRUNCATION_KEY: 'truncated'}, False), ({'surprise': 'x'}, False)])
async def test_only_structured_metadata_may_travel_beside_the_declared_ports(monkeypatch, extra, valid):
    from backend.app import registry
    definition = registry.REGISTRY['tool_python']
    async def handler(inputs, config, ctx): return {'text': 'ok', **extra}
    monkeypatch.setattr(definition, 'handler', handler)
    workflow = Workflow.model_validate({'version': 1, 'name': 'Contract', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'work', 'type': 'tool_python', 'inputs': {'input': 'input.message'}, 'config': {'code': 'print(1)', 'description': 'x'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'work.text'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'work'}, {'id': 'b', 'source': 'work', 'target': 'out'}]})
    graph = compile_workflow(workflow, message='go').graph
    if valid:
        result = await graph.ainvoke({'values': {}})
        assert run_truncation(workflow, result['values'])['truncated'] is True
    else:
        with pytest.raises(ValueError, match='declared contract'):
            await graph.ainvoke({'values': {}})
