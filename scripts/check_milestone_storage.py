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
def deployed(url, output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='relay-milestone-process-') as directory:
        lab = Lab(directory, output)
        lab.env.update(DATABASE_URL=url, AGENT_RUN_BUDGET='200')
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


def start_worker(lab, mode='serve'):
    log = open(lab.output / f'milestone-worker-{len(lab.processes)}.log', 'w')
    lab.logs.append(log)
    process = subprocess.Popen([sys.executable, '-m', 'scripts.scenarios.milestone_worker', mode], cwd=ROOT, env=lab.env, stdout=log, stderr=subprocess.STDOUT)
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
                assert schema.current_revision(connection) == '0003_loop_storage'
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
    # Downgrading an empty database is not proof that populated loop history survives.
    from alembic import command
    with database('postgres') as url, deployed(url, output) as (lab, store):
        start_worker(lab)
        identifier = lab.submit(lab.batch('/read/downgrade', n=2, write=False))
        before = lab.wait(identifier)
        assert before['status'] == 'success'
        for process in lab.processes:
            if process.poll() is None: process.terminate(); process.wait(timeout=10)
        with store.engine.begin() as connection:
            command.downgrade(schema._config(connection), '0001_baseline')
            schema.upgrade(connection)
        after = store.run(identifier, lab.tenant)
        expected = before['checkpoints']['each']['results']
        actual = after['checkpoints']['each']['results']
        (output / 'comparison.json').write_text(json.dumps({'before_results': expected, 'after_results': actual, 'before_accounting': before['accounting'], 'after_accounting': after.get('accounting')}, indent=2))
        assert actual == expected, 'Downgrade/re-upgrade lost persisted loop results'
        assert after['accounting'] == before['accounting'], 'Downgrade/re-upgrade lost accounting'
        return {'populated_history_preserved': True}


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
