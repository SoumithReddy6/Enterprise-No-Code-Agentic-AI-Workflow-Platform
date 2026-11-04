"""Independent TA04 boundary probes; no application or original evidence edits.

Real compiler/worker, real approval and SQLite storage, synthetic provider and HTTP.
Run: .venv/bin/python -I <file> "$PWD"
Exit 0 means the asserted counterexamples were observed, not that they are fixed.
"""
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

if __name__ == '__main__':
    sys.path.insert(0, sys.argv[1])

import httpx
from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from sqlalchemy.orm import Session
from backend.app import approvals, providers
from backend.app.compiler import compile_workflow
from backend.app.models import Workflow
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_agent_grounding import source
from backend.tests.test_agent_team_budget import team_flow, grounded
from backend.tests.test_agent_tools import calc_flow
from backend.tests.test_iteration import loop_flow
from backend.tests.test_token_budget_recovery import MODEL, SCHEMA, agent_config


async def removed_source():
    """A real remove happens after retrieval/recording but before post-generation verify."""
    available = True
    calls = []
    verification = []
    events = []

    def handle(request):
        nonlocal available
        calls.append(1)
        if len(calls) == 1:
            text, count = json.dumps({'action': 'call', 'target': 'spec0', 'input': 'look it up'}), 1
        elif len(calls) == 2:
            text, count = json.dumps({'action': 'call', 'target': 'kb0', 'input': 'query'}), 1
        elif len(calls) == 3:
            # Source was valid when retrieved, but is removed during the final call.
            available = False
            text, count = 'not JSON', 8
        else:
            raise AssertionError('Unexpected provider call')
        return httpx.Response(200, json={'message': {'content': text},
            'prompt_eval_count': count, 'eval_count': 0})

    async def platform(action, *args):
        if action == 'kb_retrieve':
            assert available
            return {'query': args[1], 'knowledge_base_id': 'kb1', 'version': 1, 'sources': [source(1)]}
        if action in ('record_vector_sources', 'verify_vector_sources'):
            verification.append({'action': action, 'available': available})
            if not available:
                raise ValueError('Knowledge source was removed: doc')
            return args[0] if action == 'verify_vector_sources' else None
        raise AssertionError(action)

    async def emit(event):
        events.append(event)

    workflow = team_flow(1)
    for node in workflow.nodes:
        if node.type == 'agent':
            node.config = {**node.config, **MODEL}
    original_client = httpx.AsyncClient
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
        mp.setattr(providers, 'client', lambda timeout: original_client(
            transport=httpx.MockTransport(handle), trust_env=False))
        result = await compile_workflow(workflow, message='facts', emit=emit,
            platform_resolver=platform).graph.ainvoke({'values': {}})
    values = result['values']
    row = {'provider_calls': len(calls), 'verification': verification,
           'specialist_failure': [e['error'] for e in events if e.get('node_id') == 'spec0' and e.get('status') == 'failed'],
           'agent_sources': json.loads(values['agent']['sources']),
           'response_sources': json.loads(values['out']['sources'])}
    assert len(calls) == 3
    if '--baseline' not in sys.argv[2:]:
        assert 'Knowledge source was removed: doc' in row['specialist_failure']
    assert row['agent_sources'] == []
    assert [s['id'] for s in row['response_sources']] == ['chunk1']
    return row


async def approval_resume(loop_mode=False, expect_defect=True):
    calls = []
    deliveries = []

    def model(request):
        calls.append(1)
        assert len(calls) == 1, 'No model call may follow known token exhaustion'
        return httpx.Response(200, json={
            'message': {'content': json.dumps({'action': 'call', 'target': 'calc', 'input': 'hello'})},
            'prompt_eval_count': 8, 'eval_count': 2})

    def sink(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'issues': [
                {'key': f'P-{i}', 'fields': {'summary': f'Issue {i}',
                    'status': {'name': 'Open'}, 'assignee': None, 'updated': ''}}
                for i in range(2)]})
        deliveries.append(json.loads(request.content))
        return httpx.Response(200, text='sent')

    original_client = httpx.AsyncClient
    with TemporaryDirectory(prefix='relay-ta04-approval-') as directory, MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
        mp.setattr(providers, 'client', lambda timeout: original_client(
            transport=httpx.MockTransport(model), trust_env=False))
        mp.setattr('backend.app.tool_service.public_addresses', lambda *_: ['93.184.216.34'])
        store = Store('sqlite:///' + str(Path(directory) / 'test.db'), Fernet.generate_key())
        try:
            worker = Worker(store)
            store.allow_model('ollama', 'fixture', '', 'local')
            connection = worker.tools.save_connection({'name': 'sink', 'provider': 'http',
                                                       'endpoint': 'https://example.com'})
            worker.tools.transport = httpx.MockTransport(sink)
            workflow = calc_flow()
            workflow.nodes[2].type = 'tool_http'
            workflow.nodes[2].config = {'connection_id': connection['id'], 'method': 'POST',
                'path': '/messages', 'body': '{"text":"{input}"}', 'enable_writes': True, 'approval': True}
            if loop_mode:
                jira = worker.tools.save_connection({'name': 'jira', 'provider': 'jira',
                    'endpoint': 'https://jira.example.com', 'username': 'bot', 'secret': 'fixture'})
                document = loop_flow(connection_id=jira['id']).model_dump(mode='json')
                next(n for n in document['nodes'] if n['id'] == 'work').update(
                    type='agent', config={**MODEL, 'output_schema': SCHEMA})
                document['nodes'].append(workflow.nodes[2].model_dump(mode='json'))
                document['edges'].append({'id': 'tool', 'source': 'calc', 'target': 'work',
                                         'kind': 'tool', 'targetHandle': 'tools'})
                workflow = Workflow.model_validate(document)
            else:
                workflow = agent_config(workflow, 'route', output_schema=SCHEMA)
            created = store.create_run(workflow.model_dump(mode='json'), 'send')
            await worker.execute(store.claim_next(worker.owner))
            paused = store.run(created['id'])
            assert paused['status'] == 'awaiting_approval' and not deliveries
            approval = paused['approvals'][0]
            approvals.decide(store, created['id'], 'local', approval['id'], approval['digest'], 'approve')
            await worker.execute(store.claim_next(worker.owner))
            saved = store.run(created['id'])
            with Session(store.engine) as session:
                settled = session.get(approvals.ApprovalRecord, approval['id'])
                approval_status, approval_result = settled.status, settled.result
            row = {'provider_calls': len(calls), 'deliveries': deliveries,
                   'status': saved['status'], 'truncated': saved['truncated'],
                   'truncation_reason': saved['truncation_reason'],
                   'action_counter': saved['accounting']['action_budget']['counter'],
                   'approval_status': approval_status,
                   'durable_approval_result': approval_result,
                   'recovered_events': [e for e in saved['events'] if e.get('node_id') == 'agent' and e.get('recovered')]}
            assert len(calls) == len(deliveries) == 1
            assert saved['status'] == 'success' and saved['truncated'] is True
            if loop_mode:
                row['loop_summary'] = json.loads(saved['checkpoints']['each']['summary'])
                row['loop_results'] = saved['checkpoints']['each']['results']
                row['not_run_events'] = [e for e in saved['events'] if e.get('status') == 'not_run']
                if expect_defect:
                    assert row['loop_summary']['processed'] == 0
                    assert row['loop_results'] == []
                    assert row['not_run_events']
            else:
                if expect_defect:
                    assert 'agent did not run:' in saved['truncation_reason']
            assert approval_status == 'completed' and approval_result is not None
            assert 'sent' in approval_result
            return row
        finally:
            store.engine.dispose()


async def main():
    rows = {'removed_source_response': await removed_source()}
    if '--source-only' not in sys.argv[2:]:
        rows['approval_resume_attempt'] = await approval_resume()
        rows['loop_approval_resume_attempt'] = await approval_resume(loop_mode=True)
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
