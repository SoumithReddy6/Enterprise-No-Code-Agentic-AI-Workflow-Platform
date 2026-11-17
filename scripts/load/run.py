"""Run the load baseline: a closed-queue drain for each (database, workers, workflow, latency) cell.

Example:
  .venv/bin/python -m scripts.load.run --db sqlite,postgres --workers 1,4,16 --workflow w3,w20 \
      --latency-ms 0 --runs 200 --repeats 3 --out evals/results/load-baseline-2026-11-16

PostgreSQL runs in a throwaway container that this script starts and removes. It never uses
another database. Spec: docs/specs/load-baseline-harness.md.
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from .fixtures import ModelStub
from .measure import DONE, Reader, seconds, summarize
from .stack import MARKER, ROOT, Stack, free_port
from .workflows import MODEL, WORKFLOWS

SLOTS = 4  # backend/app/worker.py:58 (fixed in the code under test)


class PostgresServer:
    """A throwaway PostgreSQL container; one new database for each cell."""
    def __init__(self):
        self.port = free_port()
        self.name = f'relay-load-pg-{os.getpid()}'
        subprocess.run(['docker', 'run', '-d', '--rm', '--name', self.name, '-e', 'POSTGRES_PASSWORD=load',
                        '-p', f'127.0.0.1:{self.port}:5432', 'postgres:17-alpine', '-c', 'max_connections=500'],
                       check=True, capture_output=True)
        self.admin = f'postgresql+psycopg://postgres:load@127.0.0.1:{self.port}/postgres'
        deadline = time.monotonic() + 60
        while True:
            try:
                with create_engine(self.admin).connect() as connection:
                    self.version = connection.execute(text('SHOW server_version')).scalar()
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        self.count = 0

    def new_database(self):
        self.count += 1
        name = f'relay_load_{self.count:03d}'
        engine = create_engine(self.admin, isolation_level='AUTOCOMMIT')
        with engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE {name}'))
        engine.dispose()
        return f'postgresql+psycopg://postgres:load@127.0.0.1:{self.port}/{name}'

    def close(self):
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True)


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def run_cell(database, postgres, workers, workflow_name, latency_ms, runs, repeat, out, timeout):
    builder, calls_per_run = WORKFLOWS[workflow_name]
    label = f'{database}-w{workers:02d}-{workflow_name}-{latency_ms}ms-r{repeat}'
    stub = ModelStub(latency_ms)
    flags = []
    with tempfile.TemporaryDirectory(prefix='relay-load-') as directory:
        (Path(directory) / MARKER).touch()
        url = postgres.new_database() if database == 'postgres' else f'sqlite:///{directory}/relay.db'
        stack = Stack(directory, url, stub.url, out / 'logs' / label)
        reader = None
        try:
            stack.start_api(MODEL)
            started = time.monotonic()
            stack.submit(builder(), runs)
            submit_seconds = time.monotonic() - started
            reader = Reader(url)
            stub.reset()
            before = reader.counters()
            workers_started_at = datetime.now(timezone.utc).isoformat()
            stack.start_workers(workers)
            deadline = time.monotonic() + timeout
            while True:
                statuses = reader.statuses()
                if sum(count for status, count in statuses.items() if status in DONE) >= runs:
                    break
                if time.monotonic() > deadline:
                    flags.append('timeout')
                    break
                time.sleep(1)
            time.sleep(2)  # PostgreSQL publishes its statistics with a short delay.
            after = reader.counters()
            model_calls = stub.calls
            result = summarize(reader.timings(), latency_ms / 1000, calls_per_run)
        finally:
            stack.close()
            if reader:
                reader.close()
            stub.close()
    completed = result['completed']
    if result['statuses'].get('success', 0) != runs:
        flags.append('degraded')
    if result['reclaims'] == 0 and 'timeout' not in flags and model_calls != runs * calls_per_run:
        flags.append('model_call_mismatch')
    per_run = (lambda key: round((after[key] - before[key]) / completed, 1)) if before and after and completed else (lambda key: None)
    startup = seconds(result.pop('first_claim_at'), workers_started_at) if result.get('first_claim_at') else None
    cell = {'database': database, 'workers': workers, 'slots': SLOTS, 'workflow': workflow_name, 'latency_ms': latency_ms,
            'runs': runs, 'repeat': repeat, 'submit_seconds': round(submit_seconds, 2),
            'worker_start_to_first_claim_seconds': round(startup, 2) if startup is not None else None,
            **result, 'model_calls': model_calls,
            'commits_per_run': per_run('commits'), 'rollbacks_per_run': per_run('rollbacks'),
            'rows_inserted_per_run': per_run('inserted'), 'rows_updated_per_run': per_run('updated'),
            'harness_queries': reader.queries if reader else None, 'flags': flags}
    print(f"{label}: {cell['runs_per_min']} runs/min, drain {cell['drain_seconds']} s, "
          f"commits/run {cell['commits_per_run']}, reclaims {cell['reclaims']}, flags {flags}", flush=True)
    return cell


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', default='sqlite')
    parser.add_argument('--workers', default='1,4,16')
    parser.add_argument('--workflow', default='w3,w20')
    parser.add_argument('--latency-ms', default='0')
    parser.add_argument('--runs', type=int, default=200)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--out', required=True)
    parser.add_argument('--allow-dirty', action='store_true')
    args = parser.parse_args()
    dirty = bool(git('status', '--porcelain', '--', 'backend/app'))
    if dirty and not args.allow_dirty:
        raise SystemExit('backend/app has uncommitted changes; a baseline must name one commit.')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    databases = args.db.split(',')
    postgres = PostgresServer() if 'postgres' in databases else None
    import sqlite3
    memory = None
    try:
        memory = round(int(subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True, text=True).stdout) / 2**30)
    except (ValueError, OSError):
        pass
    report = {'commit': git('rev-parse', 'HEAD'), 'source_commit': git('log', '-1', '--format=%H', '--', 'backend/app'),
              'dirty': dirty, 'started_at': datetime.now(timezone.utc).isoformat(), 'command': ' '.join(sys.argv),
              'machine': {'platform': platform.platform(), 'processor': platform.processor(), 'cpu_count': os.cpu_count(),
                          'memory_gb': memory, 'python': platform.python_version()},
              'database': {'sqlite': sqlite3.sqlite_version, 'postgres': postgres.version if postgres else None,
                           'postgres_settings': {'max_connections': 500, 'container': 'postgres:17-alpine, defaults'}},
              'relay_settings': {'slots_per_worker': SLOTS, 'lease_seconds': 30, 'heartbeat_seconds': 0.2,
                                 'idle_poll_seconds': 0.1, 'run_timeout_seconds': 120},
              'mode': 'closed-queue drain: all runs are queued before the workers start', 'cells': []}
    try:
        for database in databases:
            for workflow in args.workflow.split(','):
                for latency in [int(v) for v in args.latency_ms.split(',')]:
                    for workers in [int(v) for v in args.workers.split(',')]:
                        for repeat in range(1, args.repeats + 1):
                            try:
                                cell = run_cell(database, postgres, workers, workflow, latency, args.runs, repeat, out, args.timeout)
                            except Exception as exc:  # One failed cell must not lose the others.
                                cell = {'database': database, 'workers': workers, 'workflow': workflow, 'latency_ms': latency,
                                        'runs': args.runs, 'repeat': repeat, 'flags': ['harness_error'], 'error': f'{type(exc).__name__}: {exc}'[:300]}
                                print(cell, flush=True)
                            report['cells'].append(cell)
                            (out / 'results.json').write_text(json.dumps(report, indent=2))
    finally:
        if postgres:
            postgres.close()
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    (out / 'results.json').write_text(json.dumps(report, indent=2))
    from .report import write_summary
    write_summary(out)


if __name__ == '__main__':
    main()
