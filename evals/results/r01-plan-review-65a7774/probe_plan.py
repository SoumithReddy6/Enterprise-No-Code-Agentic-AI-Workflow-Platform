"""Counterexamples/controls against a disposable implementation of the R01 plan.

Real worker, KB management/search, source verification and storage; synthetic model
and Jira only. The caller supplies the disposable source tree as argv[1].
Exit 0 means the recorded plan-level gaps were observed, not that R01 is fixed.
"""
import asyncio
import base64
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1])

import httpx
from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from backend.app import providers
from backend.app.agent_runtime import ground_answer
from backend.app.compiler import validate_workflow
from backend.app.kb.management import Management
from backend.app.kb.search import Search
from backend.app.kb_gateway import KnowledgeServices
from backend.app.models import Workflow
from backend.app.platform_nodes import canonical, published_batches
from backend.app.platform_validation import kb_errors
from backend.app.registry import response_node
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_agent_grounding import source
from backend.tests.test_agent_team_budget import grounded
from backend.tests.test_iteration import loop_flow
from backend.tests.test_kb_services_integration import Local, populated
from backend.tests.test_kb_workflow import graph

MODEL = {'provider': 'ollama', 'model': 'fixture'}


async def real_case(kind):
    with TemporaryDirectory(prefix='relay-r01-probe-') as directory, MonkeyPatch.context() as mp:
        root = Path(directory)
        key = Fernet.generate_key()
        management = Management(f'sqlite:///{root}/management.db', root/'files', key)
        search = Search(f'sqlite:///{root}/search.db', root/'indexes', key)
        services = KnowledgeServices(Local(management), Local(search))
        store = Store(f'sqlite:///{root}/relay.db', key)
        try:
            kb, doc, ingester = await populated(services)
            # Keep the KB available after removing the answer's document, so a resolve
            # failure cannot accidentally cover the missing dependency check.
            await services.management.call('upload', 'local', {'kb_id': kb['id'],
                'filename': 'other.txt', 'content_b64': base64.b64encode(b'Unrelated handbook material.').decode(),
                'idempotency_key': 'other'})
            await ingester.execute(await services.management.call('claim', 'system', {}))
            worker = Worker(store)
            worker.kbs = services
            store.allow_model('ollama', 'fixture', '', 'local')
            calls, verifies = [], []
            original_verify = services.verify

            async def checked(sources, tenant):
                verifies.append([s['document_id'] for s in sources])
                return await original_verify(sources, tenant)

            mp.setattr(services, 'verify', checked)

            async def model(*args, usage=None, **kwargs):
                calls.append(1)
                providers.record_usage(usage, 8, 2)
                return grounded('Bluebird is the project codename. [S1]', ['S1'])

            mp.setenv('RELAY_RUN_TOKEN_LIMIT', '1000')
            mp.setattr(providers, 'ollama_chat', model)
            mp.setattr('backend.app.tool_service.public_addresses', lambda *_: ['93.184.216.34'])
            if kind.startswith('cached_'):
                jira = worker.tools.save_connection({'name': 'jira', 'provider': 'jira',
                    'endpoint': 'https://jira.example.com', 'username': 'bot', 'secret': 'fixture'})
                document = loop_flow(connection_id=jira['id']).model_dump(mode='json')
                next(n for n in document['nodes'] if n['id'] == 'work').update(
                    type='query', config={'knowledge_base_id': kb['id'], 'mode': 'keyword', **MODEL})
                document['nodes'].insert(-1, {'id': 'render', 'type': 'prompt',
                    'config': {'template': '{message}'}, 'inputs': {'message': 'each.results'}})
                next(n for n in document['nodes'] if n['id'] == 'out')['inputs'] = {'text': 'render.text'}
                next(e for e in document['edges'] if e['source'] == 'each' and e['target'] == 'out')['target'] = 'render'
                document['edges'].append({'id': 'render_out', 'source': 'render', 'target': 'out'})
                workflow = Workflow.model_validate(document)
                worker.tools.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
                    'issues': [{'key': 'P-1', 'fields': {'summary': 'project codename',
                        'status': {'name': 'Open'}, 'assignee': None, 'updated': ''}}]}))
            else:
                workflow = graph()
                next(n for n in workflow.nodes if n.id == 'retrieve').config['knowledge_base_id'] = kb['id']
                next(n for n in workflow.nodes if n.id == 'agent').config = dict(MODEL)
                next(n for n in workflow.nodes if n.id == 'out').on_error = 'continue' if kind == 'fresh_continue' else 'fail'

            assert validate_workflow(workflow) == []
            original_event = store.worker_event

            def event(run_id, owner, payload):
                saved = original_event(run_id, owner, payload)
                if payload.get('status') == 'success' and not payload.get('transient'):
                    if kind.startswith('cached_') and payload.get('node_id') == ('each' if kind == 'cached_loop' else 'out'):
                        raise asyncio.CancelledError('after requested checkpoint commit')
                    if kind.startswith('fresh_') and payload.get('node_id') == 'agent':
                        services.management.call_sync('remove_document', 'local',
                            {'kb_id': kb['id'], 'document_id': doc['id']})
                return saved

            mp.setattr(store, 'worker_event', event)
            row = store.create_run(workflow.model_dump(mode='json'), 'project codename')
            await worker.execute(store.claim_next(worker.owner))
            before = store.run(row['id'])
            verification_start = len(verifies)
            if kind.startswith('cached_'):
                assert before['status'] != 'success'
                assert 'each' in before['checkpoints']
                assert ('out' in before['checkpoints']) == (kind == 'cached_response')
                assert 'each:0' in before['vector_dependencies']
                await services.management.call('remove_document', 'local',
                    {'kb_id': kb['id'], 'document_id': doc['id']})
                assert await services.management.call('resolve', 'local', {'kb_id': kb['id']})
                mp.setattr(store, 'worker_event', original_event)
                store.resume_run(row['id'])
                await worker.execute(store.claim_next(worker.owner))
            final = store.run(row['id'])
            out = final['checkpoints'].get('out', {})
            result = {'case': kind, 'status': final['status'], 'error': final.get('error'),
                'provider_calls': len(calls), 'output_has_removed_answer': 'Bluebird' in (final.get('output') or ''),
                'response_source_count': len(json.loads(out.get('sources', '[]'))),
                'verification_calls_after_resume': len(verifies)-verification_start if kind.startswith('cached_') else None,
                'response_failures': [e.get('error') for e in final['events']
                    if e.get('node_id') == 'out' and e.get('status') == 'failed']}
            if kind == 'cached_loop':
                assert final['status'] == 'success' and result['output_has_removed_answer']
                assert result['response_source_count'] == result['verification_calls_after_resume'] == 0
            elif kind == 'cached_response':
                assert final['status'] == 'failed' and 'out:' in (final.get('error') or '')
            elif kind == 'fresh_continue':
                assert final['status'] == 'failed' and final['error'] == 'Run failed unexpectedly.'
                assert any('No answer was published' in str(x) for x in result['response_failures'])
            else:
                assert final['status'] == 'failed' and 'No answer was published' in final['error']
            assert len(calls) == 1
            return result
        finally:
            store.engine.dispose()
            management.engine.dispose()
            search.engine.dispose()


async def shape_cases():
    evidence = [{**source(1), 'citation': 'S1', 'cited': False}]
    out = await response_node({'text': 'Fact 1 [S1]'}, None,
        SimpleNamespace(run={'evidence': evidence}, platform=None, node_id='out'))
    verify_calls = []

    async def verify(sources, tenant):
        verify_calls.append(sources)

    workflow = Workflow.model_validate({'version': 1, 'name': 'Cached', 'nodes': [
        {'id': 'input', 'type': 'chat_input'}, {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'out'}]})
    errors = await kb_errors(SimpleNamespace(verify=verify), workflow, 'local',
        {'out': {'text': 'cached answer', 'sources': '{}'}})
    assert len(json.loads(out['sources'])) == 1
    assert errors == [] and verify_calls == []
    n7_equivalent = all(canonical(ground_answer(text, evidence)[1]) == canonical(evidence)
                        for text in ('No citation', 'Fact [S1]', 'Bad [S99]'))
    assert n7_equivalent
    return {'nonempty_sources_without_verifier_published': True,
            'malformed_empty_object_checkpoint_accepted': True,
            'N7_same_canonical_source_list': n7_equivalent}


async def main():
    print(json.dumps({'application_root': str(Path(sys.modules['backend.app.registry'].__file__).parent),
        'shapes': await shape_cases(),
        'real_cases': [await real_case(kind) for kind in (
            'fresh_fail', 'fresh_continue', 'cached_response', 'cached_loop')]}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
