"""A6.1 Part 3 deployed Repeat scenarios: separate API and worker processes, a model fixture
keyed by model name, RELAY_RUN_TOKEN_LIMIT=100, and the gate's HTTP receiver.

Design: docs/superpowers/specs/2026-11-16-bounded-loops-scenarios-design.md (revision 2,
approved). The models are scripted, so these rows measure loop control, persistence,
budgets, approvals and delivery, never a checker's judgment.

S25: a writer and a checker loop until the checker accepts; the accepted draft is sent once.
S26a: a reply that never meets the rule (on_limit fail) is not sent.
S26b: under on_limit last, a write needs approval; both fallbacks wait and show why.
S26c: a write inside the loop is refused before any run.
S27: a token budget that stops the checker sends no unchecked draft.
S28: a worker killed in an iteration resumes without repeating saved iterations.
S29: the approval after a Repeat shows the accepted draft and reuses the Repeat's checkpoint.
"""
import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN_LIMIT = 100
ROWS = ('s25', 's26a', 's26b-refused', 's26b-limit', 's26b-budget', 's26c', 's27', 's28', 's29')
VERDICT = {'type': 'object', 'required': ['pass', 'feedback'],
           'properties': {'pass': {'type': 'boolean'}, 'feedback': {'type': 'string'}}}
APPROVAL = ('send: a write after r needs approval: true, because r can return an answer that did not meet its '
            'stop rule (on_limit: last).')
LIMIT_REASON = 'r reached 2 iterations without meeting its stop rule; the last answer is returned.'
HALT = 'Run token budget exhausted; raise RELAY_RUN_TOKEN_LIMIT.'
REJECTED = 'Approval rejected. No external request was sent.'


class LoopModels:
    """The worker's Ollama endpoint, keyed by model name.

    script(model, [(content, tokens, hold), ...]) queues the answers. A `hold` answer waits
    until release(), at most 120 s. An unexpected call answers text that fails the checker's
    schema, so a row never passes by accident."""
    def __init__(self):
        self.scripts, self.calls, self.lock = {}, [], threading.Lock()
        self.released = threading.Event()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                raw = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                model = raw.get('model')
                with owner.lock:
                    owner.calls.append({'model': model, 'messages': raw.get('messages')})
                    queue = owner.scripts.get(model, [])
                    content, tokens, hold = queue.pop(0) if queue else ('unexpected model call', 1, False)
                if hold: owner.released.wait(120)
                body = json.dumps({'message': {'role': 'assistant', 'content': content},
                                   'prompt_eval_count': tokens // 2, 'eval_count': tokens - tokens // 2}).encode()
                try:
                    self.send_response(200); self.send_header('Content-Length', str(len(body)))
                    self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
                except OSError:
                    pass  # The killed worker's connection is gone.
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
    def script(self, model, answers):
        with self.lock: self.scripts[model] = list(answers)
    def count(self, model):
        with self.lock: return sum(c['model'] == model for c in self.calls)
    def requests(self, model):
        with self.lock: return [c['messages'] for c in self.calls if c['model'] == model]
    def release(self): self.released.set()
    def close(self):
        self.released.set(); self.server.shutdown(); self.server.server_close(); self.thread.join()


def models_for_lab():
    """The writer and checker model names that the lab registers, one pair per row."""
    return [f'{row}-{role}' for row in ROWS for role in ('writer', 'judge')]


def verdict(ok, feedback):
    return json.dumps({'pass': ok, 'feedback': feedback})


def repeat_flow(lab, row, max_iterations=3, on_limit='fail', approval=False, write_in_body=False):
    """input -> r (body writer, check judge, until pass eq true) -> send POST /deliver/<row> -> out."""
    deliver = f'/deliver/{row}'
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
             # The approval setting is explicit, so no row depends on the authentication default.
             {'id': 'send', 'type': 'tool_http', 'inputs': {'input': 'r.text'},
              'config': {'connection_id': lab.http, 'method': 'POST', 'path': deliver, 'body': '{input}',
                         'enable_writes': True, 'approval': approval}},
             {'id': 'out', 'type': 'response', 'inputs': {'text': 'r.text'}}]
    edges = [{'id': 'e0', 'source': 'input', 'target': 'r'}, {'id': 'e1', 'source': 'r', 'target': 'send'},
             {'id': 'e2', 'source': 'send', 'target': 'out'},
             {'id': 'e3', 'source': 'r', 'target': 'writer', 'kind': 'loop', 'sourceHandle': 'body', 'targetHandle': 'input'},
             {'id': 'e4', 'source': 'r', 'target': 'judge', 'kind': 'loop', 'sourceHandle': 'check', 'targetHandle': 'input'}]
    return {'version': 1, 'name': f'Repeat {row}', 'nodes': nodes, 'edges': edges}


def posts(lab, row):
    return [json.loads(c['body']) for c in lab.provider.calls if c['method'] == 'POST' and c['path'] == f'/deliver/{row}']


def rows_of(run):
    return run.get('loop_progress', {}).get('r')


def summary_of(run):
    return json.loads(run['checkpoints']['r']['summary'])


def node_events(run, node):
    """The node's own events (not its iterations' item or body events)."""
    return [e for e in run['events'] if e.get('node_id') == node and e.get('kind') not in ('loop_item', 'loop_summary') and not e.get('loop_node_id')]


def statuses(run, node):
    return [e['status'] for e in node_events(run, node)]


def row(index, value, ok, feedback):
    return {'index': index, 'status': 'success', 'value': value, 'verdict': verdict(ok, feedback), 'met': ok}


def repeat_scenarios(lab, models):
    finished = ('success', 'failed', 'cancelled')

    def calls(name):
        return {'writer': models.count(f'{name}-writer'), 'judge': models.count(f'{name}-judge')}

    def refused(w, message):
        """Validate and submission refuse with exactly `message`; no run exists afterwards."""
        body = lab.client.post('/api/validate', json=w).json()
        assert body['valid'] is False and body['errors'] == [message], body
        before = len(lab.client.get('/api/runs').json())
        r = lab.client.post('/api/runs', json={'workflow': w, 'message': 'Customer case'})
        assert r.status_code == 422 and r.json()['detail'] == [message], r.text
        assert len(lab.client.get('/api/runs').json()) == before
        return body['errors']

    def drafted_checked_sent():
        name = 's25'
        draft1, draft2 = 'Draft 1: your refund is on its way.', 'Draft 2: refund R-42 is on its way.'
        models.script(f'{name}-writer', [(draft1, 15, False), (draft2, 15, False)])
        models.script(f'{name}-judge', [(verdict(False, 'add the refund number'), 15, False), (verdict(True, 'good'), 15, False)])
        w = repeat_flow(lab, name)
        body = lab.client.post('/api/validate', json=w).json()
        assert body['valid'] is True and body['errors'] == [], body
        id = lab.submit(w); run = lab.wait(id, statuses=finished)
        assert run['status'] == 'success', run.get('error')
        assert calls(name) == {'writer': 2, 'judge': 2}, calls(name)
        second = models.requests(f'{name}-writer')[1][-1]['content']
        expected = json.dumps({'input': 'Customer case', 'previous': draft1, 'feedback': verdict(False, 'add the refund number')},
                              separators=(',', ':'))
        assert second == expected, second
        assert rows_of(run) == {'0': row(0, draft1, False, 'add the refund number'), '1': row(1, draft2, True, 'good')}, rows_of(run)
        assert summary_of(run) == {'iterations': 2, 'max_iterations': 3, 'met': True, 'stopped_by': 'rule',
                                   'truncated': False, 'truncation_reason': ''}, summary_of(run)
        order = [(e['node_id'], e['status']) for e in run['events'] if e in node_events(run, 'r') + node_events(run, 'send')]
        assert order.index(('r', 'success')) < order.index(('send', 'running')), order
        assert posts(lab, name) == [{'input': draft2}], posts(lab, name)
        accounting = run['accounting']
        assert accounting['tokens_spent'] == 60, accounting
        assert accounting['action_budget']['counter'] == 1 and accounting['action_budget']['charged'] == ['send#0'], accounting
        return {'run_id': id, 'calls': calls(name), 'second_writer_input': json.loads(second), 'iterations': 2,
                'deliveries': posts(lab, name), 'tokens_spent': 60, 'actions_charged': ['send#0']}

    def never_accepted_fail():
        name = 's26a'
        models.script(f'{name}-writer', [('Draft 1', 15, False), ('Draft 2', 15, False)])
        models.script(f'{name}-judge', [(verdict(False, 'too vague'), 15, False), (verdict(False, 'still vague'), 15, False)])
        id = lab.submit(repeat_flow(lab, name, max_iterations=2)); run = lab.wait(id, statuses=finished)
        assert run['status'] == 'failed' and run['error'] == 'r: the stop rule was not met after 2 iterations.', (run['status'], run.get('error'))
        assert calls(name) == {'writer': 2, 'judge': 2}, calls(name)
        assert statuses(run, 'send') == [] and posts(lab, name) == []
        return {'run_id': id, 'error': run['error'], 'calls': calls(name), 'send_events': 0, 'deliveries': 0}

    def fallback_waits(name, writer, judge, expected_summary, offered):
        """on_limit last with approval: the run waits, the Repeat's checkpoint says why, and a
        rejection sends nothing. The run-level truncated is set only when a run succeeds."""
        models.script(f'{name}-writer', writer); models.script(f'{name}-judge', judge)
        w = repeat_flow(lab, name, max_iterations=2 if name.endswith('limit') else 3, on_limit='last', approval=True)
        assert lab.client.post('/api/validate', json=w).json()['valid'] is True
        id = lab.submit(w); run = lab.wait(id)
        assert run['status'] == 'awaiting_approval', (run['status'], run.get('error'))
        assert summary_of(run) == expected_summary, summary_of(run)
        pending = [a for a in run['approvals'] if a['status'] == 'pending']
        assert len(pending) == 1 and pending[0]['payload']['body'] == {'input': offered}, run['approvals']
        assert posts(lab, name) == [] and 'truncated' not in run, sorted(run)
        waiting = calls(name)
        assert lab.decide(id, pending[0], 'reject').status_code == 200
        run = lab.wait(id, statuses=finished)
        assert run['status'] == 'cancelled' and run['error'] == REJECTED, (run['status'], run.get('error'))
        assert posts(lab, name) == [] and 'truncated' not in run and calls(name) == waiting, (sorted(run), calls(name))
        assert lab.client.post(f'/api/runs/{id}/resume').status_code == 409
        return {'run_id': id, 'calls': waiting, 'stopped_by': expected_summary['stopped_by'], 'offered': offered,
                'deliveries': 0, 'run_truncated_key': False}

    def unaccepted_needs_approval():
        refusal = refused(repeat_flow(lab, 's26b-refused', max_iterations=2, on_limit='last'), APPROVAL)
        assert calls('s26b-refused') == {'writer': 0, 'judge': 0} and posts(lab, 's26b-refused') == []
        limit = fallback_waits(
            's26b-limit', [('Draft 1', 15, False), ('Draft 2', 15, False)],
            [(verdict(False, 'too vague'), 15, False), (verdict(False, 'still vague'), 15, False)],
            {'iterations': 2, 'max_iterations': 2, 'met': False, 'stopped_by': 'limit', 'truncated': True,
             'truncation_reason': LIMIT_REASON}, 'Draft 2')
        assert limit['calls'] == {'writer': 2, 'judge': 2}, limit
        budget = fallback_waits(
            's26b-budget', [('Draft 1', 40, False), ('Draft 2', 40, False)], [(verdict(False, 'add the date'), 40, False)],
            {'iterations': 1, 'max_iterations': 3, 'met': False, 'stopped_by': 'budget', 'truncated': True,
             'truncation_reason': f'r: the run budget stopped iteration 2 before it was checked; the answer from iteration 1 is returned. {HALT}'},
            'Draft 1')
        assert budget['calls'] == {'writer': 2, 'judge': 1}, budget
        return {'refused': refusal, 'limit': limit, 'budget': budget}

    def write_in_loop():
        message = 'writer: external writes belong after the Repeat.'
        errors = refused(repeat_flow(lab, 's26c', write_in_body=True), message)
        assert posts(lab, 's26c') == [] and calls('s26c') == {'writer': 0, 'judge': 0}
        return {'validation_errors': errors, 'submission': 422, 'runs_created': 0, 'requests': 0, 'model_calls': 0}

    def budget_stops_checker():
        name = 's27'
        models.script(f'{name}-writer', [('Draft 1', 40, False), ('Draft 2', 40, False), ('Draft 3', 40, False)])
        models.script(f'{name}-judge', [(verdict(False, 'add the date'), 40, False), (verdict(True, 'good'), 40, False)])
        id = lab.submit(repeat_flow(lab, name)); run = lab.wait(id, statuses=finished)
        assert run['status'] == 'failed' and run['error'] == f'r: the run budget stopped iteration 2 before it was checked. {HALT}', (run['status'], run.get('error'))
        assert rows_of(run)['1'] == {'index': 1, 'status': 'interrupted', 'budget_halt': 'tokens', 'calls': 1}, rows_of(run)
        assert calls(name) == {'writer': 2, 'judge': 1}, calls(name)
        assert posts(lab, name) == [] and statuses(run, 'send') == []
        assert run['accounting']['tokens_spent'] == 120, run['accounting']
        assert run['token_limit'] == {'policy': 'admission', 'limit': TOKEN_LIMIT, 'spent': 120, 'over': 20}, run['token_limit']
        return {'run_id': id, 'error': run['error'], 'calls': calls(name), 'deliveries': 0, 'token_limit': run['token_limit']}

    def crash_in_iteration():
        name = 's28'
        models.released.clear()
        models.script(f'{name}-writer', [('Draft 1', 15, False), ('Draft 2 (held)', 15, True), ('Draft 2', 15, False)])
        models.script(f'{name}-judge', [(verdict(False, 'add the date'), 15, False), (verdict(True, 'good'), 15, False)])
        id = lab.submit(repeat_flow(lab, name))
        try:
            deadline = time.monotonic() + 30
            while models.count(f'{name}-writer') < 2:
                assert time.monotonic() < deadline, 'the writer never opened iteration 2'
                time.sleep(.02)
            lab.worker.kill(); lab.worker.wait()
            prior = lab.client.get('/api/runs/' + id).json()
            saved = rows_of(prior)
            assert saved == {'0': row(0, 'Draft 1', False, 'add the date')}, saved
            assert 'r' not in prior['checkpoints'] and posts(lab, name) == []
            assert prior['accounting']['tokens_spent'] == 30, prior['accounting']
            status, owner, lease_until = lab.read_job(id)
            assert status == 'running' and owner, (status, owner)
        finally:
            models.release()
        # Wait for the real default 30-second lease, without changing the database.
        lab.start('worker'); started = time.monotonic()
        run = lab.wait(id, statuses=finished, timeout=60)
        assert run['status'] == 'success', run.get('error')
        assert calls(name) == {'writer': 3, 'judge': 2}, calls(name)
        # The saved iteration is kept exactly: a resume that ignored it would also make 3 and
        # 2 calls, but would overwrite row 0 and count one iteration.
        assert rows_of(run) == {**saved, '1': row(1, 'Draft 2', True, 'good')}, rows_of(run)
        assert summary_of(run)['iterations'] == 2, summary_of(run)
        assert run['accounting']['tokens_spent'] == 60, run['accounting']
        assert posts(lab, name) == [{'input': 'Draft 2'}], posts(lab, name)
        claims = [e for e in run['events'] if e.get('kind') == 'run' and e['status'] == 'running']
        assert len(claims) == 2 and claims[0]['worker'] == owner and claims[1]['worker'] != owner, claims
        claimed = datetime.fromisoformat(claims[1]['timestamp']).timestamp()
        assert claimed >= lease_until, {'claimed_before_lease_end_seconds': round(lease_until - claimed, 3)}
        return {'run_id': id, 'rows_at_kill': 1, 'deliveries_at_kill': 0, 'tokens_at_kill': 30,
                'resumed_after_seconds': round(time.monotonic() - started, 1), 'calls': calls(name),
                'deliveries': posts(lab, name), 'tokens_spent': 60, 'lease_expiry': 'real',
                'claimed_after_lease_end_seconds': round(claimed - lease_until, 3)}

    def approval_after_repeat():
        name = 's29'
        accepted = 'Draft 2: refund R-42.'
        models.script(f'{name}-writer', [('Draft 1', 15, False), (accepted, 15, False)])
        models.script(f'{name}-judge', [(verdict(False, 'add the refund number'), 15, False), (verdict(True, 'good'), 15, False)])
        id = lab.submit(repeat_flow(lab, name, approval=True)); run = lab.wait(id)
        assert run['status'] == 'awaiting_approval', (run['status'], run.get('error'))
        pending = [a for a in run['approvals'] if a['status'] == 'pending']
        assert len(pending) == 1 and pending[0]['payload']['body'] == {'input': accepted}, run['approvals']
        assert posts(lab, name) == []
        before = len(run['events'])
        assert lab.decide(id, pending[0]).status_code == 200
        run = lab.wait(id, statuses=finished)
        assert run['status'] == 'success', run.get('error')
        assert posts(lab, name) == [{'input': accepted}], posts(lab, name)
        assert calls(name) == {'writer': 2, 'judge': 2}, calls(name)
        # Only this proves the checkpoint reuse: a Repeat rebuilt from its saved rows also
        # calls no model.
        resumed = [e for e in run['events'][before:] if e in node_events(run, 'r')]
        assert [(e['status'], e.get('cached')) for e in resumed] == [('success', True)], resumed
        return {'run_id': id, 'reviewed': pending[0]['payload']['body'], 'deliveries': posts(lab, name),
                'calls': calls(name), 'r_after_approval': [{'status': 'success', 'cached': True}]}

    return [
        ('S25', 'A6.1', 'A support reply is drafted, checked and sent once', drafted_checked_sent),
        ('S26a', 'A6.1', 'A reply that never meets the rule is not sent', never_accepted_fail),
        ('S26b', 'A6.1', 'An unaccepted reply under on_limit last waits for a person', unaccepted_needs_approval),
        ('S26c', 'A6.1', 'A write inside the loop is refused before any run', write_in_loop),
        ('S27', 'A6.1-budget', 'A token budget that stops the checker sends no unchecked draft', budget_stops_checker),
        ('S28', 'A6.1-recovery', 'A worker killed in an iteration resumes without repeating saved iterations', crash_in_iteration),
        ('S29', 'A6.1-approvals', 'The approval after a Repeat shows the accepted draft', approval_after_repeat),
    ]
