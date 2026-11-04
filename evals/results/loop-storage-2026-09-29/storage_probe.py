"""Storage cost of one loop run: final sizes and total bytes written, per store.

Runs a real Worker against SQLite. Each item returns a 60,000-byte value (under the
64,000-byte tool cap). Bytes written are the sizes of the string/bytes parameters bound
to each INSERT/UPDATE, which is what the database is asked to store.
"""
import asyncio, json, sys, tempfile
from pathlib import Path
import pytest
from sqlalchemy import event, inspect, text
from backend.tests.test_loop_completeness import durable_setup, jira_connection, loop_flow

async def measure(n, value_bytes=60000):
    mp = pytest.MonkeyPatch()
    with tempfile.TemporaryDirectory() as tmp:
        store, worker, _, _ = durable_setup(Path(tmp), mp, items=n)
        async def execute(node_type, settings, text_, tenant_id='local'): return 'x' * value_bytes
        mp.setattr(worker.tools, 'execute', execute)
        written = {'runs': 0, 'run_events': 0, 'run_loop_items': 0}
        def sql(conn, cursor, statement, params, context, executemany):
            values = params.values() if isinstance(params, dict) else (params or [])
            size = sum(len(p) for p in values if isinstance(p, (str, bytes)))
            for table in written:
                if statement.startswith((f'UPDATE {table} ', f'INSERT INTO {table} ')): written[table] += size
        row = store.create_run(loop_flow(connection_id=jira_connection(store), max_items=1000).model_dump(mode='json'), 'go')
        event.listen(store.engine, 'before_cursor_execute', sql)
        await worker.execute(store.claim_next(worker.owner))
        event.remove(store.engine, 'before_cursor_execute', sql)
        with store.engine.connect() as c:
            final_run = c.execute(text('SELECT length(data) FROM runs WHERE id=:i'), {'i': row['id']}).scalar()
            final_events = c.execute(text('SELECT coalesce(sum(length(payload)),0) FROM run_events WHERE run_id=:i'), {'i': row['id']}).scalar()
            final_items = c.execute(text('SELECT coalesce(sum(length(entry)),0) FROM run_loop_items WHERE run_id=:i'), {'i': row['id']}).scalar() \
                if 'run_loop_items' in inspect(store.engine).get_table_names() else 0
        summary = json.loads(store.run(row['id'])['checkpoints']['each']['summary'])
    mp.undo()
    return {'items': n, 'processed': summary['processed'], 'values_dropped': summary['dropped_value_count'] if 'dropped_value_count' in summary else summary['values_dropped'],
            'final_bytes': {'runs_row': final_run, 'run_loop_items': final_items, 'run_events': final_events},
            'written_bytes': {**written, 'total': sum(written.values())}}

async def main():
    value = int(sys.argv[2]) if len(sys.argv) > 2 else 60000
    rows = [await measure(n, value) for n in (25, 50, 100)]
    for a, b in zip(rows, rows[1:]):
        b['growth_vs_previous'] = {'items': b['items'] / a['items'], 'total_written': round(b['written_bytes']['total'] / a['written_bytes']['total'], 2)}
    print(json.dumps(rows, indent=1))
    if len(sys.argv) > 1: Path(sys.argv[1]).write_text(json.dumps(rows, indent=1))
asyncio.run(main())
