"""TEST ONLY: real subprocesses for the milestone's queue and stale-lease probes."""
import argparse
import asyncio
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['serve', 'stale'])
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


if __name__ == '__main__':
    main()
