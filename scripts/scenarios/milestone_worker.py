"""TEST ONLY: real subprocesses for the milestone's queue and stale-lease probes."""
import argparse
import asyncio
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['serve', 'stale'])
    # TA02 process rows only: the immediate spend save fails, and the worker holds forever
    # at the selected boundary of m0's outcome commit until the parent kills it.
    parser.add_argument('--accounting-barrier', choices=['before_commit', 'after_commit'])
    parser.add_argument('--fail-fast-saves', action='store_true')
    args = parser.parse_args()
    directory = Path(os.environ['DATA_DIR'])
    if not (directory / 'SCENARIO_LAB_ONLY').is_file():
        raise SystemExit('An isolated scenario marker is required')
    from backend.app.storage import Store, local_key
    from backend.app.worker import Worker
    from backend.app import tool_service
    store = Store(os.environ['DATABASE_URL'], local_key(directory))
    original = tool_service.public_addresses
    port = int(os.environ['SCENARIO_FIXTURE_PORT'])
    tool_service.public_addresses = lambda host, p: ['127.0.0.1'] if host == 'scenario-provider.invalid' and p == port else original(host, p)
    if args.fail_fast_saves:
        def unavailable(*_args, **_kwargs): raise OSError('Scenario accounting outage')
        store.record_accounting = unavailable
    if args.accounting_barrier:
        accounting_barrier(store, directory, args.accounting_barrier)
    if args.mode == 'serve':
        asyncio.run(Worker(store, concurrency=1).serve())
        return
    run = store.claim_next('frozen-worker', lease_seconds=1)
    if run is None:
        raise SystemExit('No run to claim')
    (directory / 'stale-ready.json').write_text(json.dumps({'run_id': run['id']}))
    # The parent SIGSTOPs this process before releasing the gate, then takes the lease
    # with another real worker. No in-process cancellation models the old worker.
    import time
    while not (directory / 'stale-release').exists():
        time.sleep(.01)
    refused = {}
    attempts = {
        'event': lambda: store.worker_event(run['id'], 'frozen-worker', {'kind': 'node', 'node_id': 'out', 'status': 'success', 'outputs': {'text': 'STALE'}}),
        'finish': lambda: store.finish_run(run['id'], 'frozen-worker', 'success', output='STALE'),
        'reserve': lambda: store.reserve_accounting(run['id'], 'frozen-worker', {'action_budget': {'remaining': 200}}),
        'mark_write': lambda: store.mark_write(run['id'], 'frozen-worker', 'stale-action'),
        'settle_write': lambda: store.settle_write(run['id'], 'frozen-worker', 'stale-action', 'STALE'),
    }
    for name, call in attempts.items():
        try:
            refused[name] = call() is False
        except (ValueError, RuntimeError):
            refused[name] = True
    (directory / 'stale-result.json').write_text(json.dumps(refused))
    store.engine.dispose()
    if not all(refused.values()):
        raise SystemExit(1)


def accounting_barrier(store, directory, moment):
    """Hold m0's fresh outcome commit at one boundary; the parent SIGKILLs this process there.

    before_commit uses SQLAlchemy's ConnectionEvents.commit, which runs after the Session
    flushed every write and before the DBAPI commit. after_commit holds once the real
    method has returned True. Only the selected outcome is held, so run creation and the
    other events commit normally.
    """
    import threading
    import time
    from sqlalchemy import event
    active = threading.local()
    def hold():
        (directory / 'accounting-barrier').write_text(moment)
        while True:
            time.sleep(.05)
    original = store.worker_event
    def worker_event(id, owner, payload, **kwargs):
        active.on = payload.get('node_id') == 'm0' and payload.get('status') == 'success' and not payload.get('cached')
        try:
            result = original(id, owner, payload, **kwargs)
        finally:
            selected, active.on = active.on, False
        if selected and result is True and moment == 'after_commit':
            hold()
        return result
    store.worker_event = worker_event
    if moment == 'before_commit':
        def commit(_connection):
            if getattr(active, 'on', False):
                hold()
        event.listen(store.engine, 'commit', commit)


if __name__ == '__main__':
    main()
