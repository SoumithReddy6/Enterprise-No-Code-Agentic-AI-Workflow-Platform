"""Independent TA02 checks. Run from the repository root with PYTHONPATH=.

Writes only disposable databases and this review's JSON result. No product mutations.
"""
import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile

import pytest
import sqlalchemy
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from backend.app.accounting_state import AccountingUnavailable
from backend.tests.test_accounting_durability import (
    REPLY, SCHEMA, attempt, chat, claimed, durable, plain_agent,
)


async def lost_acknowledgment(root):
    """A fast save commits, then its caller receives an error; repair must still be safe."""
    patch = pytest.MonkeyPatch()
    store, worker = durable(root / 'lost-ack')
    calls, flushes = [], []
    chat(patch, [('not json', 10, 5), ('{"amount": 1}', 10, 5)], calls)
    original_fast, original_reserve = store.record_accounting, store.reserve_accounting

    def lost_ack(*args):
        assert original_fast(*args) is True
        raise OSError('synthetic lost acknowledgment after commit')

    def reserve(identifier, owner, snapshot):
        flushes.append(snapshot['tokens_spent'])
        return original_reserve(identifier, owner, snapshot)

    patch.setattr(store, 'record_accounting', lost_ack)
    patch.setattr(store, 'reserve_accounting', reserve)
    try:
        row = store.create_run(plain_agent('fail', output_schema=SCHEMA).model_dump(mode='json'), 'go')
        await attempt(store, worker)
        saved = store.run(row['id'])
        assert saved['status'] == 'success', saved.get('error')
        assert len(calls) == 2 and saved['accounting']['tokens_spent'] == 30
        assert 15 in flushes, flushes
        return {'passed': True, 'model_calls': len(calls), 'tokens_spent': 30,
                'pending_spend_resaved_before_repair': True}
    finally:
        patch.undo()
        store.engine.dispose()


def missing_snapshot(root):
    store, identifier, owner = claimed(root / 'missing')
    try:
        before = store.run(identifier)
        try:
            store.worker_event(identifier, owner, {'node_id': 'm0', 'status': 'success',
                                                   'outputs': {'text': 'must not persist'}})
        except AccountingUnavailable:
            pass
        else:
            raise AssertionError('A publishing event without accounting was accepted')
        after = store.run(identifier)
        assert after['accounting'] == before['accounting']
        assert after['events'] == before['events'] and after['checkpoints'] == before['checkpoints']
        return {'passed': True, 'error_type': 'AccountingUnavailable', 'published_nothing': True}
    finally:
        store.engine.dispose()


def injection_boundary(root, hook):
    """Observe only this pinned SQLAlchemy/SQLite behavior, not a general DB guarantee."""
    path = root / f'{hook}.db'
    engine = create_engine(f'sqlite:///{path}')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE probe (value INTEGER)'))
        conn.execute(text('INSERT INTO probe VALUES (0)'))
    armed = {'on': True}

    def fail(target):
        if armed['on']:
            armed['on'] = False
            if hook == 'session':
                target.flush()
            raise RuntimeError('synthetic precommit failure')

    target, name = (Session, 'before_commit') if hook == 'session' else (engine, 'commit')
    event.listen(target, name, fail)
    try:
        try:
            with Session(engine) as session:
                session.execute(text('UPDATE probe SET value = 1'))
                session.commit()
        except RuntimeError:
            pass
        else:
            raise AssertionError('Failure injection did not fire')
    finally:
        event.remove(target, name, fail)
    with sqlite3.connect(path, timeout=.05) as independent:
        independent_value = independent.execute('SELECT value FROM probe').fetchone()[0]
    with engine.connect() as pooled:
        pooled_value = pooled.execute(text('SELECT value FROM probe')).scalar_one()
        # Collect any still-open SQLite transaction instead of leaving the probe locked.
        pooled.connection.driver_connection.rollback()
    engine.dispose()
    assert independent_value == 0
    assert pooled_value == (0 if hook == 'session' else 1)
    return {'passed': True, 'independent_connection_value': independent_value,
            'pooled_connection_value': pooled_value}


async def main():
    with tempfile.TemporaryDirectory(prefix='ta02-review-extra-') as directory:
        root = Path(directory)
        rows = {
            'lost_fast_save_ack': await lost_acknowledgment(root),
            'missing_publishing_snapshot': missing_snapshot(root),
            'session_hook_rollback': injection_boundary(root, 'session'),
            'connection_hook_is_not_a_rollback': injection_boundary(root, 'connection'),
        }
    report = {'sqlalchemy': sqlalchemy.__version__, 'rows': rows,
              'passed': all(row['passed'] for row in rows.values())}
    Path(__file__).with_name('additional-probes.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    asyncio.run(main())
