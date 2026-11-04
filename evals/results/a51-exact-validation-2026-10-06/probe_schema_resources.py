"""Independent exact-validation composition probes with temporary real API/worker processes."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.app import exact_json
from backend.app.agent_runtime import validate_structured
from backend.app.registry import AgentConfig
from scripts.check_production_scenarios import Lab, flow_nodes, input_node, response

DIALECT = 'https://json-schema.org/draft/2020-12/schema'


def schema_for(rule, declared):
    resource = {'$id': 'urn:relay:audit:money', **rule}
    if declared:
        resource['$schema'] = DIALECT
    return {'$schema': DIALECT, 'type': 'object', 'required': ['amount'],
            'properties': {'amount': {'$ref': 'urn:relay:audit:money'}},
            '$defs': {'Money': resource}}


def main():
    output = Path(__file__).resolve().parent
    rows = []
    cases = [('integer', '1000.0', {'type': 'integer'}, True),
             ('multiple', '1e30', {'type': 'number', 'multipleOf': .01}, True),
             ('conditional_bound', '1000.0', {'if': {'type': 'integer'}, 'then': {'maximum': 999}}, False)]
    for name, token, rule, expected_valid in cases:
        for declared in (False, True):
            schema = schema_for(rule, declared)
            AgentConfig(output_schema=schema)
            text = '{"amount":' + token + '}'
            try:
                returned = validate_structured(text, schema)
                error = None
            except Exception as exc:
                returned = None
                error = {'type': type(exc).__name__, 'message': str(exc)}
            rows.append({'case': name, 'layer': 'direct', 'embedded_dialect_declared': declared,
                         'schema': schema, 'input': text, 'returned': returned,
                         'error': error, 'expected_valid': expected_valid,
                         'passed': (returned is not None) == expected_valid})

    current = {'text': ''}
    model_calls = []
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            model_calls.append(self.path)
            body = json.dumps({'message': {'content': current['text']},
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
        with tempfile.TemporaryDirectory(prefix='relay-a51-resources-') as directory:
            lab = Lab(directory, output)
            lab.env['OLLAMA_BASE_URL'] = f'http://127.0.0.1:{model.server_address[1]}'
            try:
                lab.boot()
                registered = lab.client.post('/api/models', json={'provider': 'ollama', 'model': 'llama3.1:latest'})
                assert registered.status_code == 201, registered.text
                for name, token, rule, expected_valid in cases:
                    for declared in (False, True):
                        schema = schema_for(rule, declared)
                        current['text'] = '{"amount":' + token + '}'
                        extraction = {'id': 'extract', 'type': 'agent',
                            'inputs': {'input': 'input.message'}, 'config': {'provider': 'ollama',
                            'model': 'llama3.1:latest', 'role': 'extraction', 'output_schema': schema}}
                        path = f'/deliver/resource-{declared}'
                        if name == 'conditional_bound':
                            graph = flow_nodes([input_node(), extraction, {'id': 'check', 'type': 'condition',
                                'inputs': {'value': 'extract.text'},
                                'config': {'operator': 'gt', 'compare_to': '999', 'field': 'amount'}}])
                            send = {**lab.tool(path, True, False), 'id': 'send', 'inputs': {'input': 'extract.text'}}
                            graph['nodes'] += [send, response('send.text', 'sent'), response('extract.text', 'kept')]
                            graph['edges'] += [{'id': 't', 'source': 'check', 'target': 'send', 'sourceHandle': 'true'},
                                               {'id': 'f', 'source': 'check', 'target': 'kept', 'sourceHandle': 'false'},
                                               {'id': 's', 'source': 'send', 'target': 'sent'}]
                        else:
                            graph = flow_nodes([input_node(), extraction, response('extract.text')])
                        validation = lab.client.post('/api/validate', json=graph)
                        assert validation.status_code == 200 and validation.json()['valid'], validation.text
                        before = len(model_calls)
                        run = lab.wait(lab.submit(graph, 'Extract a valid amount.'))
                        calls = len(model_calls) - before
                        rows.append({'case': name, 'layer': 'API_and_worker_processes',
                            'embedded_dialect_declared': declared, 'validation': validation.json()['valid'],
                            'input': current['text'], 'run_id': run['id'], 'status': run['status'],
                            'output': run.get('output'), 'error': run.get('error'), 'model_calls': calls,
                            'checkpoint': run.get('checkpoints', {}).get('extract'),
                            'expected_valid': expected_valid,
                            'branch': run.get('checkpoints', {}).get('check', {}).get('branch'),
                            'writes': lab.provider.count(path),
                            'passed': (run['status'] == 'success' and calls == 1) if expected_valid else
                                      (run['status'] == 'failed' and calls == 2 and lab.provider.count(path) == 0)})
                receiver_calls = list(lab.provider.calls)
            finally:
                lab.close()
    finally:
        model.shutdown()
        model.server_close()
        thread.join(timeout=5)

    range_rows = []
    for token in ('1e1000', '9e1000', '2' + '0' * 1000, '1e1001'):
        try:
            exact_json.loads(token)
            accepted = True
        except exact_json.UnsupportedNumber:
            accepted = False
        range_rows.append({'token': token if len(token) < 50 else 'integer 2 * 10**1000',
                           'accepted': accepted})
    document = {'scope': 'Real isolated API and worker, deterministic local Ollama fixture; no external writes.',
                'rows': rows, 'range_observations': range_rows, 'receiver_calls': receiver_calls,
                'passed': all(row['passed'] for row in rows)}
    (output / 'schema-resources-results.json').write_text(json.dumps(document, indent=2) + '\n')
    print(json.dumps(document, indent=2))
    return 0 if document['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
