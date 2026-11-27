"""Read timings and database counters for one cell, and compute statistics."""
import json
import math
from datetime import datetime

from sqlalchemy import create_engine, text

DONE = ('success', 'failed', 'cancelled')


def percentile(values, p):
    """Nearest-rank percentile of a list; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def spread(values):
    return {'p50': percentile(values, 50), 'p95': percentile(values, 95), 'p99': percentile(values, 99)} if values else None


class Reader:
    """The harness's own read connection. It counts its queries so that a reader can subtract them."""
    def __init__(self, database_url):
        self.engine = create_engine(database_url)
        self.postgres = self.engine.dialect.name == 'postgresql'
        self.queries = 0

    def _all(self, sql):
        self.queries += 1
        with self.engine.connect() as connection:
            return connection.execute(text(sql)).all()

    def statuses(self):
        return {status: count for status, count in self._all('SELECT status, count(*) FROM runs GROUP BY status')}

    def counters(self):
        """Database-wide counters, PostgreSQL only; None on SQLite (unmeasured)."""
        if not self.postgres:
            return None
        row = self._all('SELECT xact_commit, xact_rollback, tup_inserted, tup_updated, tup_deleted '
                        'FROM pg_stat_database WHERE datname = current_database()')[0]
        return dict(zip(('commits', 'rollbacks', 'inserted', 'updated', 'deleted'), row))

    def timings(self):
        """For each run: created, first claim, finish, and the number of claims."""
        runs = {}
        for run_id, created, status, data in self._all('SELECT id, created_at, status, data FROM runs'):
            document = data if isinstance(data, dict) else json.loads(data)
            runs[run_id] = {'created': created, 'status': status, 'finished': document.get('finished_at'),
                            'error': document.get('error') or '', 'claims': []}
        for run_id, stamp, payload in self._all("SELECT run_id, timestamp, payload FROM run_events WHERE type = 'run' ORDER BY run_id, seq"):
            event = payload if isinstance(payload, dict) else json.loads(payload)
            if event.get('status') == 'running' and run_id in runs:
                runs[run_id]['claims'].append(stamp)
        return runs

    def close(self):
        self.engine.dispose()


def seconds(later, earlier):
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds()


def summarize(runs, model_latency_s, model_calls_per_run):
    finished = [r for r in runs.values() if r['status'] in DONE and r['finished'] and r['claims']]
    succeeded = [r for r in finished if r['status'] == 'success']
    first_claim = min((r['claims'][0] for r in finished), default=None)
    last_finish = max((r['finished'] for r in finished), default=None)
    drain = seconds(last_finish, first_claim) if finished else None
    exec_times = [seconds(r['finished'], r['claims'][-1]) for r in succeeded]
    errors = {}
    for r in runs.values():
        if r['status'] != 'success':
            key = (r['error'] or r['status'])[:120]
            errors[key] = errors.get(key, 0) + 1
    return {
        'completed': len(succeeded),
        'drain_seconds': drain,
        'runs_per_min': round(len(succeeded) / drain * 60, 1) if drain else None,
        'queue_wait': spread([seconds(r['claims'][0], r['created']) for r in finished]),
        'claim_to_finish': spread(exec_times),
        'engine_overhead': spread([t - model_latency_s * model_calls_per_run for t in exec_times]),
        'reclaims': sum(max(0, len(r['claims']) - 1) for r in runs.values()),
        'statuses': {s: sum(r['status'] == s for r in runs.values()) for s in sorted({r['status'] for r in runs.values()})},
        'errors': errors,
        'first_claim_at': first_claim,
    }
