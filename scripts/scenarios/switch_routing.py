"""A5.2 deployed Switch scenarios: separate API and worker processes, a model fixture and
local HTTP receivers. Support requests arrive as structured JSON, so the support
template's source-field rules pass; this measures routing and enforcement, never prose
classification quality.

S20: a checked support category selects exactly one of four receivers.
S21: forged, unchecked and misleadingly named routes send nothing on any other path.
"""
import copy
import json
import sqlite3

from scripts.check_production_scenarios import ROOT

SUPPORT = json.loads((ROOT / 'examples/request-admission/support.json').read_text())
MODEL = 'switch-fixture'
ROUTES = ('billing', 'technical', 'account')


def reading(category):
    return json.dumps({'type': 'request', 'values': {'summary': 'Invoice download is broken', 'category': category}, 'reply': ''})


def message(category):
    return json.dumps({'summary': 'Invoice download is broken', 'category': category}, separators=(',', ':'))


def support_routing(lab, prefix, cases=None, approval=(), value='reader.text', middle=()):
    """input → Request reader (support template) → [middle] → Switch on category → one HTTP
    receiver per route; the reader's reply ends at a Response."""
    reader = copy.deepcopy(next(n for n in SUPPORT['nodes'] if n['type'] == 'request_reader'))
    reader['config'].update(model=MODEL, examples=[])
    cases = cases or [{'id': r, 'label': r.title(), 'when': {'field': 'category', 'operator': 'eq', 'compare_to': r}} for r in ROUTES]
    nodes = [{'id': 'input', 'type': 'chat_input'}, reader, *middle,
             {'id': 'route', 'type': 'switch', 'inputs': {'value': value}, 'config': {'cases': cases}},
             {'id': 'replied', 'type': 'response', 'inputs': {'text': 'reader.reply'}}]
    chain = ['reader', *(m['id'] for m in middle), 'route']
    edges = [{'id': 'a', 'source': 'input', 'target': 'reader'}, {'id': 'r', 'source': 'reader', 'target': 'replied', 'sourceHandle': 'reply'}]
    edges += [{'id': f'c{i}', 'source': s, 'target': t, **({'sourceHandle': 'continue'} if s == 'reader' else {})}
              for i, (s, t) in enumerate(zip(chain, chain[1:]))]
    for route in [*(c['id'] for c in cases), 'default']:
        send, done = f'send_{route}', f'done_{route}'
        nodes += [{'id': send, 'type': 'tool_http', 'inputs': {'input': 'reader.text'},
                   'config': {'connection_id': lab.http, 'method': 'POST', 'path': f'{prefix}/{route}', 'body': '{input}',
                              'enable_writes': True, 'approval': route in approval}},
                  {'id': done, 'type': 'response', 'inputs': {'text': f'{send}.text'}}]
        edges += [{'id': 'to_' + route, 'source': 'route', 'target': send, 'sourceHandle': route},
                  {'id': 'end_' + route, 'source': send, 'target': done}]
    return {'version': 1, 'contract': 2, 'name': 'Support routing', 'nodes': nodes, 'edges': edges}


def sends(lab, prefix):
    return {c['path']: json.loads(c['body']) for c in lab.provider.calls if c['method'] == 'POST' and c['path'].startswith(prefix + '/')}


def post_count(lab, prefix):
    return sum(1 for c in lab.provider.calls if c['method'] == 'POST' and c['path'].startswith(prefix + '/'))


def switch_scenarios(lab, fixture):
    def routed():
        prefix, taken = '/deliver/switch', {}
        for category, route in [('billing', 'billing'), ('technical', 'technical')]:
            before = post_count(lab, prefix)
            fixture.script(reading(category))
            id = lab.submit(support_routing(lab, prefix), message(category))
            run = lab.wait(id)
            assert run['status'] == 'success', run.get('error')
            assert run['checkpoints']['route']['branch'] == route, run['checkpoints']['route']
            assert post_count(lab, prefix) == before + 1, 'exactly one receiver gets exactly one request'
            last = [c for c in lab.provider.calls if c['method'] == 'POST' and c['path'].startswith(prefix + '/')][-1]
            assert last['path'] == f'{prefix}/{route}' and json.loads(last['body']) == json.loads(message(category)), last
            taken[category] = {'run_id': id, 'route': route, 'receiver': last['path']}
        assert set(sends(lab, prefix)) == {f'{prefix}/billing', f'{prefix}/technical'}
        return {'routes': taken, 'receivers_called': sorted(sends(lab, prefix)), 'model_reads': len(taken)}

    def forged_checkpoint():
        # A saved route is changed in the database while the run waits for write approval;
        # restore recomputes it from the checked input and refuses before any send.
        prefix = '/deliver/forged'
        fixture.script(reading('billing'))
        id = lab.submit(support_routing(lab, prefix, approval=('billing',)), message('billing'))
        run = lab.wait(id)
        assert run['status'] == 'awaiting_approval' and run['checkpoints']['route']['branch'] == 'billing', run.get('error')
        with sqlite3.connect(lab.directory / 'relay.db') as db:  # Scenario lab only: a deliberate forgery.
            data = json.loads(db.execute('SELECT data FROM runs WHERE id=?', (id,)).fetchone()[0])
            data['checkpoints']['route']['branch'] = 'technical'
            db.execute('UPDATE runs SET data=? WHERE id=?', (json.dumps(data), id))
        assert lab.decide(id, run['approvals'][0]).status_code == 200
        run = lab.wait(id, statuses=('success', 'failed', 'cancelled'))
        assert run['status'] == 'failed' and 'Switch route does not match' in run['error'], (run['status'], run.get('error'))
        assert post_count(lab, prefix) == 0
        return {'run_id': id, 'forged_route': 'technical', 'error': run['error'], 'sends': 0}

    def unchecked_value():
        # Route on an HTTP read result: refused at validation and at run submission.
        prefix = '/deliver/unchecked'
        read = {'id': 'lookup', 'type': 'tool_http', 'inputs': {'input': 'input.message'},
                'config': {'connection_id': lab.http, 'method': 'GET', 'path': '/read'}}
        w = support_routing(lab, prefix, value='lookup.text', middle=(read,))
        body = lab.client.post('/api/validate', json=w).json()
        refusal = 'route: Route on checked values, or use a Condition, which supports review.'
        assert not body['valid'] and refusal in body['errors'], body
        r = lab.client.post('/api/runs', json={'workflow': w, 'message': message('billing')})
        assert r.status_code == 422 and refusal in r.text, r.text
        # A Switch in a contract-1 workflow is refused too.
        old = {'version': 1, 'name': 'Old', 'nodes': [{'id': 'input', 'type': 'chat_input'},
               {'id': 'route', 'type': 'switch', 'inputs': {'value': 'input.message'}, 'config': {'cases': [{'id': 'a', 'label': 'A', 'when': {'operator': 'contains', 'contains': 'x'}}]}},
               {'id': 'x', 'type': 'response', 'inputs': {'text': 'input.message'}}, {'id': 'y', 'type': 'response', 'inputs': {'text': 'input.message'}}],
               'edges': [{'id': '1', 'source': 'input', 'target': 'route'}, {'id': '2', 'source': 'route', 'target': 'x', 'sourceHandle': 'a'},
                         {'id': '3', 'source': 'route', 'target': 'y', 'sourceHandle': 'default'}]}
        old_body = lab.client.post('/api/validate', json=old).json()
        assert 'route: Switch requires a contract-2 workflow.' in old_body['errors'], old_body
        # Submitting it is refused too: no run exists afterwards.
        runs_before = len(lab.client.get('/api/runs').json())
        r = lab.client.post('/api/runs', json={'workflow': old, 'message': 'x'})
        assert r.status_code == 422 and 'Switch requires a contract-2 workflow.' in r.text, r.text
        assert len(lab.client.get('/api/runs').json()) == runs_before
        assert post_count(lab, prefix) == 0 and lab.provider.count('/read') == 0
        return {'validation_errors': body['errors'], 'run_status': 422, 'contract_1': old_body['errors'],
                'contract_1_submission': 422, 'runs_created': 0, 'sends': 0}

    def approved_name():
        # A case named "approved" is just a route and cannot bypass a required approval: its
        # write is configured for approval, so it waits, and a rejection sends nothing anywhere.
        prefix = '/deliver/named'
        cases = [{'id': 'approved', 'label': 'Approved', 'when': {'field': 'category', 'operator': 'eq', 'compare_to': 'billing'}}]
        fixture.script(reading('billing'))
        id = lab.submit(support_routing(lab, prefix, cases=cases, approval=('approved',)), message('billing'))
        run = lab.wait(id)
        assert run['status'] == 'awaiting_approval' and run['checkpoints']['route']['branch'] == 'approved', run.get('error')
        approval = run['approvals'][0]
        assert approval['kind'] == 'write' and post_count(lab, prefix) == 0
        assert lab.decide(id, approval, 'reject').status_code == 200
        run = lab.wait(id, statuses=('success', 'failed', 'cancelled'))
        assert run['status'] == 'cancelled' and post_count(lab, prefix) == 0
        return {'run_id': id, 'approval_kind': 'write', 'after_reject': 'cancelled', 'sends': 0}

    return [
        ('S20', 'A5.2', 'Switch routes a checked support category to exactly one receiver', routed),
        ('S21a', 'A5.2', 'A forged saved route is refused before any send', forged_checkpoint),
        ('S21b', 'A5.2', 'Routing on an unchecked HTTP value or in contract 1 is refused', unchecked_value),
        ('S21c', 'A5.2', 'A case named "approved" cannot bypass a required approval', approved_name),
    ]
