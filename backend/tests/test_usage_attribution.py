"""TA03: each invocation's outcome reports only the usage it incurred.

The cumulative accounting snapshot answers "how much has this run spent"; an outcome
answers "what did this invocation spend in this attempt". Summed outcome rows must equal
the known usage, with no node reporting a descendant's spend and no active child's spend
dropped when cancellation or a refusal unwinds through it.

Store and Worker are real; only the external chat and HTTP endpoints are synthetic. The
compile-level control uses the same usage_rows function the Store uses for metrics.
"""
import asyncio
import json
import threading
import time

import httpx
import pytest
from cryptography.fernet import Fernet
from langgraph.errors import NodeCancelledError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app import approvals, request_safety, tool_service
from backend.app.compiler import compile_workflow
from backend.app.models import Workflow
from backend.app.operator_metrics import RunTokenRecord, usage_rows
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_accounting_durability import PENDING, reopen
from backend.tests.test_iteration import loop_flow
from backend.tests.test_token_budget_recovery import POST

A = {'provider': 'ollama', 'model': 'fixture'}
B = {'provider': 'ollama', 'model': 'fixture-b'}
FINAL = json.dumps({'action': 'final', 'text': 'combined answer'})


def call(target, task='task'):
    return json.dumps({'action': 'call', 'target': target, 'input': task})


def script(monkeypatch, replies, calls, tasks=None):
    """Substitute only the external chat. Each reply is (text or exception, prompt tokens,
    completion tokens); None tokens means the provider reported no usage before the error."""
    from backend.app import providers
    queue = list(replies)
    async def ollama_chat(model, system, prompt, usage=None, **params):
        if not queue: raise AssertionError('unexpected provider call')
        reply, prompt_tokens, completion_tokens = queue.pop(0)
        calls.append(model)
        if tasks is not None: tasks.append(asyncio.current_task())
        if prompt_tokens is not None: providers.record_usage(usage, prompt_tokens, completion_tokens)
        if isinstance(reply, BaseException): raise reply
        return reply
    monkeypatch.setattr(providers, 'ollama_chat', ollama_chat)


def team(depth=1, helper=A):
    """input -> agent -> out. agent delegates to helper; with depth 2, helper delegates to grand."""
    nodes = [{'id': 'input', 'type': 'chat_input'},
             {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': dict(A)},
             {'id': 'out', 'type': 'response', 'inputs': {'text': 'agent.text'}}]
    edges = [{'id': 'a', 'source': 'input', 'target': 'agent'}, {'id': 'b', 'source': 'agent', 'target': 'out'}]
    parent = 'agent'
    for name, config in [('helper', helper), ('grand', A)][:depth]:
        nodes.append({'id': name, 'type': 'agent', 'config': dict(config)})
        edges.append({'id': name, 'source': parent, 'target': name, 'kind': 'agent'})
        parent = name
    return {'version': 1, 'name': 'TA03', 'nodes': nodes, 'edges': edges}


def durable(tmp_path, name='ta03'):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = Store(f'sqlite:///{tmp_path}/{name}.db', Fernet.generate_key())
    store.allow_model('ollama', 'fixture', '', 'local'); store.allow_model('ollama', 'fixture-b', '', 'local')
    return store, Worker(store)


async def attempt(store, worker):
    await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 30)


def metrics(store, id):
    """The operator metric rows for a run: total tokens, measured calls, and per model."""
    with Session(store.engine) as s:
        rows = list(s.scalars(select(RunTokenRecord).where(RunTokenRecord.run_id == id)))
    by_model = {}
    for row in rows:
        total = by_model.setdefault(row.model, [0, 0])
        total[0] += row.prompt_tokens + row.completion_tokens; total[1] += row.calls
    return {'tokens': sum(t for t, _ in by_model.values()), 'calls': sum(c for _, c in by_model.values()),
            'by_model': {model: tuple(total) for model, total in by_model.items()}}


def attributed(events):
    """Reported tokens per node, from the outcomes that carry usage."""
    totals = {}
    for event in events:
        usage = event.get('usage')
        if usage and not event.get('cached'):
            totals[event['node_id']] = totals.get(event['node_id'], 0) + usage['prompt_tokens'] + usage['completion_tokens']
    return totals


def loop_setup(monkeypatch, tmp_path, name):
    """Three Jira issues, each handled by an agent loop body."""
    monkeypatch.setattr(tool_service, 'public_addresses', lambda host, port: ['93.184.216.34'])
    jira = json.dumps({'issues': [{'key': f'PROJ-{i}', 'fields': {'summary': f'Issue {i}', 'status': {'name': 'Open'},
                      'assignee': None, 'updated': ''}} for i in range(3)]})
    async def prepared(_prepared, tenant_id='local'): return jira
    store, worker = durable(tmp_path / name)
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    connection = worker.tools.save_connection({'name': 'jira', 'provider': 'jira', 'endpoint': 'https://jira.example.com',
                                               'username': 'bot', 'secret': 'fixture'})
    document = loop_flow(connection_id=connection['id']).model_dump(mode='json')
    next(n for n in document['nodes'] if n['id'] == 'work').update(type='agent', config=dict(A))
    return store, worker, store.create_run(document, 'go')


def hold(monkeypatch, store, matches):
    """The matching event commits, then its storage thread waits until released."""
    committed, release = threading.Event(), threading.Event()
    original = store.worker_event
    def held(id, owner, event, **kwargs):
        result = original(id, owner, event, **kwargs)
        if result and matches(event) and not committed.is_set():
            committed.set(); assert release.wait(10)
        return result
    monkeypatch.setattr(store, 'worker_event', held)
    return committed, release


# --------------------------------------------------------------------------- U01: original defect

@pytest.mark.asyncio
async def test_u01_completed_children_then_parent_cancelled(tmp_path, monkeypatch):
    """Two calls to one specialist on a second model succeed; the parent is cancelled in its next request."""
    calls = []
    script(monkeypatch, [(call('helper'), 10, 5), ('one', 10, 5), (call('helper'), 10, 5), ('two', 10, 5),
                         (asyncio.CancelledError(), None, None)], calls)
    store, worker = durable(tmp_path)
    try:
        row = store.create_run(team(helper=B), 'go')
        await attempt(store, worker)
        run = store.run(row['id'])
        assert len(calls) == 5 and run['accounting']['tokens_spent'] == 60
        assert metrics(store, row['id']) == {'tokens': 60, 'calls': 4,
                                             'by_model': {'fixture': (30, 2), 'fixture-b': (30, 2)}}
        assert attributed(run['events']) == {'agent': 30, 'helper': 30}
        helpers = [e['invocation_id'] for e in run['events'] if e.get('node_id') == 'helper' and e.get('usage')]
        assert len(set(helpers)) == 2, 'two calls to one specialist are two invocations'
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- U02: active nested child

@pytest.mark.asyncio
async def test_u02_deep_active_child_cancelled_after_known_spend(tmp_path, monkeypatch):
    calls = []
    script(monkeypatch, [(call('helper'), 10, 5), (call('grand'), 10, 5), (asyncio.CancelledError(), 10, 5)], calls)
    store, worker = durable(tmp_path)
    try:
        row = store.create_run(team(depth=2), 'go')
        await attempt(store, worker)
        run = store.run(row['id'])
        assert len(calls) == 3 and run['accounting']['tokens_spent'] == 45
        assert metrics(store, row['id'])['tokens'] == 45 and metrics(store, row['id'])['calls'] == 3
        assert attributed(run['events']) == {'agent': 15, 'helper': 15, 'grand': 15}
        assert {e['node_id'] for e in run['events'] if e.get('status') == 'cancelled'} == {'agent', 'helper', 'grand'}
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- U03: control, no child usage

@pytest.mark.asyncio
@pytest.mark.parametrize('child', ['agent', 'tool'])
async def test_u03_child_cancelled_before_reporting_usage(monkeypatch, child):
    """A child with no reported usage inherits none: neither the parent's spend nor an estimate."""
    calls = []
    script(monkeypatch, [(call('helper'), 10, 5)] + ([(asyncio.CancelledError(), None, None)] if child == 'agent' else []), calls)
    document = team()
    if child == 'tool':
        document['nodes'][3] = {'id': 'helper', 'type': 'tool_python',
                                'config': {'code': 'print(input_text)', 'description': 'Echoes one task.'}}
        document['edges'][2] = {'id': 'helper', 'source': 'helper', 'target': 'agent', 'kind': 'tool', 'targetHandle': 'tools'}
    async def platform(action, *args):
        if action == 'tool': raise asyncio.CancelledError()
        raise AssertionError(action)
    events = []
    async def emit(event): events.append(dict(event))
    graph = compile_workflow(Workflow.model_validate(document), message='go', platform_resolver=platform, emit=emit).graph
    with pytest.raises((asyncio.CancelledError, NodeCancelledError)): await graph.ainvoke({'values': {}})
    rows = [row for event in events for row in usage_rows(event, document)]
    assert sum(r['prompt_tokens'] + r['completion_tokens'] for r in rows) == 15 and sum(r['calls'] for r in rows) == 1
    assert attributed(events) == {'agent': 15}
    assert not [e for e in events if e.get('node_id') == 'helper' and e.get('usage')]


# --------------------------------------------------------------------------- U04: loop body

@pytest.mark.asyncio
async def test_u04_cancelled_loop_item_reports_its_usage(tmp_path, monkeypatch):
    calls = []; script(monkeypatch, [('answer', 10, 5), (asyncio.CancelledError(), 10, 5)], calls)
    store, worker, row = loop_setup(monkeypatch, tmp_path, 'loop')
    try:
        await attempt(store, worker)
        run = store.run(row['id'])
        assert len(calls) == 2 and run['accounting']['tokens_spent'] == 30
        assert metrics(store, row['id'])['tokens'] == 30 and metrics(store, row['id'])['calls'] == 2
        cancelled = [e for e in run['events'] if e.get('node_id') == 'work' and e.get('status') == 'cancelled']
        assert [(e['item_index'], e['usage']['calls']) for e in cancelled] == [(1, 1)]
        assert '1' not in run['loop_progress'].get('each', {}), 'a cancelled item is not reusable'
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- U05: cancellation at a commit

@pytest.mark.asyncio
@pytest.mark.parametrize('held', ['child', 'root'])
async def test_u05_cancellation_while_an_outcome_commit_is_acknowledged(tmp_path, monkeypatch, held):
    """The shielded write settles first. A saved outcome is not reported again as cancelled,
    and a saved child outcome does not stop the parent reporting its own usage."""
    calls, tasks = [], []
    replies = [(call('helper'), 10, 5), ('answer', 10, 5)] + ([(FINAL, 10, 5)] if held == 'root' else [])
    script(monkeypatch, replies, calls, tasks)
    store, worker = durable(tmp_path)
    target = 'helper' if held == 'child' else 'agent'
    committed, release = hold(monkeypatch, store, lambda e: e.get('node_id') == target and e.get('status') == 'success')
    row = store.create_run(team(), 'go')
    task = asyncio.create_task(worker.execute(store.claim_next(worker.owner)))
    try:
        assert await asyncio.to_thread(committed.wait, 10)
        store.request_cancel(row['id']); tasks[-1].cancel(); release.set()
        await asyncio.wait_for(task, 15)
        run = store.run(row['id']); known = 15 * len(calls)
        assert run['accounting']['tokens_spent'] == known
        assert metrics(store, row['id'])['tokens'] == known and metrics(store, row['id'])['calls'] == len(calls)
        assert attributed(run['events']) == ({'agent': 15, 'helper': 15} if held == 'child' else {'agent': 30, 'helper': 15})
        if held == 'root':
            assert 'agent' in run['checkpoints']
            reopen(store, row['id']); await attempt(store, worker)
            resumed = store.run(row['id'])
            assert resumed['status'] == 'success' and len(calls) == 3, 'the saved checkpoint is reused'
            assert metrics(store, row['id'])['tokens'] == 45 and resumed['accounting']['tokens_spent'] == 45
    finally:
        release.set()
        if not task.done(): task.cancel(); await asyncio.gather(task, return_exceptions=True)
        store.engine.dispose()


# --------------------------------------------------------------------------- U06: control, fence refusal

@pytest.mark.asyncio
async def test_u06_fenced_refusal_leaves_saved_usage_unchanged(tmp_path, monkeypatch):
    """Another worker takes the lease as the child's outcome is saved. Unwinding frames may
    attempt their own outcomes; the fence refuses every one."""
    calls = []; script(monkeypatch, [(call('helper'), 10, 5), ('answer', 10, 5), (FINAL, 10, 5)], calls)
    store, worker = durable(tmp_path)
    original = store.worker_event; seen = {}
    def fenced(id, owner, event, **kwargs):
        if 'before' not in seen and event.get('node_id') == 'helper' and event.get('status') == 'success':
            seen['before'] = (metrics(store, id), store.run(id)['accounting']['tokens_spent'])
            with store.engine.begin() as connection:
                connection.execute(text("UPDATE execution_jobs SET owner='other-worker', lease_until=:until WHERE run_id=:id"),
                                   {'until': time.time() + 300, 'id': id})
        result = original(id, owner, event, **kwargs)
        if 'before' in seen: seen.setdefault('results', []).append(result)
        return result
    monkeypatch.setattr(store, 'worker_event', fenced)
    try:
        row = store.create_run(team(), 'go')
        try: await attempt(store, worker)
        except Exception: pass  # How the old worker stops is not under test; what it saved is.
        assert seen['before'] == ({'tokens': 0, 'calls': 0, 'by_model': {}}, 30)
        assert (metrics(store, row['id']), store.run(row['id'])['accounting']['tokens_spent']) == seen['before']
        assert seen['results'] and not any(seen['results']), 'every write after the lease moved was refused'
        assert len(calls) == 2
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- U07: control, item progress

@pytest.mark.asyncio
async def test_u07_item_outcome_saved_before_its_progress(tmp_path, monkeypatch):
    """Cancellation lands after item 0's outcome commits and before its progress row does.
    Resume runs item 0 again; both executions made provider calls, so both count once."""
    calls = []; script(monkeypatch, [('answer', 10, 5)] * 4, calls)
    store, worker, row = loop_setup(monkeypatch, tmp_path, 'progress')
    original = store.worker_event; fired = []
    def cancel_before(id, owner, event, **kwargs):
        if not fired and event.get('kind') == 'loop_item' and event.get('item_index') == 0:
            fired.append(event); raise asyncio.CancelledError()
        return original(id, owner, event, **kwargs)
    monkeypatch.setattr(store, 'worker_event', cancel_before)
    try:
        await attempt(store, worker)
        interrupted = store.run(row['id'])
        assert fired and len(calls) == 1 and not interrupted.get('loop_progress', {}).get('each')
        assert metrics(store, row['id'])['tokens'] == 15
        reopen(store, row['id']); await attempt(store, worker)
        run = store.run(row['id'])
        assert run['status'] == 'success' and len(calls) == 4
        assert run['accounting']['tokens_spent'] == 60
        assert metrics(store, row['id']) == {'tokens': 60, 'calls': 4, 'by_model': {'fixture': (60, 4)}}
        first = [e for e in run['events'] if e.get('node_id') == 'work' and e.get('item_index') == 0 and e.get('usage')]
        assert [e['usage']['calls'] for e in first] == [1, 1]
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- U08: refusals after paid work

@pytest.mark.asyncio
@pytest.mark.parametrize('refusal', ['unsafe', 'uncertain', 'accounting'])
async def test_u08_refusal_after_paid_work(tmp_path, monkeypatch, refusal):
    """Exact usage, the original stop, and no further provider request or fallback."""
    calls = []; store, worker = durable(tmp_path)
    if refusal == 'accounting':
        # Storage fails once the helper has been paid. Its empty delegation is a tool error,
        # so its next request meets the pending-save barrier and is refused.
        script(monkeypatch, [(call('helper'), 10, 5), (call('grand', ''), 10, 5)], calls)
        for name in ('record_accounting', 'reserve_accounting'):
            def flaky(*args, real=getattr(store, name), **kwargs):
                if len(calls) >= 2: raise OSError('synthetic accounting outage')
                return real(*args, **kwargs)
            monkeypatch.setattr(store, name, flaky)
        document, known, expected = team(depth=2), 30, {'agent': 15, 'helper': 15}
    else:
        from backend.app.tool_service import UncertainWriteError
        error = request_safety.UnsafeExecutionError if refusal == 'unsafe' else UncertainWriteError
        script(monkeypatch, [(call('helper'), 10, 5), ('answer', 10, 5), (FINAL, 10, 5)], calls)
        finalize = request_safety.finalize
        async def refuse(ctx, node, inputs, outputs, config):
            if node.id == 'agent': raise error('synthetic refusal after paid work')
            return await finalize(ctx, node, inputs, outputs, config)
        monkeypatch.setattr(request_safety, 'finalize', refuse)
        document, known, expected = team(), 45, {'agent': 30, 'helper': 15}
    try:
        row = store.create_run(document, 'go')
        await attempt(store, worker)
        run = store.run(row['id'])
        assert run['status'] == 'failed' and len(calls) == known // 15
        if refusal == 'accounting': assert run['error'] == PENDING
        assert metrics(store, row['id'])['tokens'] == known and metrics(store, row['id'])['calls'] == known // 15
        assert attributed(run['events']) == expected
        assert 'out' not in run['checkpoints']
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- U09: control, approval resume

@pytest.mark.asyncio
async def test_u09_approval_pause_resume_two_models(tmp_path, monkeypatch):
    """Only new model work in the resumed attempt is reported, per provider/model."""
    monkeypatch.setattr(tool_service, 'public_addresses', lambda *_: ['93.184.216.34'])
    calls, requests = [], []
    def endpoint(request):
        requests.append(request.method); return httpx.Response(200, text='sent')
    store, worker = durable(tmp_path)
    worker.tools.transport = httpx.MockTransport(endpoint)
    connection = worker.tools.save_connection({'name': 'sink', 'provider': 'http', 'endpoint': 'https://example.com'})
    document = team(helper=B)
    document['nodes'].append({'id': 'sink', 'type': 'tool_http', 'config': {'connection_id': connection['id'], **POST}})
    document['edges'].append({'id': 't', 'source': 'sink', 'target': 'agent', 'kind': 'tool', 'targetHandle': 'tools'})
    script(monkeypatch, [(call('helper'), 10, 5), ('answer', 10, 5), (call('sink', 'hello'), 10, 5), (FINAL, 10, 5)], calls)
    try:
        row = store.create_run(document, 'send')
        await attempt(store, worker)
        paused = store.run(row['id']); approval = paused['approvals'][0]
        assert paused['status'] == 'awaiting_approval' and len(calls) == 3
        approvals.decide(store, row['id'], 'local', approval['id'], approval['digest'], 'approve')
        await attempt(store, worker)
        run = store.run(row['id'])
        assert run['status'] == 'success', run.get('error')
        assert requests == ['POST'] and len(calls) == 4
        assert run['accounting']['tokens_spent'] == 60
        assert metrics(store, row['id']) == {'tokens': 60, 'calls': 4,
                                             'by_model': {'fixture': (45, 3), 'fixture-b': (15, 1)}}
        assert attributed(run['events']) == {'agent': 45, 'helper': 15}
    finally: store.engine.dispose()
