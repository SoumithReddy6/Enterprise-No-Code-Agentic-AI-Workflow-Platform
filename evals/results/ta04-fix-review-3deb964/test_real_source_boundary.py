"""A temporary real KB/worker counterexample, outside normal test discovery.

This regression fails while a removed source remains in the final Response.
No user database or external model is used. Only the provider response is mocked.
"""
import json

from cryptography.fernet import Fernet
import pytest

from backend.app import providers
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_agent_team_budget import team_flow
from backend.tests.test_kb_services_integration import populated, services  # noqa: F401


@pytest.mark.asyncio
async def test_removed_document_survives_into_the_final_response(tmp_path, monkeypatch, services):
    kb, doc, _ = await populated(services)
    calls = []

    async def model(*args, usage=None, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            text, tokens = json.dumps({'action': 'call', 'target': 'spec0', 'input': 'project codename'}), 1
        elif len(calls) == 2:
            text, tokens = json.dumps({'action': 'call', 'target': 'kb0', 'input': 'project codename'}), 1
        elif len(calls) == 3:
            await services.management.call('remove_document', 'local',
                {'kb_id': kb['id'], 'document_id': doc['id']})
            text, tokens = 'not JSON', 8
        else:
            raise AssertionError('Unexpected model call')
        providers.record_usage(usage, tokens, 0)
        return text

    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '10')
    monkeypatch.setattr(providers, 'ollama_chat', model)
    store = Store(f'sqlite:///{tmp_path}/relay.db', Fernet.generate_key())
    try:
        worker = Worker(store)
        worker.kbs = services
        store.allow_model('ollama', 'fixture', '', 'local')
        workflow = team_flow(1)
        for node in workflow.nodes:
            if node.type == 'agent':
                node.config = {**node.config, 'provider': 'ollama', 'model': 'fixture'}
            elif node.type == 'retrieve':
                node.config['knowledge_base_id'] = kb['id']
        created = store.create_run(workflow.model_dump(mode='json'), 'project codename')
        await worker.execute(store.claim_next(worker.owner))
        saved = store.run(created['id'])
        assert len(calls) == 3
        assert json.loads(saved['checkpoints']['agent']['sources']) == []
        # Either fail closed before publishing Response or omit the removed source.
        returned = json.loads(saved['checkpoints'].get('out', {}).get('sources', '[]'))
        assert not any(s['document_id'] == doc['id'] for s in returned), \
            'R01: the final Response returns the document removed during generation'
        if returned:
            await services.verify(returned, 'local')
    finally:
        store.engine.dispose()
