"""Events generated inside a loop carry the loop's identity and are stored as previews.

An agent running as a loop body produces agent steps, tool calls, specialist
delegations, retries and failures for every item. Before this, none of those nested
events carried the loop's identity, so storage could not recognise them as loop work and
stored every tool result in full, once per item. Each now carries loop_node_id and
item_index, and large fields are previews. The durable copies - item rows, checkpoints,
approvals and write results - stay exact.
"""
import asyncio
import json
import pytest
from cryptography.fernet import Fernet
from backend.app import registry
from backend.app.compiler import compile_workflow
from backend.app.iteration import PREVIEW_CHARS
from backend.app.models import Workflow
from backend.app.storage import Store
from backend.app.tool_service import TransientToolError
from backend.app.worker import Worker
from backend.tests.test_execution_safety import http_connection, jira_connection, jira_payload

NESTED = ('lookup', 'broken', 'helper', 'deep')


def agent_loop(jira='c1', http='c2', items_limit=100, tools=None, approval=False):
    """tickets -> each item -> agent 'send' with attached tools and a specialist."""
    nodes = [{'id': 'input', 'type': 'chat_input'},
             {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
              'config': {'connection_id': jira, 'operation': 'search', 'jql': 'a=b'}},
             {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'},
              'config': {'body': 'send', 'max_items': items_limit}},
             {'id': 'send', 'type': 'agent', 'config': {'provider': 'demo'}},
             {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}]
    edges = [{'id': 'a', 'source': 'input', 'target': 'tickets'}, {'id': 'b', 'source': 'tickets', 'target': 'each'},
             {'id': 'c', 'source': 'each', 'target': 'out'},
             {'id': 'd', 'source': 'each', 'target': 'send', 'kind': 'loop', 'sourceHandle': 'body', 'targetHandle': 'input'}]
    for id, config, owner in (tools or []):
        nodes.append({'id': id, 'type': config.pop('type', 'tool_http'), 'config': config, **({'retry': {'attempts': 1, 'base_delay': 0}} if id == 'lookup' else {})})
        kind = 'agent' if nodes[-1]['type'] == 'agent' else 'tool'
        edges.append({'id': f'{owner}-{id}', 'source': owner if kind == 'agent' else id, 'target': id if kind == 'agent' else owner,
                      'kind': kind, **({'sourceHandle': 'agents', 'targetHandle': 'input'} if kind == 'agent' else {'targetHandle': 'tools'})})
    return Workflow.model_validate({'version': 1, 'name': 'Agent loop', 'nodes': nodes, 'edges': edges})


def full_agent_loop():
    return agent_loop(tools=[('lookup', {'connection_id': 'c2', 'method': 'GET'}, 'send'),
                             ('broken', {'connection_id': 'c2', 'method': 'GET'}, 'send'),
                             ('helper', {'type': 'agent', 'provider': 'demo'}, 'send'),
                             ('deep', {'connection_id': 'c2', 'method': 'GET'}, 'helper')])


ITEM_SCRIPT = [('send', 'lookup'), ('send', 'broken'), ('send', 'helper'), ('helper', 'deep'), ('helper', None), ('send', None)]


def scripted_model(monkeypatch):
    """Each item: the agent calls lookup, broken and helper; helper calls deep; both answer."""
    script = []
    async def model(inputs, config, ctx):
        if not script: script.extend(ITEM_SCRIPT)
        agent, target = script.pop(0)
        assert ctx.node_id == agent, (ctx.node_id, agent)
        action = {'action': 'call', 'target': target, 'input': 'x' * 3000} if target else {'action': 'final', 'text': f'{agent} done'}
        return {'text': json.dumps(action), 'provider': 'demo'}
    monkeypatch.setattr(registry, 'llm_node', model)


@pytest.mark.asyncio
async def test_every_nested_event_carries_the_loop_identity(monkeypatch):
    scripted_model(monkeypatch)
    lookups = {}
    async def platform(action, node_type, settings, text, identity):
        if node_type == 'tool_jira': return {'text': '{}', 'items': json.loads(jira_payload(2))['issues'], 'truncated': False, 'warnings': []}
        if identity.node_id == 'broken': raise ValueError('broken tool')
        if identity.node_id == 'lookup':
            lookups[identity.invocation_id] = lookups.get(identity.invocation_id, 0) + 1
            if lookups[identity.invocation_id] == 1: raise TransientToolError('busy')
        return 'y' * 10000
    events = []
    async def emit(event): events.append(dict(event))
    await compile_workflow(full_agent_loop(), message='go', platform_resolver=platform, emit=emit).graph.ainvoke({'values': {}})
    nested = [e for e in events if e.get('node_id') in NESTED]
    kinds = {(e['node_id'], e['status']) for e in nested}
    for expected in [('lookup', 'retrying'), ('lookup', 'success'), ('broken', 'failed'), ('helper', 'running'),
                     ('helper', 'success'), ('deep', 'running'), ('deep', 'success')]:
        assert expected in kinds, f'{expected} not observed: {sorted(kinds)}'
    missing = [e for e in nested if e.get('loop_node_id') != 'each' or e.get('item_index') not in (0, 1)]
    assert not missing, missing[:2]
    for index in (0, 1):
        assert {e['node_id'] for e in nested if e['item_index'] == index} == set(NESTED)


@pytest.mark.asyncio
async def test_events_emitted_through_the_context_carry_it_too(monkeypatch):
    """Retrieval emits its guard decision through ctx.emit, not the emit it was handed."""
    from backend.app import answerability_guard
    from backend.tests.test_agent_grounding import source
    async def decide(query, sources, node_id): return {'decision': 'allow', 'reason': 'answerable'}
    monkeypatch.setattr(answerability_guard, 'check', decide)
    workflow = agent_loop()
    raw = workflow.model_dump(mode='json')
    body = next(n for n in raw['nodes'] if n['id'] == 'send')
    body.update(type='retrieve', config={'knowledge_base_id': 'kb1', 'mode': 'keyword', 'answerability_guard': True})
    async def platform(action, *args):
        if action == 'tool': return {'text': '{}', 'items': json.loads(jira_payload(2))['issues'], 'truncated': False, 'warnings': []}
        if action == 'kb_retrieve': return {'query': args[1], 'knowledge_base_id': 'kb1', 'version': 1, 'sources': [source(1)]}
        if action == 'record_vector_sources': return None
        raise AssertionError(action)
    events = []
    async def emit(event): events.append(dict(event))
    await compile_workflow(Workflow.model_validate(raw), message='go', platform_resolver=platform, emit=emit).graph.ainvoke({'values': {}})
    decisions = [e for e in events if e.get('kind') == 'answerability']
    assert [(e['loop_node_id'], e['item_index']) for e in decisions] == [('each', 0), ('each', 1)]


# --------------------------------------------------------------------------- storage

def durable(tmp_path, monkeypatch, tool_bytes=40000, items=3):
    store = Store(f'sqlite:///{tmp_path}/events.db', Fernet.generate_key())
    worker = Worker(store)
    calls = []
    async def prepared(p, *args):
        if p['connection']['provider'] == 'jira': return jira_payload(items)
        calls.append(('approved', p['payload'])); return 'sent'
    async def execute(node_type, settings, text, tenant_id='local'):
        calls.append(('executed', text)); return 'y' * tool_bytes
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    monkeypatch.setattr(worker.tools, 'execute', execute)
    return store, worker, calls


def lookup_loop(store):
    http = http_connection(store)
    return agent_loop(jira_connection(store), http, tools=[('lookup', {'connection_id': http, 'method': 'GET'}, 'send')])


def lookup_model(monkeypatch):
    """Call lookup once per item, then answer once its result is in the request."""
    async def model(inputs, config, ctx):
        seen = 'y' * 64 in inputs['prompt']
        action = {'action': 'final', 'text': 'handled'} if seen else {'action': 'call', 'target': 'lookup', 'input': 'details'}
        return {'text': json.dumps(action), 'provider': 'demo'}
    monkeypatch.setattr(registry, 'llm_node', model)


@pytest.mark.asyncio
async def test_stored_loop_events_are_bounded_regardless_of_tool_output(tmp_path, monkeypatch):
    lookup_model(monkeypatch)
    store, worker, calls = durable(tmp_path, monkeypatch)
    row = store.create_run(lookup_loop(store).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    run = store.run(row['id'])
    assert run['status'] == 'success' and len(calls) == 3
    loop_events = [e for e in run['events'] if e.get('loop_node_id') == 'each']
    assert loop_events and all(len(json.dumps(e)) <= 4 * PREVIEW_CHARS for e in loop_events), max(len(json.dumps(e)) for e in loop_events)
    results = [e for e in loop_events if e.get('node_id') == 'lookup' and e['status'] == 'success']
    assert len(results) == 3 and all(e['outputs']['text'] == 'y' * PREVIEW_CHARS and 'outputs.text' in e['preview_of'] for e in results)
    # The durable copies are exact.
    assert [r['value'] for r in run['checkpoints']['each']['results']] == ['handled'] * 3
    assert all(entry['value'] == 'handled' for entry in run['loop_progress']['each'].values())


@pytest.mark.asyncio
async def test_approval_inside_a_looped_agent_keeps_the_exact_payload(tmp_path, monkeypatch):
    """The agent's write input is 5,000 characters. Its events are previews; the approval
    record and the write executed after approval use the exact text."""
    from backend.app.approvals import decide
    store, worker, calls = durable(tmp_path, monkeypatch, items=1)
    http = http_connection(store)
    workflow = agent_loop(jira_connection(store), http, tools=[('post', {'connection_id': http, 'method': 'POST', 'body': '{input}', 'enable_writes': True, 'approval': True}, 'send')])
    payload = 'p' * 5000
    replies = iter([{'action': 'call', 'target': 'post', 'input': payload}, {'action': 'final', 'text': 'posted'}])
    async def model(inputs, config, ctx): return {'text': json.dumps(next(replies)), 'provider': 'demo'}
    monkeypatch.setattr(registry, 'llm_node', model)
    row = store.create_run(workflow.model_dump(mode='json'), 'go', approval_required=True)
    await worker.execute(store.claim_next(worker.owner))
    paused = store.run(row['id'])
    approval = next(a for a in paused['approvals'] if a['status'] == 'pending')
    assert json.dumps(approval['payload']).count('p' * 5000) == 1, 'the approval shows the exact outgoing text'
    running = next(e for e in paused['events'] if e.get('node_id') == 'post' and e['status'] == 'running')
    assert running['inputs']['input'] == payload[:PREVIEW_CHARS] and running['item_index'] == 0
    decide(store, row['id'], 'local', approval['id'], approval['digest'], 'approve')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(row['id'])['status'] == 'success'
    sent = [p for kind, p in calls if kind == 'approved']
    assert len(sent) == 1 and json.dumps(sent[0]).count('p' * 5000) == 1, 'the executed write used the exact text'


@pytest.mark.asyncio
@pytest.mark.parametrize('crash_after', [0, 1])
async def test_resuming_a_looped_agent_ends_like_an_uninterrupted_run(tmp_path, monkeypatch, crash_after):
    lookup_model(monkeypatch)
    (tmp_path / 'a').mkdir(); (tmp_path / 'b').mkdir()
    store, worker, _ = durable(tmp_path / 'a', monkeypatch)
    row = store.create_run(lookup_loop(store).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    baseline = store.run(row['id'])

    store, worker, calls = durable(tmp_path / 'b', monkeypatch)
    original = store.worker_event
    def crash(id, owner, event):
        persisted = original(id, owner, event)
        if event.get('kind') == 'loop_item' and event.get('item_index') == crash_after: raise asyncio.CancelledError()
        return persisted
    monkeypatch.setattr(store, 'worker_event', crash)
    row = store.create_run(lookup_loop(store).model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    monkeypatch.setattr(store, 'worker_event', original)
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    resumed = store.run(row['id'])
    assert resumed['status'] == 'success'
    assert len([c for c in calls if c[0] == 'executed']) == 3, 'no finished item repeats its tool call'
    assert resumed['checkpoints']['each'] == baseline['checkpoints']['each']
    assert (resumed['truncated'], resumed['output']) == (baseline['truncated'], baseline['output'])


# --------------------------------------------------------------------------- the preview policy

def test_fields_other_records_are_derived_from_stay_exact():
    """Guard decision rows and token usage rows are built from the stored event."""
    from backend.app.storage import observed
    reason = 'r' * (3 * PREVIEW_CHARS)
    event = {'loop_node_id': 'each', 'item_index': 0, 'node_id': 'kb', 'status': 'success', 'kind': 'answerability',
             'answerability': {'decision': 'abstain', 'reason': reason}, 'usage': {'prompt_tokens': 7, 'by_model': [{'model': 'm' * 3000}]},
             'outputs': {'text': 't' * (3 * PREVIEW_CHARS)}}
    stored = observed(event)
    assert stored['answerability'] == event['answerability'] and stored['usage'] == event['usage']
    assert stored['outputs']['text'] == 't' * PREVIEW_CHARS and stored['preview_of'] == ['outputs.text']


def test_a_dict_of_many_medium_values_is_bounded_as_a_whole():
    """Shortening each value is not enough: fifty 1,500-character values still add up."""
    from backend.app.storage import observed
    event = {'loop_node_id': 'each', 'item_index': 3, 'node_id': 'send', 'status': 'success',
             'outputs': {f'field{i}': 'v' * 1500 for i in range(50)}}
    stored = observed(event)
    assert isinstance(stored['outputs'], str) and len(stored['outputs']) == PREVIEW_CHARS
    assert 'outputs' in stored['preview_of'] and len(json.dumps(stored)) <= 4 * PREVIEW_CHARS


def test_events_outside_loops_are_untouched():
    from backend.app.storage import observed
    event = {'node_id': 'send', 'status': 'success', 'outputs': {'text': 'x' * (5 * PREVIEW_CHARS)}}
    assert observed(event) is event
