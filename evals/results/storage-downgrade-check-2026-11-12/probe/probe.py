"""Scratch probe: what a downgrade to 0001 and re-upgrade does to runs that are NOT finished."""
import json, os, signal, sys
from pathlib import Path
from alembic import command
from backend.app import schema
from scripts.check_milestone_storage import database, deployed, start_worker, wait_for

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
report = {}

def roundtrip(store):
    with store.engine.begin() as connection:
        command.downgrade(schema._config(connection), '0001_baseline')
        schema.upgrade(connection)

def stop(lab):
    for p in lab.processes[1:]:  # processes[0] is the API; stop only the workers.
        if p.poll() is None: p.terminate(); p.wait(timeout=10)

# A: interrupted read loop (item 0 committed, item 1 in flight), then round trip, then resume.
with database('postgres') as url, deployed(url, out / 'read') as (lab, store):
    path = '/hold/dg-read'
    rid = lab.submit(lab.batch(path, n=3, write=False))
    w = start_worker(lab)
    wait_for(lambda: lab.provider.count(path) >= 2)
    os.kill(w.pid, signal.SIGKILL); w.wait(timeout=10)
    prior = store.run(rid, lab.tenant)
    stop(lab)
    roundtrip(store)
    mid = store.run(rid, lab.tenant)
    start_worker(lab)
    final = lab.wait(rid, timeout=60)
    first = next(c['body'] for c in lab.provider.calls if c['path'] == path)
    report['read'] = {'prior_progress': len(prior['loop_progress'].get('each', [])), 'prior_counter': prior['accounting']['action_budget']['counter'],
        'after_roundtrip_progress': len(mid.get('loop_progress', {}).get('each', [])), 'after_roundtrip_accounting': mid.get('accounting'),
        'status': final['status'], 'error': final.get('error'), 'item0_sent': sum(c['path'] == path and c['body'] == first for c in lab.provider.calls),
        'reads': lab.provider.count(path), 'final_counter': (final.get('accounting') or {}).get('action_budget', {}).get('counter')}

# B: approved write batch paused at item 1's approval (item 0 written), round trip, approve the rest.
with database('postgres') as url, deployed(url, out / 'write') as (lab, store):
    path = '/deliver/dg-write'
    start_worker(lab)
    rid = lab.submit(lab.batch(path, n=3))
    run = lab.wait(rid); pending = next(a for a in run['approvals'] if a['status'] == 'pending')
    assert lab.decide(rid, pending).status_code == 200
    run = lab.wait(rid, statuses=('awaiting_approval', 'failed', 'success'))
    while lab.provider.count(path) < 1: run = lab.wait(rid)
    run = lab.wait(rid)
    prior = store.run(rid, lab.tenant)
    stop(lab)
    roundtrip(store)
    mid = store.run(rid, lab.tenant)
    start_worker(lab)
    decisions = []
    for _ in range(6):
        run = lab.wait(rid, timeout=40)
        if run['status'] != 'awaiting_approval': break
        pending = next(a for a in run['approvals'] if a['status'] == 'pending')
        decisions.append(pending['payload'].get('body'))
        r = lab.decide(rid, pending); decisions.append(r.status_code)
    report['write'] = {'prior_status': prior['status'], 'prior_progress': len(prior['loop_progress'].get('each', [])), 'writes_before': 1,
        'after_roundtrip_progress': len(mid.get('loop_progress', {}).get('each', [])), 'decisions': decisions,
        'status': run['status'], 'error': run.get('error'), 'writes': lab.provider.count(path),
        'bodies': [c['body'] for c in lab.provider.calls if c['path'] == path]}

(out / 'probe.json').write_text(json.dumps(report, indent=2, default=str)); print(json.dumps(report, indent=2, default=str))
