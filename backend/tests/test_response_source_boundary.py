"""R01: a run publishes a Response only after every knowledge source behind it passes the
authoritative source check now (plan: docs/superpowers/plans/2026-10-30-response-source-boundary.md).

The Response lists every passage in the shared run registry and, before this fix, checked
none of them. A resumed loop restores its answer text but not its passages, so the sources
behind reused loop work are checked from the run's saved dependencies, at resume and again
at publication. A failed check is a refusal: it fails the run under any on_error.

Every model call goes through the real llm_node; only the external chat is substituted.
Real-KB cases use real ingestion, removal and verification with the real Worker and SQLite.
"""
import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.app import providers
from backend.app.compiler import compile_workflow, validate_workflow
from backend.app.models import Workflow
from backend.app.platform_validation import kb_errors
from backend.app.registry import REGISTRY, response_node
from backend.app.storage import LeaseLost
from backend.tests.test_agent_grounding import source
from backend.tests.test_agent_team_budget import grounded, team_flow
from backend.tests.test_iteration import jira_connection, loop_flow
from backend.tests.test_kb_services_integration import populated, services  # noqa: F401 (fixture)
from backend.tests.test_kb_workflow import graph
from backend.tests.test_token_budget import agent_with_retrieval
from backend.tests.test_token_budget_recovery import (LIMIT, MODEL, REMOVED, agent_config, chat,
                                                      durable, failed_event, query_loop)

REFUSED = 'No answer was published'


def refused(exc, node_id='out'):
    """The Response refusal: typed as a refusal (checked by name so this module imports on the
    baseline), it names the node, and nothing was published."""
    return (exc is not None and type(exc).__name__ == 'UnsafeExecutionError'
            and str(exc).startswith(f'{node_id}: ') and REFUSED in str(exc))


class Kb:
    """A knowledge platform with `passages` passages. Set `removed` to True to make every later
    check fail, or to a set of chunk ids to fail only the checks that contain one of them."""
    def __init__(self, removed=False, passages=1):
        self.removed, self.verified, self.passages = removed, [], passages
    async def __call__(self, action, *args):
        if action == 'kb_retrieve':
            return {'query': args[1], 'knowledge_base_id': 'kb1', 'version': 1,
                    'sources': [source(i) for i in range(1, self.passages + 1)]}
        if action == 'record_vector_sources': return None
        if action == 'verify_vector_sources':
            self.verified.append(args[0])
            if self.removed is True or self.removed and any(s['id'] in self.removed for s in args[0]):
                raise ValueError(REMOVED)
            return args[0]
        if action == 'tool': return '4'
        raise AssertionError(action)


async def run_graph(workflow, platform, message='go', on_event=None):
    """Run in memory; return (values, exception, events), so a refusal keeps its events."""
    events = []
    async def emit(event):
        events.append(dict(event))
        if on_event: on_event(event)
    try:
        result = await compile_workflow(workflow, message=message, platform_resolver=platform,
                                        emit=emit).graph.ainvoke({'values': {}})
        return result['values'], None, events
    except Exception as exc:
        return None, exc, events


def published(events, node_id='out'):
    return [e for e in events if e.get('node_id') == node_id and e.get('status') == 'success']


def labelled(entry, label):
    return {**entry, 'citation': label, 'cited': False}


def direct(evidence, platform):
    return response_node({'text': 'Fact [S1]'}, None,
                         SimpleNamespace(run={'evidence': evidence}, platform=platform, node_id='out'))


def recorder(calls, fail=None):
    async def platform(action, *args):
        assert action == 'verify_vector_sources', action
        calls.append(args[0])
        if fail is not None: raise fail
        return args[0]
    return platform


async def real_source(services, kb, query='project codename'):
    result = await services.retrieve(kb['id'], query, {'mode': 'keyword'}, 'local')
    assert result['sources'], result
    return result['sources'][0]


def real_platform(services):
    async def platform(action, *args):
        assert action == 'verify_vector_sources', action
        return await services.verify(args[0], 'local')
    return platform


# --------------------------------------------------------------------------- fresh Response

@pytest.mark.asyncio
async def test_u1_a_nested_rejection_publishes_nothing(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'spec0', 'input': 'look it up'}), 1, 0),
                       (json.dumps({'action': 'call', 'target': 'kb0', 'input': 'query'}), 7, 2)], [])
    workflow = team_flow(1)
    for node in workflow.nodes:
        if node.type == 'agent': node.config = {**node.config, **MODEL}
    values, exc, events = await run_graph(workflow, Kb(removed=True), 'facts')
    assert failed_event(events, 'spec0', nested=True)['error'] == REMOVED
    assert published(events, 'agent'), 'the parent agent completes; the refusal is at the Response'
    assert refused(exc), exc
    assert not published(events) and values is None


@pytest.mark.asyncio
@pytest.mark.parametrize('upstream', ['agent', 'query'])
async def test_u3_a_removal_after_an_upstream_check_passed_publishes_nothing(monkeypatch, upstream):
    calls = []; chat(monkeypatch, [(grounded('Bluebird [S1]', ['S1']), 8, 2)], calls)
    if upstream == 'agent':
        workflow = agent_config(graph())
    else:
        workflow = Workflow.model_validate({'version': 1, 'name': 'Query', 'nodes': [
            {'id': 'input', 'type': 'chat_input'},
            {'id': 'query', 'type': 'query', 'inputs': {'query': 'input.message'},
             'config': {'knowledge_base_id': 'kb1', 'mode': 'keyword', **MODEL}},
            {'id': 'out', 'type': 'response', 'inputs': {'text': 'query.text'}}],
            'edges': [{'id': 'a', 'source': 'input', 'target': 'query'}, {'id': 'b', 'source': 'query', 'target': 'out'}]})
    # Two passages; the answer cites only S1. The uncited S2 is removed, so a check of the
    # cited sources alone would publish (N2).
    kb = Kb(passages=2)
    def remove_after_upstream(event):
        if event.get('node_id') == upstream and event.get('status') == 'success': kb.removed = {'chunk2'}
    values, exc, events = await run_graph(workflow, kb, 'What codename?', remove_after_upstream)
    assert published(events, upstream) and len(calls) == 1
    assert refused(exc), exc
    assert not published(events)


@pytest.mark.asyncio
@pytest.mark.parametrize('site', ['step', 'repair'])
async def test_u4_the_budget_fallback_sites_publish_nothing(monkeypatch, site):
    """The agent's budget abstention fails its source check (T14) and routes to a fallback
    Response. That Response reads the shared registry, which still holds the passage."""
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    if site == 'step':
        chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'calc', 'input': 'q'}), 8, 2)], [])
        workflow, message = agent_config(agent_with_retrieval(), 'route'), 'facts'
    else:
        chat(monkeypatch, [('not json', 8, 2)], [])
        workflow, message = agent_config(graph(), 'route'), 'What codename?'
    values, exc, events = await run_graph(workflow, Kb(removed=True), message)
    assert failed_event(events, 'agent')['error'] == REMOVED
    assert refused(exc, 'fallback'), exc
    assert not published(events, 'fallback')


# --------------------------------------------------------------------------- contract

@pytest.mark.asyncio
async def test_u10_listed_sources_without_a_verifier_are_refused():
    with pytest.raises(Exception) as exc:
        await direct([labelled(source(1), 'S1')], None)
    assert type(exc.value).__name__ == 'UnsafeExecutionError'
    assert str(exc.value) == 'out: knowledge source verification is unavailable. No answer was published.'


@pytest.mark.asyncio
async def test_u11_an_empty_list_needs_no_verifier():
    result = await direct([], None)
    assert json.loads(result['sources']) == [] and result['text'] == 'Fact [unsupported reference]'


SHAPES = ['{}', 'null', '"text"', '7', '[1]', '[{"text": "no id"}]']


def fake_services(calls):
    async def call(action, tenant, payload):
        assert action == 'resolve', action
        return {'id': payload['kb_id']}
    async def verify(sources, tenant):
        calls.append(sources); return sources
    return SimpleNamespace(management=SimpleNamespace(call=call), verify=verify)


@pytest.mark.asyncio
@pytest.mark.parametrize('shape', SHAPES)
@pytest.mark.parametrize('container', ['response', 'query', 'loop_item'])
async def test_u12_a_malformed_saved_container_is_never_zero_sources(container, shape):
    calls = []; dependencies = None
    if container == 'response':
        workflow = Workflow.model_validate({'version': 1, 'name': 'Cached', 'nodes': [
            {'id': 'input', 'type': 'chat_input'}, {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
            'edges': [{'id': 'a', 'source': 'input', 'target': 'out'}]})
        checkpoints = {'out': {'text': 'cached answer', 'sources': shape}}
    elif container == 'query':
        workflow = Workflow.model_validate({'version': 1, 'name': 'Query', 'nodes': [
            {'id': 'input', 'type': 'chat_input'},
            {'id': 'query', 'type': 'query', 'inputs': {'query': 'input.message'},
             'config': {'knowledge_base_id': 'kb1', 'mode': 'keyword', **MODEL}},
            {'id': 'out', 'type': 'response', 'inputs': {'text': 'query.text'}}],
            'edges': [{'id': 'a', 'source': 'input', 'target': 'query'}, {'id': 'b', 'source': 'query', 'target': 'out'}]})
        checkpoints = {'query': {'text': 'cached answer', 'sources': shape}}
    else:
        workflow = query_loop()
        checkpoints = {'each': {'results': [], 'failed': '0', 'summary': '{}'}}
        dependencies = {'each:0': json.loads(shape)}
    try:
        errors = await kb_errors(fake_services(calls), workflow, 'local', checkpoints, dependencies)
    except Exception as exc:  # the baseline can crash on some shapes; that is not a named refusal
        raise AssertionError(f'unnamed failure: {exc!r}') from None
    assert len(errors) == 1, errors
    assert [] not in calls


@pytest.mark.asyncio
async def test_u13_exact_duplicates_are_checked_once_and_both_labels_stay(services):
    kb, _, _ = await populated(services)
    chunk = await real_source(services, kb)
    checked = []
    verify = real_platform(services)
    async def platform(action, *args):
        checked.extend(s['id'] for s in args[0])
        return await verify(action, *args)
    result = await direct([labelled(chunk, 'S1'), labelled(chunk, 'S2')], platform)
    assert [s['citation'] for s in json.loads(result['sources'])] == ['S1', 'S2']
    assert len(checked) == len(set(checked)), 'the same record is never checked twice'


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_first', [False, True])
async def test_u14_same_id_with_different_metadata_checks_both_records(services, changed_first):
    kb, _, _ = await populated(services)
    chunk = await real_source(services, kb)
    changed = {**chunk, 'text': chunk['text'] + ' Launch is in October.'}
    pair = [changed, chunk] if changed_first else [chunk, changed]
    with pytest.raises(Exception) as exc:
        await direct([labelled(pair[0], 'S1'), labelled(pair[1], 'S2')], real_platform(services))
    assert refused(exc.value), exc.value


@pytest.mark.asyncio
async def test_u15_more_than_120_sources_are_checked_completely_in_valid_batches():
    evidence = [labelled({**source(i), 'id': f'chunk{i}'}, f'S{i}') for i in range(1, 131)]
    calls = []
    await direct(evidence, recorder(calls))
    assert calls and all(len(batch) <= 120 for batch in calls)
    assert all(len({s['id'] for s in batch}) == len(batch) for batch in calls)
    checked = [json.dumps(s, sort_keys=True) for batch in calls for s in batch]
    assert sorted(checked) == sorted(json.dumps(source(i), sort_keys=True) for i in range(1, 131))


@pytest.mark.asyncio
async def test_u16_each_distinct_source_is_checked_exactly_once():
    calls = []
    await direct([labelled(source(1), 'S1'), labelled(source(2), 'S2'), labelled(source(1), 'S3')], recorder(calls))
    assert sorted(s['id'] for batch in calls for s in batch) == ['chunk1', 'chunk2']
    assert all('citation' not in s and 'cited' not in s for batch in calls for s in batch)


@pytest.mark.asyncio
async def test_u17_a_fresh_success_publishes_exactly_as_before(monkeypatch):
    chat(monkeypatch, [(grounded('Bluebird [S1]', ['S1']), 8, 2)], [])
    values, exc, _ = await run_graph(agent_config(graph()), Kb(), 'What codename?')
    assert exc is None, exc
    assert values['out']['text'] == 'Bluebird [S1]'
    assert json.loads(values['out']['sources']) == [{**source(1), 'citation': 'S1', 'cited': True}]


@pytest.mark.asyncio
async def test_u18_no_evidence_makes_no_check():
    calls = []
    await direct([], recorder(calls))
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [LeaseLost('lease'), asyncio.CancelledError()])
async def test_u19_control_flow_from_the_check_propagates_unchanged(error):
    with pytest.raises(type(error)):
        await direct([labelled(source(1), 'S1')], recorder([], fail=error))


# --------------------------------------------------------------------------- real KB and Worker

def other_document(services, kb):
    async def upload(ingester):
        await services.management.call('upload', 'local', {'kb_id': kb['id'], 'filename': 'other.txt',
            'content_b64': base64.b64encode(b'Unrelated handbook material.').decode(), 'idempotency_key': 'other'})
        await ingester.execute(await services.management.call('claim', 'system', {}))
    return upload


def remover(services, kb, doc, timeline):
    def remove():
        services.management.call_sync('remove_document', 'local', {'kb_id': kb['id'], 'document_id': doc['id']})
        timeline.append('removed')
    return remove


def watch_verifier(monkeypatch, services, phase, timeline):
    """Record each check as (node running at the time, document ids) on the real verifier."""
    original = services.verify
    async def checked(sources, tenant):
        timeline.append((phase.get('node'), [s['document_id'] for s in sources]))
        return await original(sources, tenant)
    monkeypatch.setattr(services, 'verify', checked)


def loop_workflow(store, kb):
    """Input -> Jira list -> For each (Query body) -> Prompt formats each.results -> Response."""
    document = loop_flow(connection_id=jira_connection(store)).model_dump(mode='json')
    next(n for n in document['nodes'] if n['id'] == 'work').update(
        type='query', config={'knowledge_base_id': kb['id'], 'mode': 'keyword', **MODEL})
    document['nodes'].insert(-1, {'id': 'render', 'type': 'prompt', 'config': {'template': '{message}'},
                                  'inputs': {'message': 'each.results'}})
    next(n for n in document['nodes'] if n['id'] == 'out')['inputs'] = {'text': 'render.text'}
    next(e for e in document['edges'] if e['source'] == 'each' and e['target'] == 'out')['target'] = 'render'
    document['edges'].append({'id': 'render_out', 'source': 'render', 'target': 'out'})
    workflow = Workflow.model_validate(document)
    assert validate_workflow(workflow) == []
    return workflow


def passage_model(monkeypatch, calls):
    """The Query answers from its single passage; each call records the passage's document."""
    async def ollama_chat(model, system, prompt, usage=None, **params):
        passages = json.loads(prompt)['evidence']['passages']
        assert len(passages) == 1, passages
        calls.append(passages[0]['document_id'])
        providers.record_usage(usage, 8, 2)
        return grounded(passages[0]['text'] + f" [{passages[0]['citation']}]", [passages[0]['citation']])
    monkeypatch.setattr(providers, 'ollama_chat', ollama_chat)


async def loop_case(tmp_path, monkeypatch, services, crash, remove=None, two_items=False, store=None, resume=True):
    """crash: 'loop' (after the loop checkpoint), 'item' (after item 0's progress row) or 'out'
    (after the Response checkpoint). remove: 'before_resume', 'after_prompt' (in the resumed
    attempt, after the Prompt's success event) or None."""
    from backend.app import tool_service
    from backend.app.worker import Worker
    kb, doc, ingester = await populated(services)
    await other_document(services, kb)(ingester)
    if store is None: store, _ = durable(tmp_path, monkeypatch, 'loop')
    store.allow_model('ollama', 'fixture', '', 'local')
    worker = Worker(store); worker.kbs = services
    monkeypatch.setattr(tool_service, 'public_addresses', lambda *_: ['93.184.216.34'])
    issues = [{'key': 'P-1', 'fields': {'summary': 'project codename', 'status': {'name': 'Open'}, 'assignee': None, 'updated': ''}}]
    if two_items:
        issues.append({'key': 'P-2', 'fields': {'summary': 'Unrelated handbook material', 'status': {'name': 'Open'}, 'assignee': None, 'updated': ''}})
    worker.tools.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={'issues': issues}))
    calls, timeline, phase = [], [], {}
    passage_model(monkeypatch, calls)
    watch_verifier(monkeypatch, services, phase, timeline)
    remove_document = remover(services, kb, doc, timeline)
    original = store.worker_event
    def first(id, owner, event):
        if event.get('status') == 'running': phase['node'] = event.get('node_id')
        saved = original(id, owner, event)
        if crash == 'item' and event.get('kind') == 'loop_item': raise asyncio.CancelledError()
        if event.get('status') == 'success' and not event.get('transient') and event.get('node_id') == {'loop': 'each', 'out': 'out'}.get(crash):
            raise asyncio.CancelledError()
        return saved
    monkeypatch.setattr(store, 'worker_event', first)
    row = store.create_run(loop_workflow(store, kb).model_dump(mode='json'), 'project codename')
    await worker.execute(store.claim_next(worker.owner))
    before = store.run(row['id'])
    assert before['status'] != 'success' and 'each:0' in before['vector_dependencies']
    assert ('each' in before['checkpoints']) == (crash != 'item')
    if crash == 'item': assert '0' in before['loop_progress']['each']
    if remove == 'before_resume': remove_document()
    def second(id, owner, event):
        if event.get('status') == 'running': phase['node'] = event.get('node_id')
        saved = original(id, owner, event)
        if remove == 'after_prompt' and event.get('node_id') == 'render' and event.get('status') == 'success':
            remove_document()
        return saved
    monkeypatch.setattr(store, 'worker_event', second)
    resumed_from = len(timeline)
    if resume:
        store.resume_run(row['id'])
        await worker.execute(store.claim_next(worker.owner))
    final = store.run(row['id'])
    checks = [t for t in timeline[resumed_from:] if t != 'removed']
    after = timeline[timeline.index('removed') + 1:] if 'removed' in timeline else []
    return SimpleNamespace(run=final, doc=doc, calls=calls, store=store, id=row['id'],
                           out_checks=[docs for node, docs in checks if node == 'out'],
                           checks_after_removal=[t for t in after if t != 'removed'])


def no_output(case):
    assert case.run['status'] == 'failed', case.run.get('error')
    assert not case.run.get('output')
    assert 'out' not in case.run['checkpoints']


@pytest.mark.asyncio
async def test_u2_a_specialist_rejection_with_a_real_kb_publishes_nothing(tmp_path, monkeypatch, services):
    kb, doc, _ = await populated(services)
    calls = []
    async def model(*args, usage=None, **kwargs):
        calls.append(1)
        if len(calls) == 1: text, tokens = json.dumps({'action': 'call', 'target': 'spec0', 'input': 'project codename'}), 1
        elif len(calls) == 2: text, tokens = json.dumps({'action': 'call', 'target': 'kb0', 'input': 'project codename'}), 1
        elif len(calls) == 3:
            await services.management.call('remove_document', 'local', {'kb_id': kb['id'], 'document_id': doc['id']})
            text, tokens = 'not JSON', 8
        else: raise AssertionError('unexpected model call')
        providers.record_usage(usage, tokens, 0)
        return text
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    monkeypatch.setattr(providers, 'ollama_chat', model)
    store, worker = durable(tmp_path, monkeypatch)
    try:
        worker.kbs = services
        workflow = team_flow(1)
        for node in workflow.nodes:
            if node.type == 'agent': node.config = {**node.config, **MODEL}
            elif node.type == 'retrieve': node.config['knowledge_base_id'] = kb['id']
        row = store.create_run(workflow.model_dump(mode='json'), 'project codename')
        await worker.execute(store.claim_next(worker.owner))
        run = store.run(row['id'])
        assert len(calls) == 3 and json.loads(run['checkpoints']['agent']['sources']) == []
        assert run['status'] == 'failed' and run['error'].startswith('out: ') and REFUSED in run['error']
        assert 'out' not in run['checkpoints'] and not run.get('output')
    finally: store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['fail', 'continue', 'route'])
async def test_u20_the_refusal_fails_the_run_by_name_under_every_on_error(tmp_path, monkeypatch, services, policy):
    kb, doc, ingester = await populated(services)
    await other_document(services, kb)(ingester)
    chat(monkeypatch, [(grounded('Bluebird is the project codename. [S1]', ['S1']), 8, 2)], [])
    store, worker = durable(tmp_path, monkeypatch)
    try:
        worker.kbs = services
        workflow = graph()
        next(n for n in workflow.nodes if n.id == 'retrieve').config['knowledge_base_id'] = kb['id']
        next(n for n in workflow.nodes if n.id == 'agent').config = dict(MODEL)
        next(n for n in workflow.nodes if n.id == 'out').on_error = policy
        assert validate_workflow(workflow) == []
        timeline = []; remove_document = remover(services, kb, doc, timeline)
        original = store.worker_event
        def event(id, owner, payload):
            saved = original(id, owner, payload)
            if payload.get('node_id') == 'agent' and payload.get('status') == 'success': remove_document()
            return saved
        monkeypatch.setattr(store, 'worker_event', event)
        row = store.create_run(workflow.model_dump(mode='json'), 'project codename')
        await worker.execute(store.claim_next(worker.owner))
        run = store.run(row['id'])
        assert timeline == ['removed']
        assert run['status'] == 'failed' and run['error'].startswith('out: ') and REFUSED in run['error'], run.get('error')
        assert 'out' not in run['checkpoints'] and not run.get('output')
        assert not [e for e in run['events'] if e.get('node_id') == 'out' and e.get('status') == 'retrying']
    finally: store.engine.dispose()


@pytest.mark.asyncio
async def test_u21_a_response_without_text_is_a_named_failure(tmp_path, monkeypatch):
    async def broken(inputs, config, ctx): raise ValueError('broken response')
    monkeypatch.setattr(REGISTRY['response'], 'handler', broken)
    store, worker = durable(tmp_path, monkeypatch)
    try:
        workflow = Workflow.model_validate({'version': 1, 'name': 'Recovered', 'nodes': [
            {'id': 'input', 'type': 'chat_input'},
            {'id': 'out', 'type': 'response', 'on_error': 'continue', 'inputs': {'text': 'input.message'}}],
            'edges': [{'id': 'a', 'source': 'input', 'target': 'out'}]})
        row = store.create_run(workflow.model_dump(mode='json'), 'hello')
        await worker.execute(store.claim_next(worker.owner))
        run = store.run(row['id'])
        assert run['status'] == 'failed' and run['error'] == 'out: the Response produced no answer.', run.get('error')
    finally: store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', ['loop', 'item'])
async def test_u5_u6_a_removal_before_resume_refuses_reused_loop_work(tmp_path, monkeypatch, services, crash):
    case = await loop_case(tmp_path, monkeypatch, services, crash, remove='before_resume')
    try:
        no_output(case)
        assert 'each:0: saved knowledge sources are unavailable or removed' in case.run['error'], case.run['error']
        assert case.calls == [case.doc['id']], 'the model call for item 0 is not repeated'
    finally: case.store.engine.dispose()


@pytest.mark.asyncio
async def test_u7_a_removal_before_resume_refuses_a_saved_response(tmp_path, monkeypatch, services):
    case = await loop_case(tmp_path, monkeypatch, services, 'out', remove='before_resume')
    try:
        assert case.run['status'] == 'failed' and 'out: saved knowledge sources are unavailable or removed' in case.run['error'], case.run.get('error')
        assert not case.run.get('output') and case.calls == [case.doc['id']]
    finally: case.store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', ['loop', 'item'])  # U5's and U6's states
async def test_u8_the_resume_endpoint_refuses_reused_loop_work(tmp_path, monkeypatch, services, crash):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from backend.app.main import create_app
    app = create_app(database_url=f'sqlite:///{tmp_path}/api.db', encryption_key=Fernet.generate_key(),
                     auth_enabled=False, embedded_worker=False, knowledge_services=services)
    with TestClient(app) as client:
        case = await loop_case(tmp_path, monkeypatch, services, crash, remove='before_resume',
                               store=client.app.state.store, resume=False)
        response = client.post(f'/api/runs/{case.id}/resume')
        assert response.status_code == 422, response.text
        assert any(d.startswith('each:0: saved knowledge sources are unavailable') for d in response.json()['detail'])


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', ['loop', 'item', 'out'])
async def test_u9_an_unchanged_source_resumes_without_repeating_a_model_call(tmp_path, monkeypatch, services, crash):
    case = await loop_case(tmp_path, monkeypatch, services, crash)
    try:
        assert case.run['status'] == 'success', case.run.get('error')
        assert 'Bluebird' in case.run['output'] and case.calls == [case.doc['id']]
        # Existing behavior, separate from R01: a restored loop's passage is not put back in
        # the shared registry, so its label shows as unsupported after a loop-checkpoint resume.
        if crash == 'loop': assert '[unsupported reference]' in case.run['output']
    finally: case.store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', ['loop', 'item'])
async def test_u22_u23_a_removal_after_the_resume_check_refuses_publication(tmp_path, monkeypatch, services, crash):
    two = crash == 'item'
    case = await loop_case(tmp_path, monkeypatch, services, crash, remove='after_prompt', two_items=two)
    try:
        no_output(case)
        assert case.run['error'].startswith('out: ') and REFUSED in case.run['error'], case.run['error']
        assert case.checks_after_removal, 'the Response checks after the removal'
        if two:
            assert len(case.calls) == 2 and case.calls[0] == case.doc['id'] and case.calls[1] != case.doc['id']
        else:
            assert case.calls == [case.doc['id']]
    finally: case.store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', ['loop', 'item'])
async def test_u24_the_response_checks_the_reused_items_saved_source(tmp_path, monkeypatch, services, crash):
    two = crash == 'item'
    case = await loop_case(tmp_path, monkeypatch, services, crash, two_items=two)
    try:
        assert case.run['status'] == 'success', case.run.get('error')
        assert len(case.calls) == (2 if two else 1) and case.calls[0] == case.doc['id']
        assert any(case.doc['id'] in docs for docs in case.out_checks), case.out_checks
    finally: case.store.engine.dispose()
