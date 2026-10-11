"""Router boundaries through real API/worker/HTTP processes and synthetic providers.

This is fault injection, not a measurement of a live model's semantic accuracy.
No real workspace, external receiver or saved audit artifact is used.
"""
import argparse
import contextlib
import hashlib
import json
import sqlite3
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from scripts.check_production_scenarios import Lab, ROOT


def decision(route):
    return json.dumps({'route': route, 'reason': 'Synthetic classification',
        'missing_fields': ['quantity'] if route == 'clarify' else [],
        'reply': '' if route == 'proceed' else 'Please supply a complete purchase request.'})


class ModelFixture:
    def __init__(self):
        self.responses = []; self.calls = []; self.lock = threading.Lock()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def do_POST(self):
                raw = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with owner.lock:
                    owner.calls.append(raw)
                    text = owner.responses.pop(0) if owner.responses else 'unexpected model call'
                result = json.dumps({'message': {'role': 'assistant', 'content': text},
                    'prompt_eval_count': 7, 'eval_count': 3}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(result)))
                self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(result)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
    def script(self, *responses):
        with self.lock:self.responses = list(responses)
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()


def workflow():
    return {'name': 'Router receiver probe', 'contract': 2, 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'cache', 'type': 'greeting_cache', 'inputs': {'message': 'input.message'}},
        {'id': 'router', 'type': 'request_router', 'inputs': {'message': 'input.message'},
         'config': {'purpose': 'Handle purchase requests', 'provider': 'ollama', 'model': 'router-fixture'}},
        {'id': 'extract', 'type': 'prompt', 'inputs': {'message': 'input.message'}, 'config': {'template': '{message}'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'extract.text'}},
        {'id': 'greeting', 'type': 'response', 'inputs': {'text': 'cache.reply'}},
        {'id': 'declined', 'type': 'response', 'inputs': {'text': 'router.reply'}},
    ], 'edges': [
        {'id': 'a', 'source': 'input', 'target': 'cache'},
        {'id': 'b', 'source': 'cache', 'target': 'router', 'sourceHandle': 'continue'},
        {'id': 'c', 'source': 'cache', 'target': 'greeting', 'sourceHandle': 'reply'},
        {'id': 'd', 'source': 'router', 'target': 'extract', 'sourceHandle': 'continue'},
        {'id': 'e', 'source': 'router', 'target': 'declined', 'sourceHandle': 'reply'},
        {'id': 'f', 'source': 'extract', 'target': 'out'},
    ]}


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    files = [*sorted((ROOT/'backend/app').rglob('*.py')), Path(__file__), ROOT/'scripts/scenarios/lab.py']
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    started = time.time(); rows = []; model = ModelFixture()
    def record(name, action):
        try:details = action(); rows.append({'name': name, 'status': 'passed', 'details': details})
        except Exception as exc:rows.append({'name': name, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'})
        print(name, rows[-1]['status'], flush=True)
    with tempfile.TemporaryDirectory(prefix='relay-router-lab-') as temp:
        lab = Lab(temp, out); lab.env['OLLAMA_BASE_URL'] = model.url
        try:
            lab.boot()
            assert lab.client.post('/api/models', json={'provider': 'ollama', 'model': 'router-fixture'}).status_code == 201
            def routed(route):
                model.script(decision(route)); before = len(model.calls)
                original = '  Hello, buy 3 laptops at $450 each  '
                run_id = lab.submit(workflow(), original); result = lab.wait(run_id)
                assert result['status'] == 'success', result.get('error')
                assert ('extract' in result['checkpoints']) == (route == 'proceed')
                assert len(model.calls) == before+1 and lab.provider.calls == []
                prompt = json.loads(model.calls[-1]['messages'][1]['content'])
                assert prompt['original_message'] == original
                if route == 'proceed':assert result['output'] == original
                return {'run_id': run_id, 'route': route, 'receiver_requests': 0, 'model_calls': 1}
            for route in ('out_of_scope', 'clarify', 'proceed'):
                record(route, lambda route=route:routed(route))
            def malformed():
                model.script('invalid', 'invalid'); before = len(model.calls)
                run_id = lab.submit(workflow(), 'order'); result = lab.wait(run_id)
                assert result['status'] == 'failed' and 'one repair' in result['error']
                assert 'extract' not in result['checkpoints'] and lab.provider.calls == []
                assert len(model.calls) == before+2 and result['accounting']['tokens_spent'] == 20
                return {'run_id': run_id, 'receiver_requests': 0, 'model_calls': 2, 'tokens_spent': 20}
            record('invalid output has no route', malformed)
            def bypass():
                model.script(decision('proceed')); before = len(model.calls)
                w = workflow(); w['nodes'][3] = lab.tool('/deliver/router-bypass', True, False)
                w['nodes'][3]['id'] = 'extract'
                result = lab.client.post('/api/runs', json={'workflow': w, 'message': 'Unrelated request'})
                assert result.status_code == 422 and 'verification' in result.text.lower(), result.text
                assert len(model.calls) == before and lab.provider.count('/deliver/router-bypass') == 0
                return {'http_status': 422, 'receiver_requests': 0,
                    'scope': 'Structural preflight rejects missing verification; runtime verification lands in Tasks 5/6.'}
            record('wrong proceed cannot remove the required check', bypass)
            def crash_replay():
                model.script(decision('proceed')); before = len(model.calls)
                w = workflow(); w['nodes'][3] = lab.tool('/hold/router-resume', False)
                w['nodes'][3]['id'] = 'extract'
                run_id = lab.submit(w, 'Buy 3 laptops')
                deadline = time.monotonic()+20
                while lab.provider.count('/hold/router-resume') == 0 and time.monotonic() < deadline:time.sleep(.05)
                assert lab.provider.count('/hold/router-resume') == 1
                checkpoint = lab.client.get('/api/runs/'+run_id).json()['checkpoints']['router']
                lab.worker.kill(); lab.worker.wait()
                # Recover the isolated job immediately; the killed process cannot renew it.
                with sqlite3.connect(lab.directory/'relay.db') as db:
                    db.execute('UPDATE execution_jobs SET lease_until=0 WHERE run_id=?', (run_id,))
                lab.start('worker'); result = lab.wait(run_id, timeout=30)
                assert result['status'] == 'success', result.get('error')
                assert result['checkpoints']['router'] == checkpoint
                assert len(model.calls) == before+1 and result['accounting']['tokens_spent'] == 10
                assert lab.provider.count('/hold/router-resume') == 2  # Read retry is allowed.
                return {'run_id': run_id, 'model_calls': 1, 'tokens_spent': 10,
                    'worker_sigkill': True, 'receiver_writes': 0}
            record('SIGKILL preserves the paid router checkpoint', crash_replay)
        finally:
            lab.close(); model.close()
    changed = [name for name, digest in hashes.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != digest]
    report = {'started_at': started, 'completed_at': time.time(), 'source_sha256': hashes,
        'source_files_changed': changed, 'deployment': 'isolated API and worker processes, real HTTP/SQLite, synthetic Ollama and receiver',
        'rows': rows, 'passed': len(rows) == 6 and all(row['status'] == 'passed' for row in rows) and not changed}
    (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='evals/results/request-router-step4/receiver')
    return run(Path(parser.parse_args().out).resolve())


if __name__ == '__main__':raise SystemExit(main())
