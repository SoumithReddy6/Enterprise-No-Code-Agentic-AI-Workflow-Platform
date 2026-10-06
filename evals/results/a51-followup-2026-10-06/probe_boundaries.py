"""Independent follow-up probes. Only temporary databases and mocked providers are used."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.agent_runtime import validate_structured
from backend.app.main import create_app
from backend.app.worker import Worker


async def main():
    rows = []
    cases = [
        ('maximum', '1000.00000000000001', {'type': 'number', 'maximum': 1000}),
        ('integer', '1000.00000000000001', {'type': 'integer'}),
        ('minimum', '0.99999999999999999999', {'type': 'number', 'minimum': 1}),
        ('multipleOf', '1000.00000000000001', {'type': 'number', 'multipleOf': .01}),
        ('const', '1000.00000000000001', {'const': 1000}),
        ('enum', '1000.00000000000001', {'enum': [1000]}),
    ]
    for name, literal, constraint in cases:
        text = '{"amount":' + literal + '}'
        schema = {'type': 'object', 'properties': {'amount': constraint}, 'required': ['amount']}
        try:
            output = validate_structured(text, schema)
            accepted = True
        except ValueError as exc:
            output = str(exc)
            accepted = False
        rows.append({'case': 'numeric_schema_' + name, 'input': text, 'schema': schema,
                     'returned': output, 'accepted': accepted, 'passed': not accepted})

    with tempfile.TemporaryDirectory(prefix='relay-a51-boundaries-') as temp:
        with patch.dict(os.environ, {'VECTOR_DATA_DIR': str(Path(temp) / 'vectors')}):
            with TestClient(create_app(f'sqlite:///{temp}/probe.db', Fernet.generate_key(),
                                      auth_enabled=False, embedded_worker=False)) as client:
                store = client.app.state.store
                worker = Worker(store)
                schema = {'type': 'object', 'properties': {'amount': {'type': 'number', 'maximum': 1000}},
                          'required': ['amount']}
                workflow = {'version': 1, 'name': 'Schema boundary audit', 'nodes': [
                    {'id': 'input', 'type': 'chat_input'},
                    {'id': 'extract', 'type': 'agent', 'inputs': {'input': 'input.message'},
                     'config': {'provider': 'demo', 'role': 'extraction', 'output_schema': schema}},
                    {'id': 'out', 'type': 'response', 'inputs': {'text': 'extract.text'}},
                ], 'edges': [{'id': 'a', 'source': 'input', 'target': 'extract'},
                             {'id': 'b', 'source': 'extract', 'target': 'out'}]}
                async def extraction(*args):
                    return {'text': '{"amount":1000.00000000000001}', 'provider': 'demo'}
                with patch('backend.app.registry.llm_node', extraction):
                    created = client.post('/api/runs', json={'workflow': workflow, 'message': 'extract amount'})
                    assert created.status_code == 201, created.text
                    await worker.execute(store.claim_next(worker.owner))
                run = store.run(created.json()['id'])
                rows.append({'case': 'durable_extraction_schema', 'status': run['status'],
                             'checkpoint': run['checkpoints'].get('extract'),
                             'error': run.get('error'), 'passed': run['status'] == 'failed'})

                connection = client.post('/api/connections', json={'name': 'Synthetic HTTP', 'provider': 'http',
                                         'endpoint': 'https://example.com'}).json()
                literal = '{"amount":1e99999999999999999999}'
                workflow['name'] = 'Pre-send numeric range audit'
                workflow['nodes'][1] = {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'},
                                        'config': {'provider': 'demo'}}
                workflow['nodes'][2]['inputs']['text'] = 'agent.text'
                workflow['nodes'].append({'id': 'send', 'type': 'tool_http', 'config': {
                    'connection_id': connection['id'], 'method': 'POST', 'enable_writes': True,
                    'approval': False, 'body': '{input}'}})
                workflow['edges'] = [{'id': 'a', 'source': 'input', 'target': 'agent'},
                                     {'id': 'b', 'source': 'agent', 'target': 'out'},
                                     {'id': 'tool', 'source': 'send', 'target': 'agent',
                                      'kind': 'tool', 'targetHandle': 'tools'}]
                replies = iter([json.dumps({'action': 'call', 'target': 'send', 'input': literal}),
                                json.dumps({'action': 'final', 'text': 'Correct the invalid number.'})])
                async def planning(*args):
                    return {'text': next(replies), 'provider': 'demo'}
                sent = []
                async def network(*args):
                    sent.append(True)
                    return 'accepted'
                with patch('backend.app.registry.llm_node', planning), patch.object(worker.tools, 'execute_prepared', network):
                    created = client.post('/api/runs', json={'workflow': workflow, 'message': 'send amount'})
                    assert created.status_code == 201, created.text
                    await worker.execute(store.claim_next(worker.owner))
                run = store.run(created.json()['id'])
                rows.append({'case': 'out_of_range_exponent_pre_send', 'input': literal,
                             'status': run['status'], 'error': run.get('error'),
                             'write_nodes': run.get('write_nodes'), 'network_calls': len(sent),
                             'passed': run['status'] == 'success' and not sent and not run.get('write_nodes')})
    document = {'scope': 'Direct validators plus real durable worker; model and network mocked.',
                'rows': rows, 'passed': all(row['passed'] for row in rows)}
    Path(__file__).with_name('boundaries-results.json').write_text(json.dumps(document, indent=2) + '\n')
    print(json.dumps(document, indent=2))
    return 0 if document['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
