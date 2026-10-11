"""Prepared replies must never swallow requests or let Redis decide admission."""
import asyncio
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


class MemoryCache:
    """External-cache double: unit expiry uses a clock, integration uses real Redis."""
    def __init__(self):
        self.rows = {}; self.clock = 0; self.gets = []; self.sets = []; self.closed = False
    async def get(self, key):
        self.gets.append(key)
        value, expiry = self.rows.get(key, (None, 0))
        return value if expiry > self.clock else None
    async def set(self, key, value, ttl):
        self.sets.append((key, value, ttl)); self.rows[key] = (value, self.clock + ttl)
    async def health(self): return {'status': 'ok'}
    async def aclose(self): self.closed = True


def scope(config, tenant='tenant-a', workflow=None):
    return common_reply_scope(tenant, workflow or {'name': 'Orders', 'contract': 2}, config)


@pytest.mark.parametrize('raw,want', [
    (' HELLO ', 'hello'), ('hello!', 'hello!'), ('Hi\t\n there', 'hi there'),
    ('Cafe\u0301', 'café'), ('Straße', 'strasse'), ('Hello, buy 3 laptops', 'hello, buy 3 laptops'),
])
def test_lookup_normalisation_preserves_punctuation_and_words(raw, want):
    assert normalise_common_message(raw) == want


@pytest.mark.asyncio
@pytest.mark.parametrize('message,reply', [
    ('Hello', 'Welcome'), ('HELLO', 'Welcome'), ('  hello\n ', 'Welcome'),
    ('hello!', 'Welcome!'), ('你好', '您好'), ('BUE\u0301NOS DI\u0301AS', 'Hola'),
])
async def test_only_explicit_whole_message_aliases_get_prepared_replies(message, reply):
    config = configuration(hello='Welcome', **{'hello!': 'Welcome!', '你好': '您好', 'buénos días': 'Hola'})
    result = await lookup_common_reply(message, config, scope(config), None)
    assert (result.branch, result.reply, result.outcome) == ('reply', reply, 'local_fallback')


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['Hello, buy 3 laptops', 'hello?', 'yes', 'no', 'approve', 'Buy laptops', ''])
async def test_requests_and_unconfigured_commands_never_touch_cache(message):
    cache = MemoryCache(); config = configuration(hello='Welcome')
    result = await lookup_common_reply(message, config, scope(config), cache)
    assert (result.branch, result.reply, result.outcome) == ('continue', '', 'not_common')
    assert cache.gets == cache.sets == []


@pytest.mark.parametrize('messages', [('Hello', ' hello '), ('Café', 'Cafe\u0301'), ('Straße', 'STRASSE')])
def test_normalisation_collisions_are_configuration_errors(messages):
    with pytest.raises(ValueError, match='collide'):
        GreetingCacheConfig(entries=[{'message': m, 'reply': 'Hi'} for m in messages])


@pytest.mark.asyncio
async def test_fill_hit_expiry_and_exact_current_reply():
    cache = MemoryCache(); config = configuration(hello='Welcome'); current = scope(config)
    first = await lookup_common_reply('Hello', config, current, cache)
    second = await lookup_common_reply('HELLO', config, current, cache)
    assert (first.reply, first.outcome, second.reply, second.outcome) == ('Welcome', 'filled', 'Welcome', 'hit')
    assert len(cache.sets) == 1 and cache.sets[0][2] == 3600
    cache.clock = 3600
    assert (await lookup_common_reply('hello', config, current, cache)).outcome == 'filled'
    assert len(cache.sets) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('poison', [b'not json', b'{"route":"proceed"}', b'{"v":1,"reply":"Wrong"}',
    b'x' * 4097, '{"v":1,"reply":"Welcome"}', b'{"v":2,"reply":"Welcome"}'])
async def test_cache_content_cannot_replace_the_authoritative_reply(poison):
    cache = MemoryCache(); config = configuration(hello='Welcome'); current = scope(config)
    await lookup_common_reply('hello', config, current, cache)
    key = cache.gets[0]; cache.rows[key] = (poison, 3600)
    result = await lookup_common_reply('hello', config, current, cache)
    assert (result.branch, result.reply, result.outcome) == ('reply', 'Welcome', 'filled')
    assert cache.rows[key][0] == b'{"v":1,"reply":"Welcome"}'


@pytest.mark.asyncio
@pytest.mark.parametrize('where', ['get', 'set'])
async def test_cache_failure_returns_local_reply_without_leaking_error(where, caplog):
    cache = MemoryCache(); config = configuration(SECRET_GREETING='SECRET_REPLY')
    async def broken(*args): raise ConnectionError('redis://PRIVATE_PASSWORD@host')
    setattr(cache, where, broken)
    caplog.set_level(logging.INFO)
    result = await lookup_common_reply('SECRET_GREETING', config, scope(config), cache)
    assert (result.branch, result.reply, result.outcome) == ('reply', 'SECRET_REPLY', 'local_fallback')
    assert all(secret not in caplog.text for secret in ('PRIVATE_PASSWORD', 'SECRET_GREETING', 'SECRET_REPLY'))


@pytest.mark.asyncio
@pytest.mark.parametrize('where', ['get', 'set'])
async def test_hung_cache_operation_is_cancelled_and_returns_prepared_reply(where):
    cache = MemoryCache(); config = configuration(hello='Welcome'); stopped = asyncio.Event()
    async def hung(*args):
        try: await asyncio.Event().wait()
        finally: stopped.set()
    setattr(cache, where, hung)
    result = await asyncio.wait_for(lookup_common_reply('hello', config, scope(config), cache), 5)
    assert (result.reply, result.outcome) == ('Welcome', 'local_fallback')
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_get_and_fill_share_one_deadline(monkeypatch):
    """A new timeout per operation would wrongly allow this late fill to complete."""
    import backend.app.common_replies as replies
    cache = MemoryCache(); config = configuration(hello='Welcome'); deadlines = []
    real_timeout = asyncio.timeout
    def capture(seconds):
        deadlines.append(seconds)
        return real_timeout(seconds)
    monkeypatch.setattr(replies.asyncio, 'timeout', capture)
    assert (await lookup_common_reply('hello', config, scope(config), cache)).outcome == 'filled'
    assert deadlines == [0.2]


@pytest.mark.asyncio
async def test_cancellation_is_never_converted_into_a_reply():
    cache = MemoryCache(); config = configuration(hello='Welcome')
    async def cancelled(key): raise asyncio.CancelledError()
    cache.get = cancelled
    with pytest.raises(asyncio.CancelledError):
        await lookup_common_reply('hello', config, scope(config), cache)


@pytest.mark.asyncio
async def test_multibyte_reply_over_cache_byte_limit_is_kept_exact_locally():
    cache = MemoryCache(); reply = '🙂' * 2000; config = configuration(hello=reply)
    result = await lookup_common_reply('hello', config, scope(config), cache)
    assert (result.reply, result.outcome) == (reply, 'local_fallback')
    assert cache.gets == cache.sets == []


@pytest.mark.asyncio
async def test_tenant_workflow_and_reply_versions_have_disjoint_private_keys():
    cache = MemoryCache(); config = configuration(SECRET_GREETING='Welcome')
    revised = configuration(SECRET_GREETING='Revised reply')
    cases = [(config, scope(config)), (config, scope(config, tenant='SECRET_TENANT_B')),
             (config, scope(config, workflow={'name': 'Changed workflow'})), (revised, scope(revised))]
    for settings, current in cases:
        assert (await lookup_common_reply('SECRET_GREETING', settings, current, cache)).outcome == 'filled'
    assert len(set(cache.gets)) == 4
    assert 'SECRET_GREETING' not in str(cache.gets) and 'SECRET_TENANT_B' not in str(cache.gets)


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
async def test_worker_scopes_cache_to_real_tenant_and_replays_without_lookup(tmp_path, admission_workflow, monkeypatch, caplog):
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store = Store(f'sqlite:///{tmp_path}/runs.db', Fernet.generate_key())
    cache = MemoryCache(); worker = Worker(store, common_reply_cache=cache)
    caplog.set_level(logging.INFO)
    original = store.worker_event
    def crash_after_cache(id, owner, event):
        persisted = original(id, owner, event)
        if event.get('node_id') == 'cache' and event.get('status') == 'success':
            raise RuntimeError('Injected crash after the greeting checkpoint commit')
        return persisted
    try:
        for tenant in ('tenant-one', 'tenant-two'):
            if tenant == 'tenant-two':monkeypatch.setattr(store, 'worker_event', crash_after_cache)
            row = store.create_run(admission_workflow, 'hello', tenant_id=tenant)
            claim = store.claim_next(worker.owner); await worker.execute(claim)
            finished = store.run(row['id'], tenant)
            if tenant == 'tenant-one':assert finished['status'] == 'success' and finished['output'] == 'Welcome'
            else:assert finished['status'] == 'failed'
            receipt = json.loads(finished['checkpoints']['cache']['receipt'])
            assert receipt['outcome'] == 'filled'
        assert len(set(cache.gets)) == 2
        before = len(cache.gets)
        monkeypatch.setattr(store, 'worker_event', original)
        store.resume_run(row['id'], tenant_id=tenant)
        await worker.execute(store.claim_next(worker.owner))
        assert store.run(row['id'], tenant)['output'] == 'Welcome' and len(cache.gets) == before
        lines = [json.loads(record.message) for record in caplog.records if record.name == 'relay.journal']
        assert any(line.get('event') == 'common_reply.lookup' and line.get('reason_code') == 'filled' for line in lines)
        assert 'Welcome' not in json.dumps(lines) and 'hello' not in json.dumps(lines)
    finally: store.engine.dispose()


@pytest.mark.asyncio
async def test_readiness_reports_optional_cache_failure_without_failing_service(tmp_path):
    from backend.app.main import create_app
    class Healthy:
        async def health(self): return {'status': 'ok'}
    class Down(MemoryCache):
        async def health(self): raise ConnectionError('redis://SECRET@host')
    app = create_app(f'sqlite:///{tmp_path}/ready.db', Fernet.generate_key(), auth_enabled=False,
        embedded_worker=False, knowledge_services=SimpleNamespace(management=Healthy(), index=Healthy()))
    app.state.readiness.common_reply_cache = Down()
    with TestClient(app) as client:
        app.state.store.worker_seen('worker')
        reply = client.get('/api/ready')
        assert reply.status_code == 200
        assert reply.json()['checks']['common_reply_cache'] == {'status': 'unavailable', 'required': False}
        assert 'SECRET' not in reply.text


def test_app_closes_its_cache_at_shutdown(tmp_path, monkeypatch):
    from backend.app import common_replies
    from backend.app.main import create_app
    cache = MemoryCache(); monkeypatch.setattr(common_replies, 'cache_from_env', lambda: cache)
    app = create_app(f'sqlite:///{tmp_path}/close.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    with TestClient(app): assert not cache.closed
    assert cache.closed
