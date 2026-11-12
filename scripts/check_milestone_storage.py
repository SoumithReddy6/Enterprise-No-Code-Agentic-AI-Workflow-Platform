"""Milestone audit: isolated PostgreSQL, process faults, migrations and storage costs.

External writes go only to the scenario HTTP receiver. PostgreSQL schemas and SQLite
databases are disposable. A failing check stays failed in the retained report.
"""
import argparse
import asyncio
import contextlib
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from backend.app.storage import Store
from backend.app import schema
from scripts.check_production_scenarios import Lab, ROOT

PG = 'postgresql+psycopg://relay:relay_local@127.0.0.1:55432/relay'


@contextlib.contextmanager
def database(kind):
    if kind == 'sqlite':
        with tempfile.TemporaryDirectory(prefix='relay-milestone-sqlite-') as directory:
            yield f'sqlite:///{directory}/audit.db'
        return
    base = make_url(os.environ.get('MILESTONE_POSTGRES_URL', PG))
    engine = create_engine(base)
    name = 'milestone_' + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {name}'))
    try:
        yield base.update_query_dict({'options': '-csearch_path=' + name}).render_as_string(hide_password=False)
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {name} CASCADE'))
        engine.dispose()


def wait_for(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.025)
    raise AssertionError('Condition did not become true before its deadline')


@contextlib.contextmanager
def deployed(url, output, env=None):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='relay-milestone-process-') as directory:
        lab = Lab(directory, output)
        lab.env.update(DATABASE_URL=url, AGENT_RUN_BUDGET='200', **(env or {}))
        try:
            # boot starts a worker; pause queue work while the fixtures are created.
            lab.boot()
            lab.worker.terminate(); lab.worker.wait(timeout=10)
            key = lab.env['CREDENTIAL_ENCRYPTION_KEY'].encode()
            store = Store(url, key)
            yield lab, store
        finally:
            if 'store' in locals():
                store.engine.dispose()
            lab.close()


def start_worker(lab, mode='serve', *flags):
    log = open(lab.output / f'milestone-worker-{len(lab.processes)}.log', 'w')
    lab.logs.append(log)
    process = subprocess.Popen([sys.executable, '-m', 'scripts.scenarios.milestone_worker', mode, *flags], cwd=ROOT, env=lab.env, stdout=log, stderr=subprocess.STDOUT)
    lab.processes.append(process)
    return process


def migrations():
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    with database('postgres') as url:
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                schema.upgrade(connection, '0001_baseline')
                assert schema.current_revision(connection) == '0001_baseline'
                schema.upgrade(connection)
                assert schema.current_revision(connection) == schema.head_revision()
                assert compare_metadata(MigrationContext.configure(connection), schema.target_metadata) == []
                command.downgrade(schema._config(connection), '0001_baseline')
                schema.upgrade(connection)
                assert compare_metadata(MigrationContext.configure(connection), schema.target_metadata) == []
            return {'upgrade_downgrade_upgrade': True, 'metadata_diff': []}
        finally:
            engine.dispose()


def concurrent_workers(output):
    with database('postgres') as url, deployed(url, output) as (lab, store):
        runs = [(lab.submit(lab.batch(f'/deliver/concurrent-{i}', n=2, approval=False)), f'/deliver/concurrent-{i}') for i in range(50)]
        workers = [start_worker(lab) for _ in range(4)]
        for identifier, path in runs:
            run = lab.wait(identifier, timeout=120)
            assert run['status'] == 'success', run.get('error')
            assert len(run['loop_progress']['each']) == 2
            assert run['accounting']['action_budget']['counter'] == 3, run['accounting']
            deliveries = [call for call in lab.provider.calls if call['path'] == path]
            assert len(deliveries) == 2
            assert len({call['body'] for call in deliveries}) == 2, 'Duplicate item delivery'
        claims = []
        for process in workers:
            process.terminate(); process.wait(timeout=10)
        for path in output.glob('milestone-worker-*.log'):
            for line in path.read_text().splitlines():
                try: row = json.loads(line)
                except json.JSONDecodeError: continue
                if row.get('event') == 'job.lease_acquired': claims.append(row['run_id'])
        identifiers = {identifier for identifier, _ in runs}
        assert len(claims) == 50 and set(claims) == identifiers and len(set(claims)) == 50
        (output / 'receiver.json').write_text(json.dumps(lab.provider.calls, indent=2))
        return {'processes': 4, 'worker_slots_per_process': 1, 'runs': 50, 'claims': len(claims), 'deliveries': 100, 'loop_rows': 100, 'action_counter_each': 3}


def stale_lease(output):
    with database('postgres') as url, deployed(url, output) as (lab, store):
        from scripts.check_production_scenarios import flow_nodes, input_node, response
        identifier = lab.submit(flow_nodes([input_node(), response()]))
        stale = start_worker(lab, 'stale')
        marker = lab.directory / 'stale-ready.json'
        wait_for(marker.exists)
        assert json.loads(marker.read_text())['run_id'] == identifier
        os.kill(stale.pid, signal.SIGSTOP)
        try:
            time.sleep(1.2)  # Real expiry of the probe's explicitly one-second lease.
            replacement = start_worker(lab)
            run = lab.wait(identifier)
            assert run['status'] == 'success'
            before = store.run(identifier, lab.tenant)
            (lab.directory / 'stale-release').touch()
        finally:
            os.kill(stale.pid, signal.SIGCONT)
        stale.wait(timeout=20)
        assert stale.returncode == 0
        refused = json.loads((lab.directory / 'stale-result.json').read_text())
        assert all(refused.values()), refused
        assert store.run(identifier, lab.tenant) == before
        assert store.completed_action(identifier, 'stale-action') is None
        return {'sigstop_sigcont': True, 'replacement_completed': True, 'refused_operations': refused}


def killed_loop(output):
    with database('postgres') as url, deployed(url, output) as (lab, store):
        path = '/hold/milestone-read'
        identifier = lab.submit(lab.batch(path, n=3, write=False))
        worker = start_worker(lab)
        wait_for(lambda: lab.provider.count(path) >= 2)
        os.kill(worker.pid, signal.SIGKILL); worker.wait(timeout=10)
        prior = store.run(identifier, lab.tenant)
        assert len(prior['loop_progress']['each']) == 1
        # Wait for the real default 30-second lease, without mutating the database.
        start_worker(lab)
        resumed = lab.wait(identifier, timeout=60)
        assert resumed['status'] == 'success', resumed.get('error')
        restored = resumed['checkpoints']['each']['results']
        assert len(restored) == 3
        completed_body = next(call['body'] for call in lab.provider.calls if call['path'] == path)
        assert sum(call['path'] == path and call['body'] == completed_body for call in lab.provider.calls) == 1
        baseline_path = '/read/milestone-baseline'
        baseline = lab.wait(lab.submit(lab.batch(baseline_path, n=3, write=False)))
        assert resumed['checkpoints']['each'] == baseline['checkpoints']['each']
        assert resumed['output'] == baseline['output']
        assert resumed['accounting']['action_budget']['counter'] == 5  # Jira + 3 reads + repeated in-flight read.
        (output / 'receiver.json').write_text(json.dumps(lab.provider.calls, indent=2))
        return {'sigkill': True, 'committed_items_repeated': 0, 'in_flight_read_repeated': 1, 'checkpoint_equals_uninterrupted': True, 'actions_including_retry': 5}


ACCOUNTING_MODEL = 'accounting-fixture'


def accounting_flow():
    nodes = [{'id': 'input', 'type': 'chat_input'}]
    for i in range(2):
        nodes.append({'id': f'm{i}', 'type': 'llm', 'inputs': {'prompt': 'input.message' if i == 0 else 'm0.text'},
                      'config': {'provider': 'ollama', 'model': ACCOUNTING_MODEL}})
    nodes.append({'id': 'out', 'type': 'response', 'inputs': {'text': 'm1.text'}})
    return {'version': 1, 'name': 'TA02 accounting commit', 'nodes': nodes,
            'edges': [{'id': str(i), 'source': a['id'], 'target': b['id']} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))]}


def accounting_commit(kind, moment, output):
    """TA02: SIGKILL at m0's outcome commit with fast saves failing (limit 15, 10+5 per call).

    before_commit: neither m0's checkpoint nor its 15 tokens is committed. The response was
    never saved, so the resumed run pays for m0 again; that remains the documented boundary.
    after_commit: both survive; the resumed run reuses m0 and refuses m1 without a request.
    """
    from scripts.scenarios.request_routing import ModelFixture
    model = ModelFixture(prompt_tokens=10, completion_tokens=5)
    try:
        with database(kind) as url, deployed(url, output, {'OLLAMA_BASE_URL': model.url, 'RELAY_RUN_TOKEN_LIMIT': '15'}) as (lab, store):
            response = lab.client.post('/api/models', json={'provider': 'ollama', 'model': ACCOUNTING_MODEL})
            assert response.status_code == 201, response.text
            model.script('first answer', 'second answer', 'third answer')
            identifier = lab.submit(accounting_flow(), 'synthetic accounting probe')
            held = start_worker(lab, 'serve', '--accounting-barrier', moment, '--fail-fast-saves')
            barrier = lab.directory / 'accounting-barrier'
            wait_for(barrier.exists, 60)
            assert barrier.read_text() == moment and len(model.calls) == 1
            os.kill(held.pid, signal.SIGKILL); held.wait(timeout=10)
            prior = store.run(identifier, lab.tenant)
            m0_events = [e for e in prior['events'] if e.get('node_id') == 'm0' and e.get('status') == 'success']
            if moment == 'before_commit':
                assert 'm0' not in prior['checkpoints'] and not m0_events, 'an uncommitted outcome is visible'
                assert prior['accounting']['tokens_spent'] == 0, prior['accounting']
            else:
                assert 'm0' in prior['checkpoints'] and len(m0_events) == 1
                assert prior['accounting']['tokens_spent'] == 15, prior['accounting']
            # Wait for the real default 30-second lease, without mutating the database.
            start_worker(lab)
            resumed = lab.wait(identifier, timeout=90)
            assert resumed['status'] == 'failed' and 'Run token budget exhausted (15 of 15' in resumed['error'], resumed.get('error')
            assert resumed['accounting']['tokens_spent'] == 15
            calls = 2 if moment == 'before_commit' else 1
            assert len(model.calls) == calls, len(model.calls)
            assert lab.provider.calls == []
            return {'database': kind, 'moment': moment, 'sigkill': True, 'fast_saves': 'failing',
                    'prior_checkpoint': 'm0' in prior['checkpoints'], 'prior_tokens_spent': prior['accounting']['tokens_spent'],
                    'model_calls': calls, 'final_tokens_spent': 15, 'resumed_status': 'failed (token limit before m1)'}
    finally:
        model.close()


class BranchModels:
    """Ollama fixture for the A5.3 D01 rows: branch A answers at once; branch B's first
    request is held open, so it is outstanding when the worker is killed."""
    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.calls, self.lock, self.release = [], threading.Lock(), threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                model = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['model']
                with owner.lock:
                    owner.calls.append(model)
                    held = model == 'branch-b' and owner.calls.count('branch-b') == 1
                if held: owner.release.wait(120)
                body = json.dumps({'message': {'role': 'assistant', 'content': f'answer {model}'},
                                   'prompt_eval_count': 10, 'eval_count': 5}).encode()
                try:
                    self.send_response(200); self.send_header('Content-Length', str(len(body)))
                    self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
                except OSError: pass  # The killed worker's connection is gone.
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def count(self, model):
        with self.lock: return self.calls.count(model)

    def close(self):
        self.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join()


def parallel_flow():
    model = lambda id, name: {'id': id, 'type': 'llm', 'inputs': {'prompt': 'fork.text'}, 'config': {'provider': 'ollama', 'model': name}}
    nodes = [{'id': 'input', 'type': 'chat_input'}, {'id': 'fork', 'type': 'prompt', 'inputs': {'message': 'input.message'}},
             model('a1', 'branch-a'), model('b1', 'branch-b'), {'id': 'join', 'type': 'join'},
             {'id': 'out', 'type': 'response', 'inputs': {'text': 'a1.text'}}]
    pairs = [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'join'), ('b1', 'join'), ('join', 'out')]
    return {'version': 1, 'name': 'A5.3 parallel crash', 'nodes': nodes,
            'edges': [{'id': str(i), 'source': a, 'target': b} for i, (a, b) in enumerate(pairs)]}


def parallel_crash(kind, output):
    """A5.3 D01: SIGKILL after branch A's checkpoint commit while branch B's call is outstanding.

    The resumed run restores A from its checkpoint without a request, sends B again, runs the
    Join once, and saves A's 15 tokens once (A 15 + B's resumed call 15; B's killed call was
    never answered, so it reported nothing).
    """
    models = BranchModels()
    try:
        with database(kind) as url, deployed(url, output, {'OLLAMA_BASE_URL': models.url}) as (lab, store):
            for name in ('branch-a', 'branch-b'):
                response = lab.client.post('/api/models', json={'provider': 'ollama', 'model': name})
                assert response.status_code == 201, response.text
            identifier = lab.submit(parallel_flow(), 'synthetic parallel probe')
            held = start_worker(lab, 'serve', '--accounting-barrier', 'after_commit', '--barrier-node', 'a1')
            barrier = lab.directory / 'accounting-barrier'
            wait_for(lambda: barrier.exists() and models.count('branch-b') == 1, 60)
            os.kill(held.pid, signal.SIGKILL); held.wait(timeout=10)
            prior = store.run(identifier, lab.tenant)
            assert 'a1' in prior['checkpoints'] and 'b1' not in prior['checkpoints'], sorted(prior['checkpoints'])
            assert prior['accounting']['tokens_spent'] == 15, prior['accounting']
            # Wait for the real default 30-second lease, without mutating the database.
            start_worker(lab)
            resumed = lab.wait(identifier, timeout=90)
            assert resumed['status'] == 'success', resumed.get('error')
            calls = {'branch-a': models.count('branch-a'), 'branch-b': models.count('branch-b')}
            assert calls == {'branch-a': 1, 'branch-b': 2}, calls
            joins = [e for e in resumed['events'] if e.get('node_id') == 'join' and e.get('status') == 'success']
            assert len(joins) == 1, joins
            assert resumed['output'] == 'answer branch-a'
            assert resumed['accounting']['tokens_spent'] == 30, resumed['accounting']
            assert lab.provider.calls == []
            return {'database': kind, 'sigkill': 'after branch A checkpoint, branch B outstanding',
                    'prior_checkpoints': sorted(prior['checkpoints']), 'prior_tokens_spent': 15,
                    'model_calls': calls, 'join_runs': 1, 'final_tokens_spent': 30}
    finally:
        models.close()


class RepeatModels:
    """Ollama fixture for the A6.1 Repeat rows. The writer drafts, the checker judges. The
    writer's second request (iteration 1) is held open, so it is outstanding when the worker
    is killed. Each answered request reports 15 tokens."""
    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.calls, self.lock, self.release = [], threading.Lock(), threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                model = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['model']
                with owner.lock:
                    owner.calls.append(model)
                    number = owner.calls.count(model)
                held = model == 'repeat-writer' and number == 2
                if held: owner.release.wait(120)
                if model == 'repeat-writer':
                    content = 'draft 1' if number == 1 else 'draft 2'
                else:
                    content = json.dumps({'pass': number > 1, 'feedback': 'add the date' if number == 1 else 'good'})
                body = json.dumps({'message': {'role': 'assistant', 'content': content},
                                   'prompt_eval_count': 10, 'eval_count': 5}).encode()
                try:
                    self.send_response(200); self.send_header('Content-Length', str(len(body)))
                    self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
                except OSError: pass  # The killed worker's connection is gone.
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def count(self, model):
        with self.lock: return self.calls.count(model)

    def close(self):
        self.release.set(); self.server.shutdown(); self.server.server_close(); self.thread.join()


def repeat_flow():
    verdict = {'type': 'object', 'required': ['pass', 'feedback'],
               'properties': {'pass': {'type': 'boolean'}, 'feedback': {'type': 'string'}}}
    nodes = [{'id': 'input', 'type': 'chat_input'},
             {'id': 'r', 'type': 'repeat', 'inputs': {'input': 'input.message'},
              'config': {'body': 'writer', 'check': 'judge', 'max_iterations': 3,
                         'until': {'operator': 'eq', 'field': 'pass', 'compare_to': 'true'}}},
             {'id': 'writer', 'type': 'agent', 'config': {'provider': 'ollama', 'model': 'repeat-writer', 'system': 'Write a reply.'}},
             {'id': 'judge', 'type': 'agent', 'config': {'provider': 'ollama', 'model': 'repeat-judge', 'system': 'Judge the reply.',
                                                         'role': 'critic', 'output_schema': verdict}},
             {'id': 'out', 'type': 'response', 'inputs': {'text': 'r.text'}}]
    edges = [{'id': 'e0', 'source': 'input', 'target': 'r'}, {'id': 'e1', 'source': 'r', 'target': 'out'},
             {'id': 'e2', 'source': 'r', 'target': 'writer', 'kind': 'loop', 'sourceHandle': 'body', 'targetHandle': 'input'},
             {'id': 'e3', 'source': 'r', 'target': 'judge', 'kind': 'loop', 'sourceHandle': 'check', 'targetHandle': 'input'}]
    return {'version': 1, 'name': 'A6.1 Repeat crash', 'nodes': nodes, 'edges': edges}


def repeat_crash(kind, output):
    """A6.1: SIGKILL during iteration 1 of a Repeat, while the writer's request is outstanding.

    Iteration 0 (writer, then checker: rule not met) is saved as row 0 before iteration 1
    starts. After the real lease ends, a new worker reuses row 0 without a request, repeats
    only iteration 1, and saves row 1. Tokens: each answered request is charged once (30
    before the kill, 60 at the end); the killed request was never answered.
    """
    models = RepeatModels()
    try:
        with database(kind) as url, deployed(url, output, {'OLLAMA_BASE_URL': models.url}) as (lab, store):
            for name in ('repeat-writer', 'repeat-judge'):
                response = lab.client.post('/api/models', json={'provider': 'ollama', 'model': name})
                assert response.status_code == 201, response.text
            identifier = lab.submit(repeat_flow(), 'synthetic repeat probe')
            held = start_worker(lab)
            wait_for(lambda: models.count('repeat-writer') == 2, 60)
            os.kill(held.pid, signal.SIGKILL); held.wait(timeout=10)
            prior = store.run(identifier, lab.tenant)
            prior_rows = prior.get('loop_progress', {}).get('r', {})
            assert sorted(prior_rows) == ['0'] and prior_rows['0']['met'] is False, prior_rows
            assert 'r' not in prior.get('checkpoints', {}), sorted(prior.get('checkpoints', {}))
            assert prior['accounting']['tokens_spent'] == 30, prior['accounting']
            # Wait for the real default 30-second lease, without mutating the database.
            start_worker(lab)
            resumed = lab.wait(identifier, timeout=90)
            assert resumed['status'] == 'success', resumed.get('error')
            calls = {'repeat-writer': models.count('repeat-writer'), 'repeat-judge': models.count('repeat-judge')}
            assert calls == {'repeat-writer': 3, 'repeat-judge': 2}, calls
            rows = resumed['loop_progress']['r']
            assert sorted(rows) == ['0', '1'], rows
            assert rows['0'] == prior_rows['0'], 'row 0 was reused, not written again'
            assert rows['1']['status'] == 'success' and rows['1']['value'] == 'draft 2' and rows['1']['met'] is True
            assert resumed['output'] == 'draft 2'
            assert json.loads(resumed['checkpoints']['r']['summary'])['iterations'] == 2
            assert resumed['accounting']['tokens_spent'] == 60, resumed['accounting']
            assert lab.provider.calls == []
            return {'database': kind, 'sigkill': 'iteration 1, writer request outstanding',
                    'prior_rows': sorted(prior_rows), 'prior_tokens_spent': 30, 'model_calls': calls,
                    'final_rows': sorted(rows), 'final_tokens_spent': 60}
    finally:
        models.close()


async def measure(url, n):
    import pytest
    from backend.app.worker import Worker
    from backend.tests.test_loop_completeness import jira_connection, loop_flow
    patch = pytest.MonkeyPatch()
    patch.setenv('AGENT_RUN_BUDGET', '200')
    try:
        from backend.app import tool_service
        patch.setattr(tool_service, 'public_addresses', lambda *_: ['93.184.216.34'])
        store = Store(url, Fernet.generate_key())
        worker = Worker(store)
        async def prepared(*_):
            return json.dumps({'issues': [{'key': f'PROJ-{i}', 'fields': {'summary': f'Issue {i}'}} for i in range(n)]})
        async def execute(*_, **__): return 'x' * 60000
        patch.setattr(worker.tools, 'execute_prepared', prepared)
        patch.setattr(worker.tools, 'execute', execute)
        written = {'runs': 0, 'run_events': 0, 'run_loop_items': 0}
        def sql(_conn, _cursor, statement, params, _context, _many):
            values = params.values() if isinstance(params, dict) else params or []
            size = 0
            for value in values:
                # psycopg wraps JSON parameters instead of passing the serialized string
                # SQLite receives. Counting only strings would falsely make PG look cheap.
                if hasattr(value, 'obj'):
                    value = (getattr(value, 'dumps', None) or json.dumps)(value.obj)
                if isinstance(value, str): size += len(value.encode('utf-8'))
                elif isinstance(value, bytes): size += len(value)
            for table in written:
                if statement.startswith((f'UPDATE {table} ', f'INSERT INTO {table} ')): written[table] += size
        row = store.create_run(loop_flow(connection_id=jira_connection(store), max_items=1000).model_dump(mode='json'), 'go')
        event.listen(store.engine, 'before_cursor_execute', sql)
        await worker.execute(store.claim_next(worker.owner))
        event.remove(store.engine, 'before_cursor_execute', sql)
        result = store.run(row['id'])
        assert result['status'] == 'success', result.get('error')
        summary = json.loads(result['checkpoints']['each']['summary'])
        assert summary['processed'] == n and summary['failed'] == 0
        with store.engine.connect() as connection:
            expr = 'length({column})' if store.engine.dialect.name == 'sqlite' else 'octet_length({column}::text)'
            sizes = {}
            for table, column, where in [('runs', 'data', 'id'), ('run_events', 'payload', 'run_id'), ('run_loop_items', 'entry', 'run_id')]:
                sizes[table] = connection.execute(text(f'SELECT coalesce(sum({expr.format(column=column)}),0) FROM {table} WHERE {where}=:id'), {'id': row['id']}).scalar()
        store.engine.dispose()
        assert sizes['run_loop_items'] < 1_100_000
        return {'items': n, 'item_value_bytes': 60000, 'processed': summary['processed'], 'final_bytes': sizes, 'bound_parameter_bytes': {**written, 'total': sum(written.values())}}
    finally:
        patch.undo()


def storage_cost():
    rows = []
    for kind in ['sqlite', 'postgres']:
        samples = []
        for n in [25, 50, 100]:
            with database(kind) as url:
                samples.append(asyncio.run(measure(url, n)))
        ratios = [b['bound_parameter_bytes']['total'] / a['bound_parameter_bytes']['total'] for a, b in zip(samples, samples[1:])]
        assert max(ratios) < 2.5, ratios
        rows.append({'database': kind, 'samples': samples, 'doubling_write_ratios': ratios, 'measurement': 'Serialized parameters submitted to SQL; excludes database WAL/index/page overhead. Tool transport scripted to isolate storage cost.'})
    return rows


def populated_downgrade(output):
    """A downgrade is not lossless (docs/operations.md); it must not be silent either.

    Revision 0003's downgrade drops run_loop_items and runs.accounting. Three populated runs
    go through one downgrade to 0001 and back: a finished loop, a loop SIGKILLed after its
    first item committed, and a write batch paused at its second approval after the first
    write. The finished run must show its lost item results as unavailable, never as an
    empty success. Each unfinished run must fail on resume with the accounting refusal and
    send nothing again: no committed read is repeated and no write is sent twice.
    """
    from alembic import command
    from backend.app.accounting_state import INVALID
    with database('postgres') as url, deployed(url, output) as (lab, store):
        def run(identifier): return store.run(identifier, lab.tenant)
        def pending(identifier):
            return next((a for a in lab.wait(identifier)['approvals'] if a['status'] == 'pending'), None)
        worker = start_worker(lab)
        finished = lab.submit(lab.batch('/read/downgrade', n=2, write=False))
        before = lab.wait(finished)
        assert before['status'] == 'success' and len(before['checkpoints']['each']['results']) == 2, before.get('error')
        write_path = '/deliver/downgrade'
        paused = lab.submit(lab.batch(write_path, n=3))
        assert lab.decide(paused, pending(paused)).status_code == 200
        wait_for(lambda: lab.provider.count(write_path) == 1 and run(paused)['status'] == 'awaiting_approval')
        read_path = '/hold/downgrade'
        killed = lab.submit(lab.batch(read_path, n=3, write=False))
        wait_for(lambda: lab.provider.count(read_path) >= 2)
        os.kill(worker.pid, signal.SIGKILL); worker.wait(timeout=10)
        held = {name: run(identifier) for name, identifier in [('paused', paused), ('killed', killed)]}
        assert len(held['killed']['loop_progress']['each']) == 1 and len(held['paused']['loop_progress']['each']) == 1
        for process in lab.processes[1:]:  # processes[0] is the API; stop every worker.
            if process.poll() is None: process.terminate(); process.wait(timeout=10)
        calls = len(lab.provider.calls)
        with store.engine.begin() as connection:
            command.downgrade(schema._config(connection), '0001_baseline')
            schema.upgrade(connection)
            assert schema.current_revision(connection) == schema.head_revision()
        after = run(finished)
        results = after['checkpoints']['each']['results']
        (output / 'finished.json').write_text(json.dumps({'before_results': before['checkpoints']['each']['results'], 'after_results': results,
                                                          'before_accounting': before['accounting'], 'after_accounting': after.get('accounting')}, indent=2))
        assert after['status'] == 'success' and after['output'] == before['output']
        assert isinstance(results, dict) and results.get('unavailable') == 'Loop each cannot be restored: 2 of 2 stored item results are missing. Start a new run.', results
        assert after.get('accounting') is None, 'Downgrade kept accounting; update the documented loss'
        start_worker(lab)
        assert lab.decide(paused, pending(paused)).status_code == 200
        resumed = {name: lab.wait(identifier, timeout=60) for name, identifier in [('paused', paused), ('killed', killed)]}
        (output / 'unfinished.json').write_text(json.dumps({name: {'status': value['status'], 'error': value.get('error')} for name, value in resumed.items()}, indent=2))
        (output / 'receiver.json').write_text(json.dumps(lab.provider.calls, indent=2))
        for name, value in resumed.items():
            assert value['status'] == 'failed' and value.get('error') == INVALID, (name, value['status'], value.get('error'))
        assert len(lab.provider.calls) == calls, 'A resumed run sent a request after the downgrade'
        assert lab.provider.count(write_path) == 1 and lab.provider.count(read_path) == 2
        return {'lossless': False, 'finished_results': 'unavailable marker, not an empty success', 'accounting_after': None,
                'unfinished_runs_refused': 2, 'requests_after_downgrade': 0, 'writes_sent': 1}


def copied_workspace():
    source = ROOT / '.data/workflows.db'
    with tempfile.TemporaryDirectory(prefix='relay-workspace-copy-') as directory:
        dest = Path(directory) / 'copy.db'
        with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as original, sqlite3.connect(dest) as copy:
            original.backup(copy)
        def counts():
            with sqlite3.connect(dest) as connection:
                return {name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'") if name not in ('alembic_version', 'schema_migrations')}
        before = counts()
        store = Store(f'sqlite:///{dest}', Fernet.generate_key())
        with store.engine.connect() as connection: revision = schema.current_revision(connection)
        store.engine.dispose()
        after = counts()
        assert before == after, 'Startup changed user row counts on the database copy'
        return {'copy_only': True, 'revision': revision, 'tables': len(after), 'row_counts_preserved': True, 'limitation': 'Current workspace copy; genuine pre-Alembic adoption is exercised by the migration suite, not invented by removing its version stamp.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='evals/results/milestone-2026-10/storage')
    parser.add_argument('--checks', nargs='+', help='Run only these named checks (unknown names are refused)')
    args = parser.parse_args()
    output = Path(args.out).resolve(); output.mkdir(parents=True, exist_ok=True)
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    started_at = datetime.now(timezone.utc).isoformat()
    paths = [*sorted((ROOT / 'backend/app').rglob('*.py')), *sorted((ROOT / 'backend/migrations').rglob('*.py')),
             *sorted((ROOT / 'scripts').rglob('*.py'))]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    rows = []
    checks = [
        ('postgres-migrations', migrations),
        ('postgres-four-workers', lambda: concurrent_workers(output / 'workers')),
        ('postgres-stale-lease', lambda: stale_lease(output / 'lease')),
        ('postgres-sigkill-loop', lambda: killed_loop(output / 'crash')),
        ('storage-cost', storage_cost),
        ('copied-workspace', copied_workspace),
        ('populated-downgrade', lambda: populated_downgrade(output / 'downgrade')),
        *[(f'{kind}-accounting-{moment.replace("_", "-")}', lambda kind=kind, moment=moment: accounting_commit(kind, moment, output / f'accounting-{kind}-{moment}'))
          for kind in ('sqlite', 'postgres') for moment in ('before_commit', 'after_commit')],
        *[(f'{kind}-parallel-sigkill', lambda kind=kind: parallel_crash(kind, output / f'parallel-{kind}')) for kind in ('sqlite', 'postgres')],
        *[(f'{kind}-repeat-sigkill', lambda kind=kind: repeat_crash(kind, output / f'repeat-{kind}')) for kind in ('sqlite', 'postgres')],
    ]
    if args.checks and set(args.checks) - {name for name, _ in checks}:
        parser.error('Unknown check name')
    for name, operation in checks:
        if args.checks and name not in args.checks:
            continue
        started = time.monotonic()
        try:
            details = operation(); row = {'name': name, 'passed': True, 'details': details}
        except Exception as exc:
            row = {'name': name, 'passed': False, 'error': f'{type(exc).__name__}: {exc}'}
        row['seconds'] = round(time.monotonic() - started, 3)
        rows.append(row); print(json.dumps(row), flush=True)
    changed = [str(path.relative_to(ROOT)) for path in paths if hashlib.sha256(path.read_bytes()).hexdigest() != hashes[str(path.relative_to(ROOT))]]
    report = {'head_at_start': head, 'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'started_at': started_at, 'completed_at': datetime.now(timezone.utc).isoformat(),
              'source_sha256': hashes, 'source_files_changed': changed,
              'rows': rows, 'passed': all(row['passed'] for row in rows) and not changed}
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
