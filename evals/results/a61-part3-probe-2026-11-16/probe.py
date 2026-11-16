"""A6.1 Part 3 design probe: Repeat through the deployed API and worker processes.

Uses the gate's Lab with a model fixture keyed by model name: each row scripts its writer and
checker answers, the tokens each answer reports, and which request is held open. The worker
runs with RELAY_RUN_TOKEN_LIMIT=100. Writes go to the gate's HTTP receiver.
Usage (from a clean archive): PYTHONPATH=. .venv/bin/python <this file> <out dir>
"""
import json, sys, tempfile, threading, time, traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.check_production_scenarios import Lab

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
VERDICT = {'type': 'object', 'required': ['pass', 'feedback'],
           'properties': {'pass': {'type': 'boolean'}, 'feedback': {'type': 'string'}}}


class Models:
    """POST from the worker's Ollama client. script(model, [(content, tokens, hold), ...])."""
    def __init__(self):
        self.scripts, self.calls, self.lock = {}, [], threading.Lock()
        self.release = threading.Event()
        owner = self
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                raw = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                model = raw.get('model')
                with owner.lock:
                    owner.calls.append({'model': model, 'messages': raw.get('messages'), 't': time.monotonic()})
                    queue = owner.scripts.get(model, [])
                    content, tokens, hold = queue.pop(0) if queue else ('unexpected model call', 1, False)
                if hold: owner.release.wait(120)
                body = json.dumps({'message': {'role': 'assistant', 'content': content},
                                   'prompt_eval_count': tokens // 2, 'eval_count': tokens - tokens // 2}).encode()
                try:
                    self.send_response(200); self.send_header('Content-Length', str(len(body)))
                    self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
                except OSError: pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
    def script(self, model, answers):
        with self.lock: self.scripts[model] = list(answers)
    def count(self, model):
        with self.lock: return sum(c['model'] == model for c in self.calls)
    def requests(self, model):
        with self.lock: return [c['messages'] for c in self.calls if c['model'] == model]
    def close(self):
        self.release.set(); self.server.shutdown(); self.server.server_close()


def verdict(ok, feedback):
    return json.dumps({'pass': ok, 'feedback': feedback})


def repeat_flow(lab, row, deliver, max_iterations=3, on_limit='fail', approval=False, write_in_body=False):
    writer = {'id': 'writer', 'type': 'agent', 'config': {'provider': 'ollama', 'model': f'{row}-writer', 'system': 'Write a customer reply.'}}
    if write_in_body:
        writer = {'id': 'writer', 'type': 'tool_http', 'config': {'connection_id': lab.http, 'method': 'POST', 'path': deliver,
                                                                'body': '{input}', 'enable_writes': True, 'approval': False}}
    nodes = [{'id': 'input', 'type': 'chat_input'},
             {'id': 'r', 'type': 'repeat', 'inputs': {'input': 'input.message'},
              'config': {'body': 'writer', 'check': 'judge', 'max_iterations': max_iterations, 'on_limit': on_limit,
                         'until': {'operator': 'eq', 'field': 'pass', 'compare_to': 'true'}}},
             writer,
             {'id': 'judge', 'type': 'agent', 'config': {'provider': 'ollama', 'model': f'{row}-judge', 'system': 'Judge the reply.',
                                                         'role': 'critic', 'output_schema': VERDICT}},
             {'id': 'send', 'type': 'tool_http', 'inputs': {'input': 'r.text'},
              'config': {'connection_id': lab.http, 'method': 'POST', 'path': deliver, 'body': '{input}',
                         'enable_writes': True, 'approval': approval}},
             {'id': 'out', 'type': 'response', 'inputs': {'text': 'r.text'}}]
    edges = [{'id': 'e0', 'source': 'input', 'target': 'r'}, {'id': 'e1', 'source': 'r', 'target': 'send'},
             {'id': 'e2', 'source': 'send', 'target': 'out'},
             {'id': 'e3', 'source': 'r', 'target': 'writer', 'kind': 'loop', 'sourceHandle': 'body', 'targetHandle': 'input'},
             {'id': 'e4', 'source': 'r', 'target': 'judge', 'kind': 'loop', 'sourceHandle': 'check', 'targetHandle': 'input'}]
    return {'version': 1, 'name': f'Repeat {row}', 'nodes': nodes, 'edges': edges}


def posts(lab, path):
    return [c['body'] for c in lab.provider.calls if c['method'] == 'POST' and c['path'] == path]


def facts(run):
    """What a row would read: status, error, truncation, rows, summary, output, events of r and send."""
    summary = (run.get('checkpoints', {}).get('r') or {}).get('summary')
    return {'status': run['status'], 'error': run.get('error'), 'truncated': run.get('truncated'),
            'truncation': run.get('truncation') or run.get('truncation_reason'),
            'rows': run.get('loop_progress', {}).get('r'), 'summary': json.loads(summary) if summary else None,
            'output': run.get('output'), 'tokens_spent': run.get('accounting', {}).get('tokens_spent'),
            'action_budget': run.get('accounting', {}).get('action_budget'),
            'r_events': [e['status'] for e in run['events'] if e.get('node_id') == 'r' and e.get('kind') != 'loop_item' and not e.get('loop_node_id')],
            'loop_item_events': sum(1 for e in run['events'] if e.get('kind') == 'loop_item' and e.get('node_id') == 'r'),
            'send_events': [e['status'] for e in run['events'] if e.get('node_id') == 'send'],
            'approvals': [{'status': a['status'], 'body': a['payload'].get('body')} for a in run.get('approvals', [])]}


def main():
    models = Models(); notes = {}
    with tempfile.TemporaryDirectory(prefix='relay-repeat-probe-') as temp:
        lab = Lab(temp, OUT); lab.env['OLLAMA_BASE_URL'] = models.url; lab.env['RELAY_RUN_TOKEN_LIMIT'] = '100'
        try:
            lab.boot()
            for row in ('p25', 'p26a', 'p26b', 'p26c', 'p27a', 'p27b', 'p28', 'p29'):
                for role in ('writer', 'judge'):
                    r = lab.client.post('/api/models', json={'provider': 'ollama', 'model': f'{row}-{role}'}); assert r.status_code == 201, r.text

            def probe(name, fn):
                try: notes[name] = fn()
                except Exception: notes[name] = {'probe_error': traceback.format_exc()}
                print(name, json.dumps(notes[name])[:600], flush=True)

            def p25():  # writer, checker, one rejection, then accepted; one write after the Repeat
                models.script('p25-writer', [('Draft 1: your refund is on its way.', 15, False), ('Draft 2: refund R-42 is on its way.', 15, False)])
                models.script('p25-judge', [(verdict(False, 'add the refund number'), 15, False), (verdict(True, 'good'), 15, False)])
                w = repeat_flow(lab, 'p25', '/deliver/p25')
                v = lab.client.post('/api/validate', json=w).json()
                id = lab.submit(w); run = lab.wait(id, statuses=('success', 'failed', 'cancelled'))
                second = models.requests('p25-writer')[1] if models.count('p25-writer') > 1 else None
                return {'valid': v['valid'], 'errors': v['errors'], **facts(run), 'posts': posts(lab, '/deliver/p25'),
                        'calls': {'writer': models.count('p25-writer'), 'judge': models.count('p25-judge')},
                        'second_writer_user_message': second[-1]['content'] if second else None}

            def p26a():  # never accepted, on_limit fail, max 2
                models.script('p26a-writer', [('Draft 1', 15, False), ('Draft 2', 15, False)])
                models.script('p26a-judge', [(verdict(False, 'too vague'), 15, False), (verdict(False, 'still vague'), 15, False)])
                id = lab.submit(repeat_flow(lab, 'p26a', '/deliver/p26a', max_iterations=2)); run = lab.wait(id, statuses=('success', 'failed', 'cancelled'))
                return {**facts(run), 'posts': posts(lab, '/deliver/p26a'), 'calls': {'writer': models.count('p26a-writer'), 'judge': models.count('p26a-judge')}}

            def p26b():  # never accepted, on_limit last, max 2, write after the Repeat
                models.script('p26b-writer', [('Draft 1', 15, False), ('Draft 2', 15, False)])
                models.script('p26b-judge', [(verdict(False, 'too vague'), 15, False), (verdict(False, 'still vague'), 15, False)])
                id = lab.submit(repeat_flow(lab, 'p26b', '/deliver/p26b', max_iterations=2, on_limit='last')); run = lab.wait(id)
                return {**facts(run), 'posts': posts(lab, '/deliver/p26b'), 'calls': {'writer': models.count('p26b-writer'), 'judge': models.count('p26b-judge')}}

            def p26c():  # a write inside the Repeat body
                w = repeat_flow(lab, 'p26c', '/deliver/p26c', write_in_body=True)
                v = lab.client.post('/api/validate', json=w).json()
                before = len(lab.client.get('/api/runs').json())
                r = lab.client.post('/api/runs', json={'workflow': w, 'message': 'Customer case'})
                return {'valid': v['valid'], 'errors': v['errors'], 'submission': r.status_code, 'detail': r.json().get('detail'),
                        'runs_created': len(lab.client.get('/api/runs').json()) - before, 'posts': posts(lab, '/deliver/p26c'),
                        'calls': {'judge': models.count('p26c-judge')}}

            def budget(row, on_limit):  # 40 tokens per call, limit 100: the checker of iteration 2 is refused
                models.script(f'{row}-writer', [('Draft 1', 40, False), ('Draft 2', 40, False), ('Draft 3', 40, False)])
                models.script(f'{row}-judge', [(verdict(False, 'add the date'), 40, False), (verdict(True, 'good'), 40, False)])
                id = lab.submit(repeat_flow(lab, row, f'/deliver/{row}', on_limit=on_limit)); run = lab.wait(id)
                return {**facts(run), 'posts': posts(lab, f'/deliver/{row}'), 'calls': {'writer': models.count(f'{row}-writer'), 'judge': models.count(f'{row}-judge')}}

            def p28():  # SIGKILL while the writer's iteration-2 request is held; one write after the Repeat
                models.release.clear()
                models.script('p28-writer', [('Draft 1', 15, False), ('Draft 2 (held)', 15, True), ('Draft 2', 15, False)])
                models.script('p28-judge', [(verdict(False, 'add the date'), 15, False), (verdict(True, 'good'), 15, False)])
                id = lab.submit(repeat_flow(lab, 'p28', '/deliver/p28'))
                deadline = time.monotonic() + 30
                while models.count('p28-writer') < 2 and time.monotonic() < deadline: time.sleep(.02)
                lab.worker.kill(); lab.worker.wait()
                prior = lab.client.get('/api/runs/' + id).json()
                status, owner, lease_until = lab.read_job(id)
                at_kill = {'rows': prior.get('loop_progress', {}).get('r'), 'r_checkpoint': 'r' in prior.get('checkpoints', {}),
                           'posts': posts(lab, '/deliver/p28'), 'tokens_spent': prior.get('accounting', {}).get('tokens_spent'),
                           'job': [status, bool(owner)]}
                models.release.set()
                lab.start('worker'); started = time.monotonic()
                run = lab.wait(id, statuses=('success', 'failed', 'cancelled'), timeout=60)
                claims = [e for e in run['events'] if e.get('kind') == 'run' and e['status'] == 'running']
                claimed = datetime.fromisoformat(claims[-1]['timestamp']).timestamp() if claims else None
                return {'at_kill': at_kill, **facts(run), 'posts': posts(lab, '/deliver/p28'),
                        'calls': {'writer': models.count('p28-writer'), 'judge': models.count('p28-judge')},
                        'resumed_after_seconds': round(time.monotonic() - started, 1),
                        'claims': [(c['worker'] == owner) for c in claims],
                        'claimed_after_lease_end_seconds': round(claimed - lease_until, 3) if claimed else None}

            def p29():  # an approval-required write after the Repeat
                models.script('p29-writer', [('Draft 1', 15, False), ('Draft 2: refund R-42.', 15, False)])
                models.script('p29-judge', [(verdict(False, 'add the refund number'), 15, False), (verdict(True, 'good'), 15, False)])
                id = lab.submit(repeat_flow(lab, 'p29', '/deliver/p29', approval=True)); run = lab.wait(id)
                waiting = facts(run); sent_before = len(posts(lab, '/deliver/p29'))
                a = run['approvals'][0]; assert lab.decide(id, a).status_code == 200
                run = lab.wait(id, statuses=('success', 'failed', 'cancelled'))
                return {'waiting': waiting, 'posts_before_approval': sent_before, **facts(run), 'posts': posts(lab, '/deliver/p29'),
                        'calls': {'writer': models.count('p29-writer'), 'judge': models.count('p29-judge')}}

            probe('P25', p25); probe('P26a', p26a); probe('P26b', p26b); probe('P26c', p26c)
            probe('P27a', lambda: budget('p27a', 'fail')); probe('P27b', lambda: budget('p27b', 'last'))
            probe('P29', p29); probe('P28', p28)
            (OUT / 'runs.json').write_text(json.dumps([lab.client.get('/api/runs/' + i['id']).json() for i in lab.client.get('/api/runs').json()], indent=1))
            (OUT / 'provider-requests.json').write_text(json.dumps(lab.provider.calls, indent=1))
        finally:
            lab.close(); models.close()
    (OUT / 'notes.json').write_text(json.dumps(notes, indent=1, ensure_ascii=False) + '\n')


main()
