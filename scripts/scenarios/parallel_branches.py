"""A5.3 deployed fork scenarios: separate API and worker processes, real auth, SQL storage,
leases and HTTP tool transport. Branches are HTTP reads against the gate's fixture, so each
branch's answer is exact; the write after the Join goes to the fixture's receiver.

Design: docs/superpowers/specs/2026-11-08-parallel-branches-scenarios-design.md (rev 2).
S22: branches meet at the fixture, the Join waits for both and runs once, one write is sent.
S23a: a failed branch fails the run and cancels its held sibling; nothing after the Join is sent.
S23b: a write inside a branch is refused before any run exists.
S24: a worker killed in a fork resumes; the finished branch is restored, the write is sent once.
"""
import json
import time
from datetime import datetime


def read(lab, id, path, source='input.message'):
    return {'id': id, 'type': 'tool_http', 'inputs': {'input': source},
            'config': {'connection_id': lab.http, 'method': 'GET', 'path': path}}


def write(lab, id, path, source):
    return {'id': id, 'type': 'tool_http', 'inputs': {'input': source},
            'config': {'connection_id': lab.http, 'method': 'POST', 'path': path, 'body': '{input}',
                       'enable_writes': True, 'approval': False}}


def flow(nodes, pairs):
    return {'version': 1, 'name': 'Parallel branches', 'nodes': [{'id': 'input', 'type': 'chat_input'}, *nodes],
            'edges': [{'id': f'e{i}', 'source': a, 'target': b} for i, (a, b) in enumerate(pairs)]}


def fork(lab, a, b, deliver, b2=None):
    """input forks to a1 and b1 (→ b2); the Join; the write reads a1; the Response reads B's last node."""
    last = 'b2' if b2 else 'b1'
    nodes = [read(lab, 'a1', a), read(lab, 'b1', b), *([read(lab, 'b2', b2, 'b1.text')] if b2 else []), {'id': 'join', 'type': 'join'},
             write(lab, 'send', deliver, 'a1.text'), {'id': 'out', 'type': 'response', 'inputs': {'text': f'{last}.text'}}]
    pairs = [('input', 'a1'), ('input', 'b1'), *([('b1', 'b2')] if b2 else []), ('a1', 'join'), (last, 'join'), ('join', 'send'), ('send', 'out')]
    return flow(nodes, pairs)


def lookup(name): return {'lookup': name}


def events(run, node, status=None):
    return [e for e in run['events'] if e.get('node_id') == node and (status is None or e['status'] == status)]


def outcome(run, node):
    found = [e['status'] for e in events(run, node) if e['status'] in ('success', 'failed', 'cancelled')]
    return found[-1] if found else None


def position(run, node, status):
    found = [i for i, e in enumerate(run['events']) if e.get('node_id') == node and e['status'] == status]
    assert len(found) == 1, (node, status, found)
    return found[0]


def posts(lab, path):
    """The JSON bodies the receiver got at this path."""
    return [json.loads(c['body']) for c in lab.provider.calls if c['method'] == 'POST' and c['path'] == path]


def until(predicate, seconds, what):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value: return value
        time.sleep(.02)
    raise AssertionError(f'Timed out waiting for {what}')


def parallel_scenarios(lab):
    def fork_and_join():
        deliver = '/deliver/s22'
        w = fork(lab, '/meet/s22/a', '/meet/s22/b', deliver, b2='/lookup/s22-b2')
        body = lab.client.post('/api/validate', json=w).json()
        assert body['valid'] and body['errors'] == [], body
        id = lab.submit(w); run = lab.wait(id, statuses=('success', 'failed', 'cancelled'))
        assert run['status'] == 'success', run.get('error')
        assert lab.provider.count('/meet/s22/a') == 1 and lab.provider.count('/meet/s22/b') == 1
        assert len(events(run, 'join', 'success')) == 1, events(run, 'join')
        join = position(run, 'join', 'running')
        last = {node: position(run, node, 'success') for node in ('a1', 'b2')}
        assert all(index < join for index in last.values()), {'join_running': join, 'branch_success': last}
        assert posts(lab, deliver) == [lookup('a')], posts(lab, deliver)
        assert json.loads(run['output']) == {'lookup': 's22-b2'}, run['output']
        budget = run['accounting']['action_budget']
        assert budget['counter'] == 4 and sorted(budget['charged']) == ['a1#0', 'b1#0', 'b2#0', 'send#0'], budget
        return {'run_id': id, 'join_runs': 1, 'join_running_event': join, 'branch_success_events': last,
                'deliveries': posts(lab, deliver), 'output': run['output'], 'actions_charged': sorted(budget['charged'])}

    def failure_cancels():
        deliver, held = '/deliver/s23', '/meet/s23/hold-b'
        id = lab.submit(fork(lab, '/meet/s23/fail-a', held, deliver))
        try:
            def settled():
                run = lab.client.get('/api/runs/' + id).json()
                return run if run['status'] in ('success', 'failed', 'cancelled') and outcome(run, 'b1') else None
            run = until(settled, 20, 'the run to end while b1 is held')
            assert held not in lab.provider.answered, 'the held answer was sent before the check'
            assert run['status'] == 'failed' and run['error'] == 'Tool provider returned HTTP 500', (run['status'], run.get('error'))
            assert outcome(run, 'a1') == 'failed' and outcome(run, 'b1') == 'cancelled', (outcome(run, 'a1'), outcome(run, 'b1'))
        finally:
            lab.provider.release('s23/hold-b')
        until(lambda: held in lab.provider.answered, 10, 'the released answer')
        run = lab.client.get('/api/runs/' + id).json()
        assert run['status'] == 'failed' and events(run, 'join') == [], events(run, 'join')
        assert posts(lab, deliver) == [] and lab.provider.count(held) == 1
        return {'run_id': id, 'error': run['error'], 'a1': 'failed', 'b1': 'cancelled while its answer was held',
                'join_events': 0, 'deliveries': 0, 'b1_requests': 1}

    def write_in_branch_refused():
        deliver = '/deliver/s23b'
        w = flow([read(lab, 'a1', '/lookup/s23b-a'), write(lab, 'b1', deliver, 'input.message'), {'id': 'join', 'type': 'join'},
                  {'id': 'out', 'type': 'response', 'inputs': {'text': 'a1.text'}}],
                 [('input', 'a1'), ('input', 'b1'), ('a1', 'join'), ('b1', 'join'), ('join', 'out')])
        refusal = 'b1: external writes belong after the Join.'
        body = lab.client.post('/api/validate', json=w).json()
        assert not body['valid'] and body['errors'] == [refusal], body
        before = len(lab.client.get('/api/runs').json())
        r = lab.client.post('/api/runs', json={'workflow': w, 'message': 'Customer case'})
        assert r.status_code == 422 and r.json()['detail'] == [refusal], r.text
        assert len(lab.client.get('/api/runs').json()) == before
        assert lab.provider.count(deliver) == 0 and lab.provider.count('/lookup/s23b-a') == 0
        return {'validation_errors': body['errors'], 'submission': 422, 'runs_created': 0, 'requests': 0}

    def crash_in_fork():
        deliver, a, held = '/deliver/s24', '/meet/s24/a', '/meet/s24/hold-b'
        id = lab.submit(fork(lab, a, held, deliver))
        try:
            until(lambda: 'a1' in lab.client.get('/api/runs/' + id).json().get('checkpoints', {}), 20, "a1's checkpoint")
            assert held not in lab.provider.answered and lab.provider.count(held) == 1
            lab.worker.kill(); lab.worker.wait()
            prior = lab.client.get('/api/runs/' + id).json()
            assert 'a1' in prior['checkpoints'] and 'b1' not in prior['checkpoints'], sorted(prior['checkpoints'])
            assert posts(lab, deliver) == []
            status, owner, lease_until = lab.read_job(id)
            assert status == 'running' and owner, (status, owner)
        finally:
            lab.provider.release('s24/hold-b')
        # Wait for the real default 30-second lease, without mutating the database.
        lab.start('worker'); started = time.monotonic()
        run = lab.wait(id, statuses=('success', 'failed', 'cancelled'), timeout=60)
        assert run['status'] == 'success', run.get('error')
        assert lab.provider.count(a) == 1, 'the restored branch was read again'
        assert lab.provider.count(held) == 2
        assert len(events(run, 'join', 'success')) == 1, events(run, 'join')
        assert posts(lab, deliver) == [lookup('a')], posts(lab, deliver)
        # The replacement claimed the run only after the killed worker's lease ended.
        claims = [e for e in run['events'] if e.get('kind') == 'run' and e['status'] == 'running']
        assert len(claims) == 2 and claims[0]['worker'] == owner and claims[1]['worker'] != owner, claims
        claimed = datetime.fromisoformat(claims[1]['timestamp']).timestamp()
        assert claimed >= lease_until, {'claimed_before_lease_end_seconds': round(lease_until - claimed, 3)}
        return {'run_id': id, 'checkpoints_at_kill': sorted(prior['checkpoints']), 'deliveries_at_kill': 0,
                'resumed_after_seconds': round(time.monotonic() - started, 1), 'a1_requests': 1, 'b1_requests': 2,
                'join_runs': 1, 'deliveries': posts(lab, deliver), 'lease_expiry': 'real',
                'claimed_after_lease_end_seconds': round(claimed - lease_until, 3)}

    return [
        ('S22', 'A5.3', 'Fork: branches run together, the Join waits for both, one write is sent', fork_and_join),
        ('S23a', 'A5.3', 'A failed branch cancels its held sibling; nothing after the Join is sent', failure_cancels),
        ('S23b', 'A5.3', 'A write inside a branch is refused before any run', write_in_branch_refused),
        ('S24', 'A5.3', 'A worker killed in a fork resumes without repeating finished work', crash_in_fork),
    ]
