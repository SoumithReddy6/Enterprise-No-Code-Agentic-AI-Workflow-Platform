"""A5.3 Part 3 design probe: fork scenarios through the deployed API and worker processes.
Uses the gate's Lab with a replacement HTTP fixture (meet barrier, lookups, holds)."""
import contextlib, json, socket, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from scripts.check_production_scenarios import Lab

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)

class Fixture:
    def __init__(self):
        self.calls, self.done, self.arrived = [], [], {}
        self.cond = threading.Condition(); owner = self
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()
                path = urlsplit(self.path).path; parts = path.strip('/').split('/')
                with owner.cond:
                    owner.calls.append({'method': self.command, 'path': path, 'body': body, 't': time.monotonic()})
                code, name = 200, parts[-1]
                if parts[0] == 'meet':
                    group = parts[1]
                    with owner.cond:
                        owner.arrived[group] = owner.arrived.get(group, 0) + 1; owner.cond.notify_all()
                        met = owner.cond.wait_for(lambda: owner.arrived[group] >= 2, 10)
                    if not met: code = 504
                    elif name.startswith('fail'): code = 500
                    elif name.startswith('slow'): time.sleep(5)
                if parts[0] == 'hold': time.sleep(5)
                out = json.dumps({'lookup': name}).encode() if self.command == 'GET' else b'accepted'
                self.send_response(code); self.send_header('Content-Length', str(len(out))); self.send_header('Content-Type', 'application/json'); self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError): self.wfile.write(out)
                with owner.cond: owner.done.append({'path': path, 't': time.monotonic()})
            do_GET = handle_request; do_POST = handle_request
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]
    def count(self, path): return sum(c['path'] == path for c in self.calls)
    def close(self): self.server.shutdown(); self.server.server_close()

def read(lab, id, path, src='input.message'):
    return {'id': id, 'type': 'tool_http', 'inputs': {'input': src}, 'config': {'connection_id': lab.http, 'method': 'GET', 'path': path}}
def write(lab, id, path, src):
    return {'id': id, 'type': 'tool_http', 'inputs': {'input': src}, 'config': {'connection_id': lab.http, 'method': 'POST', 'path': path, 'body': '{input}', 'enable_writes': True, 'approval': False}}
def flow(nodes, edges):
    return {'version': 1, 'name': 'Fork', 'nodes': [{'id': 'input', 'type': 'chat_input'}, *nodes], 'edges': [{'id': f'e{i}', 'source': s, 'target': t} for i, (s, t) in enumerate(edges)]}
def ev(run, node, status=None): return [e for e in run['events'] if e.get('node_id') == node and (status is None or e['status'] == status)]

results = {}
with tempfile.TemporaryDirectory(prefix='relay-p3-probe-') as temp:
    lab = Lab(temp, OUT); lab.provider.close(); fx = Fixture(); lab.provider = fx; lab.env['SCENARIO_FIXTURE_PORT'] = str(fx.port)
    try:
        lab.boot()
        # S22: unequal branches meet at the fixture; after the Join the write reads branch A, the Response reads branch B's last node.
        w = flow([read(lab, 'a1', '/meet/s22/a'), read(lab, 'b1', '/meet/s22/b'), read(lab, 'b2', '/lookup/b2', 'b1.text'), {'id': 'join', 'type': 'join'},
                  write(lab, 'send', '/deliver/s22', 'a1.text'), {'id': 'out', 'type': 'response', 'inputs': {'text': 'b2.text'}}],
                 [('input', 'a1'), ('input', 'b1'), ('b1', 'b2'), ('a1', 'join'), ('b2', 'join'), ('join', 'send'), ('send', 'out')])
        v = lab.client.post('/api/validate', json=w).json()
        id = lab.submit(w); run = lab.wait(id, statuses=('success', 'failed', 'cancelled'))
        sends = [c for c in fx.calls if c['path'] == '/deliver/s22']
        results['S22'] = {'valid': v, 'status': run['status'], 'error': run.get('error'), 'output': run.get('output'),
                          'deliveries': [c['body'] for c in sends], 'join_success': len(ev(run, 'join', 'success')),
                          'meet_arrivals': fx.arrived.get('s22'), 'tokens': run.get('accounting')}
        # S23: branch A fails after both branches started; branch B is slow.
        w = flow([read(lab, 'a1', '/meet/s23/fail-a'), read(lab, 'b1', '/meet/s23/slow-b'), {'id': 'join', 'type': 'join'},
                  write(lab, 'send', '/deliver/s23', 'a1.text'), {'id': 'out', 'type': 'response', 'inputs': {'text': 'b1.text'}}],
                 [('input', 'a1'), ('input', 'b1'), ('a1', 'join'), ('b1', 'join'), ('join', 'send'), ('send', 'out')])
        id = lab.submit(w); run = lab.wait(id, statuses=('success', 'failed', 'cancelled')); finished = time.monotonic()
        slow_done = [d for d in fx.done if d['path'] == '/meet/s23/slow-b']
        time.sleep(5.5); slow_done_later = [d for d in fx.done if d['path'] == '/meet/s23/slow-b']
        results['S23'] = {'status': run['status'], 'error': run.get('error'), 'a1': [e['status'] for e in ev(run, 'a1')], 'b1': [e['status'] for e in ev(run, 'b1')],
                          'join_events': len(ev(run, 'join')), 'deliveries': fx.count('/deliver/s23'),
                          'slow_branch_answered_before_run_failed': bool(slow_done), 'slow_branch_answered_later': bool(slow_done_later)}
        # S23b: a write inside a branch is refused at validation and submission.
        w = flow([read(lab, 'a1', '/lookup/s23b-a'), write(lab, 'b1', '/deliver/s23b', 'input.message'), {'id': 'join', 'type': 'join'}, {'id': 'out', 'type': 'response', 'inputs': {'text': 'a1.text'}}],
                 [('input', 'a1'), ('input', 'b1'), ('a1', 'join'), ('b1', 'join'), ('join', 'out')])
        before = len(lab.client.get('/api/runs').json())
        v = lab.client.post('/api/validate', json=w).json(); r = lab.client.post('/api/runs', json={'workflow': w, 'message': 'x'})
        results['S23b'] = {'validate': v, 'submit': r.status_code, 'submit_body': r.text[:300], 'runs_created': len(lab.client.get('/api/runs').json()) - before,
                           'requests': fx.count('/deliver/s23b') + fx.count('/lookup/s23b-a')}
        # S24: SIGKILL after branch A's checkpoint while branch B's read is outstanding.
        w = flow([read(lab, 'a1', '/lookup/s24-a'), read(lab, 'b1', '/hold/s24-b'), {'id': 'join', 'type': 'join'},
                  write(lab, 'send', '/deliver/s24', 'a1.text'), {'id': 'out', 'type': 'response', 'inputs': {'text': 'b1.text'}}],
                 [('input', 'a1'), ('input', 'b1'), ('a1', 'join'), ('b1', 'join'), ('join', 'send'), ('send', 'out')])
        id = lab.submit(w); deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            prior = lab.client.get('/api/runs/' + id).json()
            if 'a1' in prior.get('checkpoints', {}) and fx.count('/hold/s24-b') == 1: break
            time.sleep(.02)
        lab.worker.kill(); lab.worker.wait(); killed = sorted(prior.get('checkpoints', {}))
        lab.start('worker'); t0 = time.monotonic(); run = lab.wait(id, statuses=('success', 'failed', 'cancelled'), timeout=60)
        results['S24'] = {'checkpoints_at_kill': killed, 'status': run['status'], 'error': run.get('error'), 'resume_seconds': round(time.monotonic() - t0, 1),
                          'a_reads': fx.count('/lookup/s24-a'), 'b_reads': fx.count('/hold/s24-b'), 'deliveries': [c['body'] for c in fx.calls if c['path'] == '/deliver/s24'],
                          'join_success': len(ev(run, 'join', 'success')), 'output': run.get('output')}
    finally:
        lab.close()
(OUT / 'notes.json').write_text(json.dumps(results, indent=2)); print(json.dumps(results, indent=2))
