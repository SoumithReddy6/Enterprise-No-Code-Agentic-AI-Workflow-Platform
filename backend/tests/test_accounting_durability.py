"""TA02: reusable work and its known token accounting commit together.

A saved node result, loop item or approval frame is never ahead of the run's cumulative
accounting, no writer lowers tokens_spent, a known but unsaved spend blocks the next model
request, and an older run whose past usage cannot be verified starts no new model request.

Store and Worker are real; only the external chat and HTTP endpoints are synthetic. New
helpers are imported inside the tests that need them, so this module still collects on the
baseline and a missing interface is never taken for a reproduction of the defect.
"""
import asyncio
import json
import threading

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from backend.app import approvals, tool_service
from backend.app.compiler import compile_workflow
from backend.app.models import Workflow
from backend.app.storage import LeaseLost, RunRecord, Store
from backend.app.worker import Worker
from backend.tests.test_iteration import loop_flow
from backend.tests.test_token_budget_recovery import (MODEL, SCHEMA, chat, kb_platform, plain_agent,
                                                      query_flow)

LEGACY_MODEL = ('Past token usage cannot be verified for this older run. No further model request was started. '
                'Saved results were kept.')
LEGACY_WRITE = ('Past token usage cannot be verified for this older run. The unstarted approved action was not '
                'sent by this attempt. Saved results were kept.')
PENDING = ('Run accounting could not be saved; no further model request was started. Saved results were kept. '
           'Resume after storage recovers.')
INVALID = ('Saved token accounting is missing or malformed. No further model request was started. '
           'Saved results were kept.')
STALE = 'The accounting write was refused because it would lower tokens_spent.'
REPLY = ('answer', 10, 5)


def unavailable_type(exc):
    """The durability refusal, checked by name so this module collects on the baseline."""
    return type(exc).__name__ == 'AccountingUnavailable' and isinstance(exc, ValueError)


def snapshot(tokens, counter=0):
    budget = {'remaining': 10 - counter, 'counter': counter, 'ceiling': 10} if counter else {}
    return {'action_budget': budget, 'tokens_spent': tokens, 'usage': {}, 'usage_by_invocation': {}}


def llm(id, source):
    return {'id': id, 'type': 'llm', 'inputs': {'prompt': source}, 'config': dict(MODEL)}


def flow(count=1):
    nodes = [{'id': 'input', 'type': 'chat_input'}]
    for i in range(count): nodes.append(llm(f'm{i}', 'input.message' if i == 0 else f'm{i-1}.text'))
    nodes.append({'id': 'out', 'type': 'response', 'inputs': {'text': f'm{count-1}.text'}})
    return {'version': 1, 'name': 'TA02', 'nodes': nodes,
            'edges': [{'id': str(i), 'source': a['id'], 'target': b['id']} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))]}


def durable(tmp_path, name='ta02'):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = Store(f'sqlite:///{tmp_path}/{name}.db', Fernet.generate_key())
    store.allow_model('ollama', 'fixture', '', 'local')
    return store, Worker(store)


def claimed(tmp_path):
    """A run this test owns, with no worker executing it."""
    store, _ = durable(tmp_path)
    row = store.create_run(flow(), 'go'); owner = 'ta02-test'
    assert store.claim_next(owner, lease_seconds=300)['id'] == row['id']
    return store, row['id'], owner


async def attempt(store, worker):
    await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 30)


def reopen(store, id):
    """Queue an interrupted run again; a released run is already queued."""
    if store.run(id)['status'] in ('failed', 'cancelled'): store.resume_run(id)


def fast_saves_fail(monkeypatch, store):
    """The immediate spend save fails as in a storage outage. Action reservations still work."""
    def unavailable(*args, **kwargs): raise OSError('synthetic accounting outage')
    monkeypatch.setattr(store, 'record_accounting', unavailable)


def interrupt_after(monkeypatch, store, matches):
    """The worker stops right after the matching event is committed."""
    original = store.worker_event
    def stop(id, owner, event, **kwargs):
        persisted = original(id, owner, event, **kwargs)
        if persisted and matches(event): raise asyncio.CancelledError()
        return persisted
    monkeypatch.setattr(store, 'worker_event', stop)
    return original


def fresh_success(node_id):
    return lambda e: e.get('node_id') == node_id and e.get('status') == 'success' and not e.get('cached')


def rewrite(store, id, accounting, marker=None, legacy_copy=None):
    """Replace the saved accounting and contract marker, as an older or damaged run has them."""
    with Session(store.engine) as s:
        row = s.get(RunRecord, id); data = {k: v for k, v in row.data.items() if k not in ('accounting_contract', 'accounting')}
        if marker is not None: data['accounting_contract'] = marker
        if legacy_copy is not None: data['accounting'] = legacy_copy
        row.data = data; row.accounting = accounting; s.commit()


def pause_request(invocation='m0#write'):
    exc = approvals.ApprovalPause('m0', 'm0', invocation, {'payload': {'text': 'hello'}, 'connection_id': 'c1'})
    exc.frames = {'m0': {'checkpoint_owner': 'm0', 'step': 1}}
    return exc


@pytest.fixture
def commit_failure():
    """Arms a failure of this thread's next commit, after its final flush.

    The transaction then holds every write it would commit and is genuinely rolled back.
    An exception raised from ConnectionEvents.commit is not used here: SQLAlchemy returns
    that connection to the pool with its DBAPI transaction still open.
    """
    armed = threading.local()
    def fail(session):
        if getattr(armed, 'on', False):
            armed.on = False; session.flush(); raise RuntimeError('synthetic commit failure')
    sa_event.listen(Session, 'before_commit', fail)
    yield armed
    sa_event.remove(Session, 'before_commit', fail)


# --------------------------------------------------------------------------- T01: original defect

@pytest.mark.asyncio
async def test_t01_outage_checkpoint_resume(tmp_path, monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '15')
    calls = []; chat(monkeypatch, [REPLY, REPLY], calls)
    store, worker = durable(tmp_path)
    try:
        row = store.create_run(flow(2), 'go')
        created = store.run(row['id'])
        originals = (store.record_accounting, store.worker_event)
        fast_saves_fail(monkeypatch, store)
        interrupt_after(monkeypatch, store, fresh_success('m0'))
        await attempt(store, worker)
        interrupted = store.run(row['id'])
        assert 'm0' in interrupted['checkpoints']
        assert (interrupted.get('accounting') or {}).get('tokens_spent') == 15, 'the saved answer is ahead of its accounting'
        monkeypatch.setattr(store, 'record_accounting', originals[0]); monkeypatch.setattr(store, 'worker_event', originals[1])
        reopen(store, row['id'])
        await attempt(store, worker)
        resumed = store.run(row['id'])
        assert len(calls) == 1, 'resume reuses m0 and refuses m1 at the limit'
        assert resumed['status'] == 'failed' and 'Run token budget exhausted (15 of 15' in resumed['error']
        assert [e for e in resumed['events'] if e.get('node_id') == 'm0' and e.get('cached')]
        assert resumed['accounting']['tokens_spent'] == 15
        # Checked last, so a baseline failure above shows the original defect, not the new marker.
        assert created['accounting_contract'] == 1
        assert created['accounting'] == {'action_budget': {}, 'tokens_spent': 0, 'usage': {}, 'usage_by_invocation': {}}
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- T02-T05: storage boundaries

@pytest.mark.asyncio
@pytest.mark.parametrize('moment', ['before_commit', 'after_commit'])
async def test_t02_outcome_transaction(tmp_path, monkeypatch, commit_failure, moment):
    calls = []; chat(monkeypatch, [REPLY], calls)
    store, worker = durable(tmp_path)
    try:
        row = store.create_run(flow(1), 'go')
        before = {}
        fast_saves_fail(monkeypatch, store)
        if moment == 'after_commit':
            interrupt_after(monkeypatch, store, fresh_success('m0'))
        else:
            armed = commit_failure; original = store.worker_event
            def failing(id, owner, event, **kwargs):
                armed.on = fresh_success('m0')(event)
                if armed.on: before['accounting'] = store.run(id).get('accounting')
                try: return original(id, owner, event, **kwargs)
                finally: armed.on = False
            monkeypatch.setattr(store, 'worker_event', failing)
        await attempt(store, worker)
        saved = store.run(row['id'])
        assert len(calls) == 1
        if moment == 'before_commit':
            # The rolled-back transaction held both; neither is visible.
            assert 'm0' not in saved['checkpoints']
            assert not [e for e in saved['events'] if e.get('node_id') == 'm0' and e.get('status') == 'success']
            assert 'accounting' in before and saved.get('accounting') == before['accounting']
            assert (saved.get('accounting') or {}).get('tokens_spent', 0) == 0
        else:
            assert 'm0' in saved['checkpoints'] and (saved.get('accounting') or {}).get('tokens_spent') == 15
    finally: store.engine.dispose()


@pytest.mark.parametrize('supplied', ['valid', 'missing'])
def test_t03_pause_snapshot(tmp_path, commit_failure, supplied):
    store, id, owner = claimed(tmp_path)
    try:
        assert store.record_accounting(id, owner, snapshot(0)) is not False
        frozen = snapshot(15, counter=1)
        frozen['usage'] = {'m0': {'calls': 1, 'prompt_tokens': 10, 'completion_tokens': 5}}
        def unchanged():
            run = store.run(id)
            assert run['status'] == 'running' and not run.get('approvals') and not run.get('agent_frames')
            assert run['accounting'] == snapshot(0)
        if supplied == 'missing':
            with pytest.raises(ValueError) as exc: approvals.pause(store, id, owner, pause_request())
            assert unavailable_type(exc.value)
            unchanged(); return
        commit_failure.on = True
        with pytest.raises(RuntimeError, match='synthetic commit failure'):
            approvals.pause(store, id, owner, pause_request(), accounting=frozen)
        unchanged()
        assert approvals.pause(store, id, owner, pause_request(), accounting=frozen) is True
        run = store.run(id)
        assert run['status'] == 'awaiting_approval' and len(run['approvals']) == 1 and run['agent_frames']
        assert run['accounting']['tokens_spent'] == 15 and run['accounting']['usage'] == frozen['usage']
        assert run['accounting']['action_budget']['deferred'] == ['m0#write#0']
    finally: store.engine.dispose()


@pytest.mark.parametrize('writer', ['event', 'fast', 'reserve', 'pause'])
def test_t04_monotonic_writer(tmp_path, writer):
    store, id, owner = claimed(tmp_path)
    def write(value):
        if writer == 'event':
            return store.worker_event(id, owner, {'node_id': 'm0', 'status': 'success', 'outputs': {'text': 'late'}},
                                      accounting=value)
        if writer == 'fast': return store.record_accounting(id, owner, value)
        if writer == 'reserve': return store.reserve_accounting(id, owner, value)
        return approvals.pause(store, id, owner, pause_request(), accounting=value)
    try:
        assert store.record_accounting(id, owner, snapshot(30)) is not False
        events = len(store.run(id)['events'])
        with pytest.raises(ValueError) as exc: write(snapshot(10, counter=1))
        assert unavailable_type(exc.value) and str(exc.value) == STALE
        run = store.run(id)
        assert run['accounting'] == snapshot(30) and len(run['events']) == events
        assert 'm0' not in run['checkpoints'] and not run.get('approvals') and not run.get('agent_frames')
        # An equal total may still change the action counter.
        assert write(snapshot(30, counter=1)) is True
        run = store.run(id)
        assert run['accounting']['tokens_spent'] == 30 and run['accounting']['action_budget']['counter'] == 1
        if writer == 'event': assert run['checkpoints']['m0'] == {'text': 'late'}
        if writer == 'pause': assert run['status'] == 'awaiting_approval'
    finally: store.engine.dispose()


@pytest.mark.asyncio
async def test_t05_cancelled_thread_cannot_regress(tmp_path, monkeypatch):
    store, id, owner = claimed(tmp_path)
    try:
        assert store.record_accounting(id, owner, snapshot(5)) is not False
        late = threading.local(); blocked = threading.Event(); release = threading.Event(); done = threading.Event()
        outcome = {}; original = store._fence
        def fence(s, run_id, run_owner):
            if getattr(late, 'on', False):
                blocked.set(); assert release.wait(10)
            return original(s, run_id, run_owner)
        monkeypatch.setattr(store, '_fence', fence)
        def stale_write():
            late.on = True
            try: outcome['result'] = store.record_accounting(id, owner, snapshot(10))
            except Exception as exc: outcome['error'] = exc
            finally: done.set()
        task = asyncio.create_task(asyncio.to_thread(stale_write))
        assert await asyncio.to_thread(blocked.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert store.worker_event(id, owner, {'node_id': 'm0', 'status': 'success', 'outputs': {'text': 'new'}},
                                  accounting=snapshot(30)) is True
        release.set(); assert await asyncio.to_thread(done.wait, 10)
        run = store.run(id)
        assert run['accounting']['tokens_spent'] == 30 and run['checkpoints']['m0'] == {'text': 'new'}
        assert unavailable_type(outcome.get('error')), outcome
    finally:
        store.engine.dispose()


# --------------------------------------------------------------------------- T06-T09: runtime capture and barrier

@pytest.mark.asyncio
async def test_t06_cancelled_known_usage_snapshot(tmp_path, monkeypatch):
    calls = []; chat(monkeypatch, [REPLY, (asyncio.CancelledError(), 10, 5)], calls)
    store, worker = durable(tmp_path)
    try:
        row = store.create_run(flow(2), 'go')
        await attempt(store, worker)
        run = store.run(row['id'])
        assert len(calls) == 2 and 'm0' in run['checkpoints'] and 'm1' not in run['checkpoints']
        assert [e for e in run['events'] if e.get('node_id') == 'm1' and e.get('status') == 'cancelled']
        assert run['accounting']['tokens_spent'] == 30, 'the cancelled response was paid for'
    finally: store.engine.dispose()
    from backend.app.accounting_state import AccountingDurability
    durability = AccountingDurability(True)
    durability.changed(); first, _ = durability.capture({}); durability.changed()
    durability.acknowledge(first)
    assert durability.pending and durability.acknowledged == first < durability.generation


@pytest.mark.asyncio
async def test_t07_loop_progress_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_service, 'public_addresses', lambda host, port: ['93.184.216.34'])
    jira = json.dumps({'issues': [{'key': f'PROJ-{i}', 'fields': {'summary': f'Issue {i}', 'status': {'name': 'Open'},
                      'assignee': None, 'updated': ''}} for i in range(3)]})
    async def prepared(_prepared, tenant_id='local'): return jira
    def setup(name):
        store, worker = durable(tmp_path / name)
        monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
        connection = worker.tools.save_connection({'name': 'jira', 'provider': 'jira', 'endpoint': 'https://jira.example.com',
                                                   'username': 'bot', 'secret': 'fixture'})
        document = loop_flow(connection_id=connection['id']).model_dump(mode='json')
        next(n for n in document['nodes'] if n['id'] == 'work').update(type='agent', config=dict(MODEL))
        return store, worker, store.create_run(document, 'go')
    calls = []; chat(monkeypatch, [REPLY] * 3, calls)
    control, control_worker, control_row = setup('control')
    await attempt(control, control_worker)
    expected = control.run(control_row['id']); control.engine.dispose()
    assert expected['status'] == 'success' and len(calls) == 3 and expected['accounting']['tokens_spent'] == 45

    calls.clear(); chat(monkeypatch, [REPLY] * 3, calls)
    store, worker, row = setup('resumed')
    try:
        originals = (store.record_accounting, store.worker_event)
        fast_saves_fail(monkeypatch, store)
        interrupt_after(monkeypatch, store, lambda e: e.get('kind') == 'loop_item' and e.get('item_index') == 0)
        await attempt(store, worker)
        interrupted = store.run(row['id'])
        assert list(interrupted['loop_progress']['each']) == ['0'] and len(calls) == 1
        assert interrupted['accounting']['tokens_spent'] == 15, 'the saved item is ahead of its accounting'
        monkeypatch.setattr(store, 'record_accounting', originals[0]); monkeypatch.setattr(store, 'worker_event', originals[1])
        reopen(store, row['id'])
        await attempt(store, worker)
        saved = store.run(row['id'])
        assert saved['status'] == 'success' and len(calls) == 3, 'item 0 is not repeated'
        assert saved['accounting']['tokens_spent'] == 45, 'item 0 tokens are not added again'
        assert saved['accounting']['action_budget']['counter'] == expected['accounting']['action_budget']['counter']
        assert saved['checkpoints']['each']['results'] == expected['checkpoints']['each']['results']
    finally: store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('flush', ['failed', 'recovered'])
async def test_t08_pending_before_repair(tmp_path, monkeypatch, flush):
    order = []; chat(monkeypatch, [('not json', 10, 5), ('{"amount": 1}', 10, 5)], order)
    store, worker = durable(tmp_path)
    try:
        row = store.create_run(plain_agent('fail', output_schema=SCHEMA).model_dump(mode='json'), 'go')
        fast_saves_fail(monkeypatch, store)
        original = store.reserve_accounting
        def reserve(id, owner, value):
            order.append(('reserve', value['tokens_spent'], (value.get('action_budget') or {}).get('counter', 0)))
            if flush == 'failed' and value['tokens_spent']: return False
            return original(id, owner, value)
        monkeypatch.setattr(store, 'reserve_accounting', reserve)
        await attempt(store, worker)
        run = store.run(row['id'])
        models = [i for i, e in enumerate(order) if isinstance(e, str)]
        flushes = [(i, e) for i, e in enumerate(order) if not isinstance(e, str) and e[1] == 15]
        assert len(flushes) == 1 and models[0] < flushes[0][0], 'known spend is flushed before the next request'
        earlier = [e for e in order[:models[0]] if not isinstance(e, str)]
        assert flushes[0][1][2] == (earlier[-1][2] if earlier else 0), 'the flush charges no action'
        if flush == 'failed':
            assert len(models) == 1 and run['status'] == 'failed' and run['error'] == PENDING
        else:
            assert len(models) == 2 and flushes[0][0] < models[1]
            assert run['status'] == 'success', run.get('error')
            assert run['accounting']['tokens_spent'] == 30
    finally: store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['grounded_continue', 'structured_route'])
async def test_t09_durability_not_recoverable(monkeypatch, path):
    from backend.app.accounting_state import AccountingDurability
    calls = []
    if path == 'grounded_continue':
        workflow, platform = query_flow('continue'), kb_platform([])
        replies = [('not json', 10, 5), (json.dumps({'answer': 'Fact [S1]', 'citations': ['S1'], 'abstain': False, 'reason': ''}), 1, 1)]
    else:
        workflow, platform = plain_agent('route', output_schema=SCHEMA), None
        replies = [('not json', 10, 5), ('{"amount": 1}', 1, 1)]
    chat(monkeypatch, replies, calls)
    async def fast_save(snapshot): raise OSError('synthetic accounting outage')
    async def flush(snapshot): return False
    events = []
    async def emit(event): events.append(dict(event))
    graph = compile_workflow(workflow, message='go', platform_resolver=platform, emit=emit, persist_spend=fast_save,
                             persist_accounting=flush, durability=AccountingDurability(True)).graph
    with pytest.raises(ValueError) as exc: await graph.ainvoke({'values': {}})
    assert unavailable_type(exc.value) and str(exc.value) == PENDING
    assert len(calls) == 1, 'no repair request without a durable copy of the known spend'
    assert not [e for e in events if e.get('recovered') or e.get('node_id') in ('out', 'fallback')]


# --------------------------------------------------------------------------- T10-T11: restore trust

LEGACY_USAGE = {'calls': 1, 'prompt_tokens': 10, 'completion_tokens': 5}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['model', 'approved_write', 'cached_and_tool'])
async def test_t10_legacy_trust(tmp_path, monkeypatch, case):
    monkeypatch.setattr(tool_service, 'public_addresses', lambda *_: ['93.184.216.34'])
    calls, requests = [], []
    def endpoint(request):
        requests.append(request.method); return httpx.Response(200, text='status ok')
    store, worker = durable(tmp_path)
    worker.tools.transport = httpx.MockTransport(endpoint)
    connection = worker.tools.save_connection({'name': 'sink', 'provider': 'http', 'endpoint': 'https://example.com'})
    try:
        if case == 'model':
            chat(monkeypatch, [REPLY], calls)
            row = store.create_run(flow(1), 'go')
            rewrite(store, row['id'], {'usage': {'m0': LEGACY_USAGE}})
            await attempt(store, worker)
            run = store.run(row['id'])
            assert run['status'] == 'failed' and run['error'] == LEGACY_MODEL and calls == []
            return
        if case == 'approved_write':
            from backend.tests.test_agent_tools import calc_flow
            from backend.tests.test_token_budget_recovery import POST, agent_config
            chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'calc', 'input': 'hello'}), 10, 5)], calls)
            workflow = calc_flow()
            workflow.nodes[2].type = 'tool_http'; workflow.nodes[2].config = {'connection_id': connection['id'], **POST}
            row = store.create_run(agent_config(workflow, 'route', output_schema=SCHEMA).model_dump(mode='json'), 'send')
            await attempt(store, worker)
            paused = store.run(row['id']); approval = paused['approvals'][0]
            assert paused['status'] == 'awaiting_approval' and len(calls) == 1
            approvals.decide(store, row['id'], 'local', approval['id'], approval['digest'], 'approve')
            rewrite(store, row['id'], {'usage': {'agent': LEGACY_USAGE}})
            await attempt(store, worker)
            run = store.run(row['id'])
            assert run['status'] == 'failed' and run['error'] == LEGACY_WRITE
            assert requests == [] and len(calls) == 1, 'the approved write was not sent'
            assert run['approvals'][0]['status'] == 'approved'
            return
        chat(monkeypatch, [REPLY], calls)
        document = flow(1)
        document['nodes'][-1:] = [{'id': 'get', 'type': 'tool_http', 'inputs': {'input': 'm0.text'},
                                   'config': {'connection_id': connection['id'], 'path': '/status'}},
                                  {'id': 'out', 'type': 'response', 'inputs': {'text': 'get.text'}}]
        document['edges'] = [{'id': 'a', 'source': 'input', 'target': 'm0'}, {'id': 'b', 'source': 'm0', 'target': 'get'},
                             {'id': 'c', 'source': 'get', 'target': 'out'}]
        row = store.create_run(document, 'go')
        original = interrupt_after(monkeypatch, store, fresh_success('m0'))
        await attempt(store, worker)
        assert 'm0' in store.run(row['id'])['checkpoints'] and len(calls) == 1
        monkeypatch.setattr(store, 'worker_event', original)
        rewrite(store, row['id'], {'usage': {'m0': LEGACY_USAGE}})
        calls.clear(); reopen(store, row['id'])
        await attempt(store, worker)
        run = store.run(row['id'])
        assert run['status'] == 'success', run.get('error')
        assert (calls, requests) == ([], ['GET']) and run['output'] == 'status ok'
        assert run['accounting']['tokens_spent'] == 15 and 'accounting_contract' not in run
    finally: store.engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('total', ['missing', True, -1, '15'])
async def test_t11_invalid_trusted_snapshot(tmp_path, monkeypatch, total):
    calls = []; chat(monkeypatch, [REPLY], calls)
    store, worker = durable(tmp_path)
    try:
        row = store.create_run(flow(1), 'go')
        if total == 'missing':
            # A stale legacy copy in the document must not stand in for the missing column.
            rewrite(store, row['id'], None, marker=1, legacy_copy=snapshot(0))
        else:
            rewrite(store, row['id'], {**snapshot(0), 'tokens_spent': total}, marker=1)
        attempted = []; original = store.worker_event
        def record(id, owner, event, **kwargs):
            if event.get('node_id'): attempted.append(event['node_id'])
            return original(id, owner, event, **kwargs)
        monkeypatch.setattr(store, 'worker_event', record)
        await attempt(store, worker)
        run = store.run(row['id'])
        assert run['status'] == 'failed' and run['error'] == INVALID and calls == []
        # Refused at claim: storage also rejects a malformed stored snapshot, but no node may start.
        assert attempted == [], attempted
        assert run.get('accounting') == (None if total == 'missing' else {**snapshot(0), 'tokens_spent': total})
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- T12: lease loss at the fast save

@pytest.mark.asyncio
async def test_t12_fast_save_lease_loss(monkeypatch):
    calls = []; chat(monkeypatch, [REPLY, REPLY], calls)
    document = flow(1)
    document['nodes'][1].update(on_error='route', retry={'attempts': 2, 'base_delay': 0})
    document['nodes'].append({'id': 'fallback', 'type': 'response', 'inputs': {'text': 'input.message'}})
    document['edges'].append({'id': 'err', 'source': 'm0', 'target': 'fallback', 'sourceHandle': 'error'})
    lost = LeaseLost('Execution lease is no longer owned.')
    async def fast_save(snapshot): raise lost
    events = []
    async def emit(event): events.append(dict(event))
    graph = compile_workflow(Workflow.model_validate(document), message='go', emit=emit, persist_spend=fast_save).graph
    with pytest.raises(LeaseLost) as exc: await graph.ainvoke({'values': {}})
    assert exc.value is lost and len(calls) == 1
    assert not [e for e in events if e.get('recovered') or e.get('status') == 'retrying' or e.get('node_id') in ('out', 'fallback')]
