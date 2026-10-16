"""Prepared replies come only from saved configuration and never swallow requests."""
import json
import logging
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.common_replies import (
    ReplyScope, common_reply_scope, lookup_common_reply, normalise_common_message,
)
from backend.app.request_contracts import GreetingCacheConfig, RequestRouterConfig


def configuration(**entries):
    return GreetingCacheConfig(entries=[{'message': k, 'reply': v} for k, v in entries.items()])


def scope(config, tenant='tenant-a', workflow=None):
    return common_reply_scope(tenant, workflow or {'name': 'Orders', 'contract': 2}, config)


@pytest.mark.parametrize('raw,want', [
    (' HELLO ', 'hello'), ('hello!', 'hello!'), ('Hi\t\n there', 'hi there'),
    ('Cafe\u0301', 'café'), ('Straße', 'strasse'), ('Hello, buy 3 laptops', 'hello, buy 3 laptops'),
])
def test_lookup_normalisation_preserves_punctuation_and_words(raw, want):
    assert normalise_common_message(raw) == want


@pytest.mark.parametrize('message,reply', [
    ('Hello', 'Welcome'), ('HELLO', 'Welcome'), ('  hello\n ', 'Welcome'),
    ('hello!', 'Welcome!'), ('你好', '您好'), ('BUE\u0301NOS DI\u0301AS', 'Hola'),
])
def test_only_explicit_whole_message_aliases_get_prepared_replies(message, reply):
    config = configuration(hello='Welcome', **{'hello!': 'Welcome!', '你好': '您好', 'buénos días': 'Hola'})
    result = lookup_common_reply(message, config)
    assert (result.branch, result.reply, result.outcome) == ('reply', reply, 'local_fallback')


@pytest.mark.parametrize('message', ['Hello, buy 3 laptops', 'hello?', 'yes', 'no', 'approve', 'Buy laptops', ''])
def test_requests_and_unconfigured_commands_continue_unchanged(message):
    result = lookup_common_reply(message, configuration(hello='Welcome'))
    assert (result.branch, result.reply, result.outcome) == ('continue', '', 'not_common')


@pytest.mark.parametrize('messages', [('Hello', ' hello '), ('Café', 'Cafe\u0301'), ('Straße', 'STRASSE')])
def test_normalisation_collisions_are_configuration_errors(messages):
    with pytest.raises(ValueError, match='collide'):
        GreetingCacheConfig(entries=[{'message': m, 'reply': 'Hi'} for m in messages])


def test_a_long_multibyte_reply_is_returned_exactly():
    reply = '🙂' * 2000
    assert lookup_common_reply('hello', configuration(hello=reply)).reply == reply


@pytest.mark.parametrize('message', ['Hello', '  HELLO!? ', 'Hello, buy 3 laptops', 'Cafe\u0301', 'yes'])
def test_the_request_reader_uses_the_same_whole_message_rule(message):
    from backend.app.request_reader import RequestReaderConfig, prepared_reply
    entries = {'hello': 'Welcome', 'hello!?': 'Welcome!', 'café': 'Coffee'}
    reader = RequestReaderConfig(purpose='Orders', expected_schema={'type': 'object', 'properties': {'item': {'type': 'string'}}},
        required_fields=['/item'], field_rules=[{'field': '/item', 'kind': 'accept_quoted'}],
        prepared_replies=[{'message': k, 'reply': v} for k, v in entries.items()])
    result = lookup_common_reply(message, configuration(**entries))
    assert prepared_reply(message, reader) == (result.reply if result.branch == 'reply' else None)


@pytest.mark.parametrize('changes', [{'tenant_id': ''}, {'workflow_digest': 'x'}, {'reply_set_digest': 'x'}])
def test_scope_refuses_missing_identity_or_invalid_digests(changes):
    current = scope(configuration(hello='Hi'))
    with pytest.raises(ValueError): replace(current, **changes)


@pytest.fixture
def admission_workflow(monkeypatch):
    from backend.app.registry import REGISTRY, NodeDefinition
    async def echo(inputs, config, ctx):
        return {'branch': 'reply', 'reply': inputs['message'], 'decision': json.dumps({
            'route': 'out_of_scope', 'reason': 'Test echo', 'missing_fields': [], 'reply': inputs['message']})}
    monkeypatch.setitem(REGISTRY, 'request_router', NodeDefinition('request_router', 'Test router', 'Control', '',
        {'message': 'string'}, {'branch': 'string', 'reply': 'string', 'decision': 'string'},
        RequestRouterConfig, echo, branch_routes=('continue', 'reply')))
    return {'name': 'Prepared greeting', 'contract': 2, 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'cache', 'type': 'greeting_cache', 'inputs': {'message': 'input.message'},
         'config': {'entries': [{'message': 'hello', 'reply': 'Welcome'}]}},
        {'id': 'router', 'type': 'request_router', 'inputs': {'message': 'input.message'}, 'config': {'purpose': 'Echo original input'}},
        {'id': 'greeting', 'type': 'response', 'inputs': {'text': 'cache.reply'}},
        {'id': 'other', 'type': 'response', 'inputs': {'text': 'router.reply'}},
        {'id': 'accepted', 'type': 'response', 'inputs': {'text': 'input.message'}},
    ], 'edges': [
        {'id': 'a', 'source': 'input', 'target': 'cache'},
        {'id': 'b', 'source': 'cache', 'target': 'greeting', 'sourceHandle': 'reply'},
        {'id': 'c', 'source': 'cache', 'target': 'router', 'sourceHandle': 'continue'},
        {'id': 'd', 'source': 'router', 'target': 'other', 'sourceHandle': 'reply'},
        {'id': 'e', 'source': 'router', 'target': 'accepted', 'sourceHandle': 'continue'},
    ]}


@pytest.mark.asyncio
@pytest.mark.parametrize('message,want,destination', [('HELLO', 'Welcome', 'greeting'),
    ('Hello, buy 3 laptops', 'Hello, buy 3 laptops', 'other')])
async def test_graph_routes_whole_greeting_or_original_request_without_a_model(admission_workflow, message, want, destination):
    from backend.app.compiler import compile_workflow
    from backend.app.models import Workflow
    def model_forbidden(*args): raise AssertionError('A greeting cannot call a model')
    result = await compile_workflow(Workflow.model_validate(admission_workflow), model_forbidden,
        message=message, authorize_model=model_forbidden).graph.ainvoke({'values': {}})
    assert result['values'][destination]['text'] == want
    assert result['values']['input']['message'] == message
    if destination == 'greeting':assert 'router' not in result['values']
    provenance = result['values']['cache']['_provenance']
    assert 'reply' not in provenance.get('eligible_ports', [])


@pytest.mark.asyncio
async def test_worker_scopes_receipts_to_the_real_tenant_and_replays_the_checkpoint(tmp_path, admission_workflow, monkeypatch, caplog):
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from backend.app import common_replies
    store = Store(f'sqlite:///{tmp_path}/runs.db', Fernet.generate_key())
    worker = Worker(store)
    caplog.set_level(logging.INFO)
    original = store.worker_event
    def crash_after_cache(id, owner, event):
        persisted = original(id, owner, event)
        if event.get('node_id') == 'cache' and event.get('status') == 'success':
            raise RuntimeError('Injected crash after the greeting checkpoint commit')
        return persisted
    lookups = []
    real_lookup = common_replies.lookup_common_reply
    monkeypatch.setattr(common_replies, 'lookup_common_reply', lambda *a: lookups.append(a) or real_lookup(*a))
    try:
        digests = set()
        for tenant in ('tenant-one', 'tenant-two'):
            if tenant == 'tenant-two':monkeypatch.setattr(store, 'worker_event', crash_after_cache)
            row = store.create_run(admission_workflow, 'hello', tenant_id=tenant)
            claim = store.claim_next(worker.owner); await worker.execute(claim)
            finished = store.run(row['id'], tenant)
            if tenant == 'tenant-one':assert finished['status'] == 'success' and finished['output'] == 'Welcome'
            else:assert finished['status'] == 'failed'
            receipt = json.loads(finished['checkpoints']['cache']['receipt'])
            assert receipt['outcome'] == 'local_fallback'
            digests.add(receipt['workflow_digest'])
        before = len(lookups)
        monkeypatch.setattr(store, 'worker_event', original)
        store.resume_run(row['id'], tenant_id=tenant)
        await worker.execute(store.claim_next(worker.owner))
        assert store.run(row['id'], tenant)['output'] == 'Welcome' and len(lookups) == before
        lines = [json.loads(record.message) for record in caplog.records if record.name == 'relay.journal']
        assert any(line.get('event') == 'common_reply.lookup' and line.get('reason_code') == 'local_fallback' for line in lines)
        assert 'Welcome' not in json.dumps(lines) and 'hello' not in json.dumps(lines)
    finally: store.engine.dispose()


def test_readiness_no_longer_lists_a_prepared_reply_cache(tmp_path):
    from backend.app.main import create_app
    class Healthy:
        async def health(self): return {'status': 'ok'}
    app = create_app(f'sqlite:///{tmp_path}/ready.db', Fernet.generate_key(), auth_enabled=False,
        embedded_worker=False, knowledge_services=SimpleNamespace(management=Healthy(), index=Healthy()))
    with TestClient(app) as client:
        app.state.store.worker_seen('worker')
        reply = client.get('/api/ready')
        assert reply.status_code == 200 and 'common_reply_cache' not in reply.json()['checks']
