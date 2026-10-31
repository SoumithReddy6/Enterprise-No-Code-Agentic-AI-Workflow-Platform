"""Exercise revision 3 in an exported, disposable application tree.

Real Worker, SQLite storage, KB management/search/verification; fake LLM and Jira.
Exit zero confirms the expected refusals/controls on the candidate. This is plan
verification, not a product implementation or the full acceptance suite.
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
from backend.app.compiler import validate_workflow
from backend.app.kb.management import Management
from backend.app.kb.search import Search
from backend.app.kb_gateway import KnowledgeServices
from backend.app.models import Workflow
from backend.app.platform_validation import kb_errors
from backend.app.registry import response_node
from backend.app.request_safety import UnsafeExecutionError
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_agent_grounding import source
from backend.tests.test_agent_team_budget import grounded
from backend.tests.test_iteration import loop_flow
from backend.tests.test_kb_services_integration import Local, populated
from backend.tests.test_kb_workflow import graph

MODEL = {'provider': 'ollama', 'model': 'fixture'}


async def real_case(kind):
    cached = kind.startswith('cached_')
    loop = cached or kind == 'loop_fresh'
    two_items = kind in ('cached_partial_after_reuse', 'cached_partial_unchanged')
    partial = kind == 'cached_partial_loop' or two_items
    with TemporaryDirectory(prefix='relay-r01-r2-') as directory, MonkeyPatch.context() as mp:
        root = Path(directory)
        key = Fernet.generate_key()
        management = Management(f'sqlite:///{root}/management.db', root/'files', key)
        search = Search(f'sqlite:///{root}/search.db', root/'indexes', key)
        services = KnowledgeServices(Local(management), Local(search))
        store = Store(f'sqlite:///{root}/relay.db', key)
        try:
            kb, doc, ingester = await populated(services)
            await services.management.call('upload', 'local', {'kb_id': kb['id'],
                'filename': 'other.txt', 'content_b64': base64.b64encode(b'Unrelated handbook material.').decode(),
                'idempotency_key': 'other'})
            await ingester.execute(await services.management.call('claim', 'system', {}))
            worker = Worker(store)
            worker.kbs = services
            store.allow_model('ollama', 'fixture', '', 'local')
            calls, verifies, timeline = [], [], []
            phase={'node':None}
            response_documents=[]
            original_verify = services.verify

            async def checked(sources, tenant):
                verifies.append([s['document_id'] for s in sources])
                if phase['node']=='out':response_documents.extend(verifies[-1])
                timeline.append({'event': 'verify', 'documents': verifies[-1]})
                return await original_verify(sources, tenant)

            mp.setattr(services, 'verify', checked)

            async def model(*args, usage=None, **kwargs):
                prompt=json.loads(args[2]) if loop else None
                passages=prompt['evidence']['passages'] if prompt else None
                calls.append([p['document_id'] for p in passages] if passages else [])
                providers.record_usage(usage, 8, 2)
                if passages:
                    assert len(passages)==1,passages
                    label=passages[0]['citation']
                    return grounded(passages[0]['text']+f' [{label}]',[label])
                return grounded('Bluebird is the project codename. [S1]', ['S1'])

            mp.setenv('RELAY_RUN_TOKEN_LIMIT', '1000')
            mp.setattr(providers, 'ollama_chat', model)
            mp.setattr('backend.app.tool_service.public_addresses', lambda *_: ['93.184.216.34'])
            if loop:
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
                issues=[{'key': 'P-1', 'fields': {'summary': 'project codename',
                    'status': {'name': 'Open'}, 'assignee': None, 'updated': ''}}]
                if two_items:
                    issues.append({'key':'P-2','fields':{'summary':'Unrelated handbook material',
                        'status':{'name':'Open'},'assignee':None,'updated':''}})
                worker.tools.transport = httpx.MockTransport(lambda request: httpx.Response(200,json={'issues':issues}))
            else:
                workflow = graph()
                next(n for n in workflow.nodes if n.id == 'retrieve').config['knowledge_base_id'] = kb['id']
                next(n for n in workflow.nodes if n.id == 'agent').config = dict(MODEL)
                next(n for n in workflow.nodes if n.id == 'out').on_error = {
                    'fresh_continue': 'continue', 'fresh_route': 'route'}.get(kind, 'fail')
            assert validate_workflow(workflow) == []
            original_event = store.worker_event

            def remove_document():
                services.management.call_sync('remove_document', 'local',
                    {'kb_id': kb['id'], 'document_id': doc['id']})
                timeline.append({'event': 'document_removed'})

            def initial_event(run_id, owner, payload):
                if payload.get('status')=='running':phase['node']=payload.get('node_id')
                saved = original_event(run_id, owner, payload)
                if partial and payload.get('kind') == 'loop_item':
                    raise asyncio.CancelledError('after requested item progress commit')
                if payload.get('status') == 'success' and not payload.get('transient'):
                    if cached and payload.get('node_id') == ('out' if kind == 'cached_response' else 'each'):
                        raise asyncio.CancelledError('after requested checkpoint commit')
                    if kind.startswith('fresh_') and payload.get('node_id') == 'agent':
                        remove_document()
                return saved

            mp.setattr(store, 'worker_event', initial_event)
            row = store.create_run(workflow.model_dump(mode='json'), 'project codename')
            await worker.execute(store.claim_next(worker.owner))
            before = store.run(row['id'])
            verification_start = len(verifies)
            if cached:
                assert before['status'] != 'success'
                assert 'each:0' in before['vector_dependencies']
                if partial:
                    assert 'each' not in before['checkpoints'] and '0' in before['loop_progress']['each']
                else:
                    assert 'each' in before['checkpoints']
                assert ('out' in before['checkpoints']) == (kind == 'cached_response')
                if kind in ('cached_response', 'cached_loop', 'cached_partial_loop'):
                    remove_document()
                else:
                    assert kind in ('cached_loop_unchanged', 'cached_loop_after_reuse',
                        'cached_partial_after_reuse','cached_partial_unchanged')

                def resume_event(run_id, owner, payload):
                    if payload.get('status')=='running':phase['node']=payload.get('node_id')
                    saved = original_event(run_id, owner, payload)
                    if (kind == 'cached_loop_after_reuse' and payload.get('node_id') == 'each'
                            and payload.get('status') == 'success' and payload.get('cached')):
                        timeline.append({'event': 'loop_reuse_committed'})
                    if (kind in ('cached_loop_after_reuse','cached_partial_after_reuse') and payload.get('node_id') == 'render'
                            and payload.get('status') == 'success'):
                        timeline.append({'event': 'downstream_prompt_committed'})
                        remove_document()
                    return saved

                mp.setattr(store, 'worker_event', resume_event)
                store.resume_run(row['id'])
                await worker.execute(store.claim_next(worker.owner))
            final = store.run(row['id'])
            out = final['checkpoints'].get('out', {})
            removed = next((i for i, event in enumerate(timeline) if event['event'] == 'document_removed'), None)
            result = {'case': kind, 'status': final['status'], 'error': final.get('error'),
                'provider_calls': len(calls), 'output': final.get('output'),
                'response_source_count': len(json.loads(out.get('sources', '[]'))),
                'verification_calls_after_resume': len(verifies)-verification_start if cached else None,
                'verification_calls_after_removal': sum(e['event'] == 'verify' for e in timeline[removed+1:]) if removed is not None else None,
                'response_checkpointed': 'out' in final['checkpoints'],
                'model_call_documents': calls,
                'response_verification_documents':response_documents,
                'timeline': timeline}
            if kind in ('cached_loop_unchanged', 'loop_fresh','cached_partial_unchanged'):
                assert final['status'] == 'success' and 'Bluebird' in final['output']
                if kind in ('loop_fresh','cached_partial_unchanged'):
                    assert result['response_source_count'] == 1
                else:
                    assert result['response_source_count'] == 0
                assert doc['id'] in response_documents
            elif kind in ('cached_loop_after_reuse','cached_partial_after_reuse'):
                assert final['status']=='failed' and 'No answer was published' in final['error'],result
                assert not result['response_checkpointed'] and not final['output']
                assert removed is not None and result['verification_calls_after_removal'] > 0
                assert result['verification_calls_after_resume'] > 0
            elif cached:
                assert final['status'] == 'failed' and ('out:' in final['error'] or 'each:0:' in final['error'])
            else:
                assert final['status'] == 'failed' and 'No answer was published' in final['error']
                assert not result['response_checkpointed']
            assert len(calls) == (2 if two_items else 1),calls
            if two_items:
                assert calls[0]==[doc['id']] and doc['id'] not in calls[1],calls
            return result
        finally:
            store.engine.dispose()
            management.engine.dispose()
            search.engine.dispose()


async def shape_cases():
    evidence = [{**source(1), 'citation': 'S1', 'cited': False}]
    try:
        await response_node({'text': 'Fact 1 [S1]'}, None,
            SimpleNamespace(run={'evidence': evidence}, platform=None, node_id='out'))
    except UnsafeExecutionError as exc:
        unavailable_error = str(exc)
    else:
        raise AssertionError('Missing authority must fail closed')
    verify_calls = []

    async def verify(sources, tenant):
        verify_calls.append(sources)

    workflow = Workflow.model_validate({'version': 1, 'name': 'Cached', 'nodes': [
        {'id': 'input', 'type': 'chat_input'}, {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'out'}]})
    errors = await kb_errors(SimpleNamespace(verify=verify), workflow, 'local',
        {'out': {'text': 'cached answer', 'sources': '{}'}})
    assert len(errors) == 1 and verify_calls == []
    return {'missing_authority_refused': unavailable_error, 'malformed_empty_object_refused': errors}


async def main():
    if '--case' in sys.argv:
        kind=sys.argv[sys.argv.index('--case')+1]
        print(json.dumps(await real_case(kind),indent=2))
        return
    if '--control-only' in sys.argv:
        print(json.dumps({'application_root': str(Path(sys.modules['backend.app.registry'].__file__).parent),
            'real_cases': [await real_case(kind) for kind in ('loop_fresh', 'cached_loop_unchanged')]}, indent=2))
        return
    results = {'application_root': str(Path(sys.modules['backend.app.registry'].__file__).parent),
        'shapes': await shape_cases(),
        'real_cases': [await real_case(kind) for kind in (
            'fresh_fail', 'fresh_continue', 'fresh_route', 'cached_response', 'cached_loop', 'cached_partial_loop',
            'loop_fresh', 'cached_loop_unchanged', 'cached_loop_after_reuse',
            'cached_partial_after_reuse','cached_partial_unchanged')]}
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
