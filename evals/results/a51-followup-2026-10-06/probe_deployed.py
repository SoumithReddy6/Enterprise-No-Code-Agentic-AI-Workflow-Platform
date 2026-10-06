"""Fresh follow-up: isolated API/worker, deterministic Ollama HTTP fixture and receiver."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.check_production_scenarios import Lab, flow_nodes, input_node, response


def main():
    output = Path(__file__).resolve().parent
    rows = []
    reply = {'mode': 'schema'}
    model_calls = []
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            model_calls.append({'mode': reply['mode'], 'path': self.path})
            if reply['mode'] == 'schema':
                text = '{"amount":1000.00000000000001}'
            elif 'tool_error' in request['messages'][-1]['content']:
                text = json.dumps({'action': 'final', 'text': 'Correct the invalid number.'})
            else:
                text = json.dumps({'action': 'call', 'target': 'send',
                                   'input': '{"amount":1e99999999999999999999}'})
            body = json.dumps({'message': {'role': 'assistant', 'content': text},
                               'prompt_eval_count': 1, 'eval_count': 1}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    model = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='relay-a51-followup-') as directory:
            lab = Lab(directory, output)
            lab.env['OLLAMA_BASE_URL'] = f'http://127.0.0.1:{model.server_address[1]}'
            try:
                lab.boot()
                # New policy: a precise value can route true while its unsafe numeric body is refused.
                for literal, operator, target, expected_status, expected_branch in [
                    ('1000.00000000000001', 'gt', '1000', 'failed', 'true'),
                    ('0.100000000000000005', 'eq', '0.1', 'success', 'false'),
                ]:
                    path = '/deliver/precision-' + operator
                    graph = flow_nodes([input_node(), {'id': 'check', 'type': 'condition',
                        'inputs': {'value': 'input.message'}, 'config': {'operator': operator,
                        'compare_to': target, 'compare_as': 'number', 'field': 'amount'}}])
                    graph['nodes'] += [{**lab.tool(path, True, False), 'id': 'send'},
                                       response('send.text', 'sent'), response('input.message', 'kept')]
                    graph['edges'] += [{'id': 't', 'source': 'check', 'target': 'send', 'sourceHandle': 'true'},
                                       {'id': 'f', 'source': 'check', 'target': 'kept', 'sourceHandle': 'false'},
                                       {'id': 's', 'source': 'send', 'target': 'sent'}]
                    run = lab.wait(lab.submit(graph, '{"amount":' + literal + '}'))
                    rows.append({'case': 'precision_' + operator, 'status': run['status'],
                        'branch': run.get('checkpoints', {}).get('check', {}).get('branch'),
                        'error': run.get('error'), 'writes': lab.provider.count(path),
                        'write_nodes': run.get('write_nodes'),
                        'passed': run['status'] == expected_status and
                                  run.get('checkpoints', {}).get('check', {}).get('branch') == expected_branch and
                                  lab.provider.count(path) == 0 and not run.get('write_nodes')})

                path = '/deliver/exact-approved'
                graph = flow_nodes([input_node(), lab.tool(path, True, True), response('work.text')])
                identifier = lab.submit(graph, '{"amount":9007199254740993.0}')
                paused = lab.wait(identifier)
                approval = paused['approvals'][0]
                wrong = lab.decide(identifier, approval, digest='0' * 64).status_code
                before = lab.provider.count(path)
                accepted = lab.decide(identifier, approval).status_code
                repeated = lab.decide(identifier, approval).status_code
                run = lab.wait(identifier)
                received = [call['body'] for call in lab.provider.calls if call['path'] == path]
                rows.append({'case': 'exact_number_approval', 'payload_json': approval['payload_json'],
                    'mismatched_digest_status': wrong, 'writes_before_approval': before,
                    'approve_status': accepted, 'second_approve_status': repeated,
                    'final_status': run['status'], 'received': received,
                    'passed': wrong == 409 and before == 0 and accepted == repeated == 200 and
                              run['status'] == 'success' and len(received) == 1 and
                              json.loads(received[0])['amount'] == 9007199254740993 and
                              '9007199254740993' in approval['payload_json']})

                registered = lab.client.post('/api/models', json={'provider': 'ollama', 'model': 'llama3.1:latest'})
                assert registered.status_code == 201, registered.text
                agent = {'id': 'extract', 'type': 'agent', 'inputs': {'input': 'input.message'},
                    'config': {'provider': 'ollama', 'model': 'llama3.1:latest', 'role': 'extraction',
                               'output_schema': {'type': 'object', 'required': ['amount'],
                                   'properties': {'amount': {'type': 'number', 'maximum': 1000}}}}}
                path = '/deliver/schema-violation'
                send = {**lab.tool(path, True, False), 'id': 'send', 'inputs': {'input': 'extract.text'}}
                send['config']['body'] = '{"audit":"schema-violation"}'
                graph = flow_nodes([input_node(), agent, {'id': 'check', 'type': 'condition',
                    'inputs': {'value': 'extract.text'},
                    'config': {'operator': 'gt', 'compare_to': '1000', 'field': 'amount'}}])
                graph['nodes'] += [send, response('send.text', 'sent'), response('extract.text', 'kept')]
                graph['edges'] += [{'id': 't', 'source': 'check', 'target': 'send', 'sourceHandle': 'true'},
                                   {'id': 'f', 'source': 'check', 'target': 'kept', 'sourceHandle': 'false'},
                                   {'id': 's', 'source': 'send', 'target': 'sent'}]
                run = lab.wait(lab.submit(graph, 'Extract a permitted amount.'))
                rows.append({'case': 'schema_violation_reaches_external_write', 'status': run['status'],
                    'extracted': run.get('checkpoints', {}).get('extract', {}).get('text'),
                    'branch': run.get('checkpoints', {}).get('check', {}).get('branch'),
                    'writes': lab.provider.count(path),
                    'passed': run['status'] == 'failed' and lab.provider.count(path) == 0})

                reply['mode'] = 'exponent'
                path = '/deliver/exponent'
                graph = flow_nodes([input_node(), {'id': 'agent', 'type': 'agent',
                    'inputs': {'input': 'input.message'},
                    'config': {'provider': 'ollama', 'model': 'llama3.1:latest'}}, response('agent.text')])
                graph['nodes'].append({**lab.tool(path, True, False), 'id': 'send', 'inputs': {}})
                graph['edges'].append({'id': 'attach', 'source': 'send', 'target': 'agent',
                                       'kind': 'tool', 'targetHandle': 'tools'})
                run = lab.wait(lab.submit(graph, 'Send the amount.'))
                rows.append({'case': 'range_refusal_should_be_correctable', 'status': run['status'],
                    'error': run.get('error'), 'write_nodes': run.get('write_nodes'),
                    'writes': lab.provider.count(path),
                    'passed': run['status'] == 'success' and lab.provider.count(path) == 0 and
                              not run.get('write_nodes')})
                requests = list(lab.provider.calls)
            finally:
                lab.close()
    finally:
        model.shutdown()
        model.server_close()
        thread.join(timeout=5)
    document = {'scope': 'Real HTTP, API and worker processes; deterministic local Ollama and tool fixtures.',
                'rows': rows, 'model_calls': model_calls, 'receiver_calls': requests,
                'passed': all(row['passed'] for row in rows)}
    (output / 'deployed-results.json').write_text(json.dumps(document, indent=2) + '\n')
    print(json.dumps(document, indent=2))
    return 0 if document['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
