"""A5.3 Part 1 / TA08: accounting commits from parallel branches stay in order.

Design: docs/superpowers/specs/2026-10-24-parallel-branches-design.md (revision 3), section
"Accounting order". One lock per run attempt orders every accounting commit; under it the
writer commits the newest publication (by publication sequence, which also advances for
action-only changes) and acknowledges only that publication's spend generation.

The Store is real. A held Store call blocks its own thread on a threading event, so the
lock holder is inside the database call while the test observes the other branches.
Observable boundaries: a Store method entered or finished, an action debited, model usage
accumulated. A bounded wait that must expire (`never`) is used only to show that a call did
not happen; correct code passes it whatever the timing.

PostgreSQL cases run only with RELAY_TEST_POSTGRES_URL (CI postgres-conformance; locally a
throwaway container), each in a temporary schema.
"""
import asyncio
import contextlib
import os
import threading
import uuid

import pytest
from cryptography.fernet import Fernet

from backend.app import execution_policy, worker as worker_module
from backend.app.accounting_state import AccountingDurability
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_parallel_branches import Models, document, events, join, model, prompt, response, until

ACCOUNTING = ('reserve_accounting', 'record_accounting', 'worker_event')


@contextlib.contextmanager
def database(kind, tmp_path):
    if kind == 'sqlite':
        yield f'sqlite:///{tmp_path}/parallel-accounting.db'
        return
    url = os.environ.get('RELAY_TEST_POSTGRES_URL')
    if not url: pytest.skip('RELAY_TEST_POSTGRES_URL is not set')
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    base = make_url(url); engine = create_engine(base); schema = 'a53_' + uuid.uuid4().hex
    with engine.begin() as connection: connection.execute(text(f'CREATE SCHEMA {schema}'))
    try:
        yield base.update_query_dict({'options': '-csearch_path=' + schema}).render_as_string(hide_password=False)
    finally:
        with engine.begin() as connection: connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


@pytest.fixture(params=['sqlite', 'postgres'])
def backend(request):
    return request.param


@contextlib.contextmanager
def lab(kind, tmp_path):
    with database(kind, tmp_path) as url:
        store = Store(url, Fernet.generate_key())
        store.allow_model('ollama', 'fixture', '')
        try: yield store, Worker(store)
        finally: store.engine.dispose()


class Calls:
    """Records every accounting Store call (enter and exit, in order) and holds one of them.

    `hold(method)` blocks the first call of that method inside its thread until `release()`.
    """
    def __init__(self, store, monkeypatch):
        self.log, self.lock = [], threading.Lock()
        self.target, self.gate, self.entered = None, threading.Event(), threading.Event()
        self.on_enter = None
        for name in ACCOUNTING:
            original = getattr(store, name)
            def wrapped(*args, _name=name, _original=original, **kwargs):
                snapshot = kwargs.get('accounting') if _name == 'worker_event' else args[2]
                with self.lock:
                    index = len(self.log)
                    self.log.append(('enter', _name, index, snapshot))
                    held = self.target == _name and not self.entered.is_set()
                    if held: self.entered.set()
                if self.on_enter is not None and not held: self.on_enter(_name, snapshot)
                if held: assert self.gate.wait(30), 'the held call was never released'
                try: return _original(*args, **kwargs)
                finally:
                    with self.lock: self.log.append(('exit', _name, index, snapshot))
            monkeypatch.setattr(store, name, wrapped)

    def hold(self, method): self.target = method
    def release(self): self.gate.set()

    def entries(self, method=None):
        return [(name, snapshot) for kind, name, _, snapshot in self.log if kind == 'enter' and (method is None or name == method)]

    def after_hold(self):
        """Accounting calls that entered the Store after the held call."""
        with self.lock:
            held = next(i for i, (kind, name, *_) in enumerate(self.log) if kind == 'enter' and name == self.target)
            return [entry for entry in self.log[held + 1:] if entry[0] == 'enter']


async def never(predicate, seconds=1.0):
    """The predicate stays false for a bounded wait; correct code passes whatever the timing."""
    with pytest.raises(TimeoutError): await until(predicate, seconds)


def debits(monkeypatch):
    counted = []
    original = execution_policy._debit_action
    def counting(budget, key=None):
        result = original(budget, key)
        if result: counted.append(key)
        return result
    monkeypatch.setattr(execution_policy, '_debit_action', counting)
    return counted


def durabilities(monkeypatch):
    made = []
    class Recorded(AccountingDurability):
        def __init__(self, trusted):
            super().__init__(trusted); made.append(self)
    monkeypatch.setattr(worker_module, 'AccountingDurability', Recorded)
    return made


def tools(worker, monkeypatch):
    async def execute(*_args, **_kwargs): return 'ok'
    monkeypatch.setattr(worker.tools, 'execute', execute)


def tool(id, source='fork.text'):
    return {'id': id, 'type': 'tool_python', 'inputs': {'input': source}, 'config': {'code': 'print(input_text)', 'description': 'Echoes.'}}


def fork_of(nodes, out):
    ids = [n['id'] for n in nodes]
    return document([prompt('fork'), *nodes, join(), response(out)],
                    [('input', 'fork'), *[('fork', i) for i in ids], *[(i, 'join') for i in ids], ('join', 'out')])


async def start(store, worker, raw):
    row = store.create_run(raw, 'go')
    return row['id'], asyncio.create_task(asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 60))


# --------------------------------------------------------------------------- A01 / A05: action-only order

@pytest.mark.asyncio
async def test_a01_action_only_snapshots_commit_in_order(backend, tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_RUN_BUDGET', '10')
    with lab(backend, tmp_path) as (store, worker):
        tools(worker, monkeypatch); charged = debits(monkeypatch)
        calls = Calls(store, monkeypatch); calls.hold('reserve_accounting')
        id, task = await start(store, worker, fork_of([tool('a1'), tool('b1')], 'a1.text'))
        await until(calls.entered.is_set)
        await until(lambda: len(charged) == 2)  # The second branch has debited and published.
        await never(lambda: calls.after_hold(), 1.0)
        calls.release(); await task
        result = store.run(id)
        assert result['status'] == 'success', result.get('error')
        # The held commit saves the newest publication when it took the lock (1, or 2 if B
        # debited first); the commit after it must save B's action.
        reserved = [snapshot['action_budget']['counter'] for _, snapshot in calls.entries('reserve_accounting')]
        assert len(reserved) == 2 and reserved[1] == 2, reserved
        budget = result['accounting']['action_budget']
        assert budget['counter'] == 2 and budget['remaining'] == 8
        assert sorted(budget['charged']) == ['a1#0', 'b1#0']


# --------------------------------------------------------------------------- A02 / A05: spend and event order

@pytest.mark.asyncio
async def test_a02_spend_and_events_commit_in_order_and_pending_spend_stays_pending(backend, tmp_path, monkeypatch):
    with lab(backend, tmp_path) as (store, worker):
        made = durabilities(monkeypatch)
        models = Models(monkeypatch, tokens={'a': 4, 'b': 6}, gated=('a', 'b'), barrier=('a', 'b'))
        calls = Calls(store, monkeypatch); calls.hold('record_accounting')
        seen = []
        def observe(name, snapshot):
            if not seen: seen.append((made[0].generation, made[0].acknowledged))
        raw = document([prompt('fork'), model('a1', 'fork.text', 'a'), model('b1', 'fork.text', 'b'), join(), response('a1.text')],
                       [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'join'), ('b1', 'join'), ('join', 'out')])
        id, task = await start(store, worker, raw)
        await asyncio.wait_for(asyncio.gather(models.started['a'].wait(), models.started['b'].wait()), 10)
        models.release['a'].set()
        await until(calls.entered.is_set)  # a's spend save holds the lock inside the Store.
        models.release['b'].set()
        await until(lambda: made[0].generation == 2)  # b's usage arrived during a's commit.
        await never(lambda: calls.after_hold(), 1.0)
        calls.on_enter = observe
        calls.release(); await task
        result = store.run(id)
        assert result['status'] == 'success', result.get('error')
        assert seen == [(2, 1)], f'(generation, acknowledged) when the next commit started: {seen}'
        assert result['accounting']['tokens_spent'] == 10
        by_invocation = result['accounting']['usage_by_invocation']
        assert by_invocation['a1']['prompt_tokens'] == 4 and by_invocation['b1']['prompt_tokens'] == 6
        assert events(store, id, 'a1', 'success')[0]['usage']['prompt_tokens'] == 4
        assert events(store, id, 'b1', 'success')[0]['usage']['prompt_tokens'] == 6


# --------------------------------------------------------------------------- A03: cancellation while a commit is held

@pytest.mark.asyncio
async def test_a03_the_lock_is_held_until_a_cancelled_branchs_commit_finishes(tmp_path, monkeypatch):
    with lab('sqlite', tmp_path) as (store, worker):
        tools(worker, monkeypatch)
        calls = Calls(store, monkeypatch); calls.hold('reserve_accounting')
        cancelled = threading.Event()
        original = store.cancel_requested
        def cancel_requested(*args, **kwargs):
            value = original(*args, **kwargs)
            if value: cancelled.set()
            return value
        monkeypatch.setattr(store, 'cancel_requested', cancel_requested)
        id, task = await start(store, worker, fork_of([tool('a1'), tool('b1')], 'a1.text'))
        await until(calls.entered.is_set)
        store.request_cancel(id)
        await until(cancelled.is_set)  # The worker saw the request and cancels the graph.
        await never(lambda: calls.after_hold(), 1.0)
        calls.release(); await task
        log = calls.log
        held = next(e for e in log if e[0] == 'enter' and e[1] == 'reserve_accounting')[2]
        finished = next(i for i, e in enumerate(log) if e[0] == 'exit' and e[2] == held)
        later = [i for i, e in enumerate(log) if e[0] == 'enter' and e[2] > held]
        assert all(i > finished for i in later), 'a commit entered the Store while the cancelled branch still held it'
        assert store.run(id)['status'] == 'cancelled'


# --------------------------------------------------------------------------- A04: a stale queued snapshot

@pytest.mark.asyncio
async def test_a04_the_commit_after_the_holder_carries_the_newest_publication(tmp_path, monkeypatch):
    """B and C publish and wait behind A. Whichever commits next carries the newest snapshot
    (three actions), not its own caller's (two). The held commit itself saves whatever was
    newest when it took the lock, which depends on the schedule, so it is not asserted."""
    monkeypatch.setenv('AGENT_RUN_BUDGET', '10')
    with lab('sqlite', tmp_path) as (store, worker):
        tools(worker, monkeypatch); charged = debits(monkeypatch)
        calls = Calls(store, monkeypatch); calls.hold('reserve_accounting')
        id, task = await start(store, worker, fork_of([tool('a1'), tool('b1'), tool('c1')], 'a1.text'))
        await until(calls.entered.is_set)
        await until(lambda: len(charged) == 3)
        calls.release(); await task
        reserved = [snapshot['action_budget']['counter'] for _, snapshot in calls.entries('reserve_accounting')]
        assert reserved[1] == 3, reserved
        assert store.run(id)['accounting']['action_budget']['counter'] == 3


# --------------------------------------------------------------------------- D02: simultaneous checkpoints

@pytest.mark.asyncio
async def test_d02_eight_branches_commit_checkpoints_at_once(backend, tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_RUN_BUDGET', '20')
    with lab(backend, tmp_path) as (store, worker):
        started, ready = [], asyncio.Event()
        async def execute(*_args, **_kwargs):
            started.append(1)
            if len(started) == 8: ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            return 'ok'
        monkeypatch.setattr(worker.tools, 'execute', execute)
        branches = [tool(f'p{i}') for i in range(8)]
        id, task = await start(store, worker, fork_of(branches, 'p0.text')); await task
        result = store.run(id)
        assert result['status'] == 'success', result.get('error')
        assert {f'p{i}' for i in range(8)} <= set(result['checkpoints'])
        assert result['accounting']['action_budget']['counter'] == 8
