"""Real async adapter, disposable Redis namespace; CI supplies an isolated server."""
import asyncio
import os
import uuid

import pytest

from backend.app.common_replies import (RedisCommonReplyCache, cache_from_env, cache_health,
    common_reply_scope, lookup_common_reply)
from backend.app.request_contracts import GreetingCacheConfig


@pytest.mark.parametrize('url', ['http://localhost', 'redis://', 'redis://localhost?retry_on_timeout=True',
    'redis://localhost?socket_timeout=50', 'redis://localhost#secret'])
def test_url_cannot_override_safe_client_settings(url):
    with pytest.raises(ValueError):RedisCommonReplyCache(url)


@pytest.mark.asyncio
async def test_disabled_or_invalid_optional_configuration_has_safe_readiness(monkeypatch):
    monkeypatch.delenv('COMMON_REPLY_REDIS_URL', raising=False)
    assert await cache_health(cache_from_env()) == {'status': 'disabled', 'required': False}
    monkeypatch.setenv('COMMON_REPLY_REDIS_URL', 'redis://SECRET:PRIVATE@host?socket_timeout=999')
    cache = cache_from_env()
    assert await cache_health(cache) == {'status': 'unavailable', 'required': False}
    await cache.aclose()


@pytest.mark.asyncio
async def test_hung_real_connection_has_one_attempt_and_shutdown_closes_pool():
    connections = []; writers = []; entered = asyncio.Event()
    async def hang(reader, writer):
        connections.append(1); writers.append(writer); entered.set()
        try:await reader.read()  # The adapter must cancel/disconnect its connection.
        finally:writer.close(); await writer.wait_closed()
    server = await asyncio.start_server(hang, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    cache = RedisCommonReplyCache(f'redis://127.0.0.1:{port}/0')
    config = GreetingCacheConfig(entries=[{'message': 'hello', 'reply': 'Welcome'}])
    try:
        result = await asyncio.wait_for(lookup_common_reply('hello', config,
            common_reply_scope('one', {}, config), cache), 5)
        assert entered.is_set() and connections == [1]
        assert (result.branch, result.reply, result.outcome) == ('reply', 'Welcome', 'local_fallback')
    finally:
        await cache.aclose(); server.close()
        for writer in writers:writer.close(); await writer.wait_closed()
        await server.wait_closed()
    assert all(not connection.is_connected for connection in cache.client.connection_pool._available_connections)


@pytest.mark.asyncio
async def test_immediate_read_failure_is_not_retried():
    """A slow connection can hit the outer deadline before hidden retries occur.

    This RESP peer completes the real client's handshake, then closes each GETRANGE
    immediately. Enabling redis-py's retries would therefore make several read attempts.
    """
    reads = []; writers = []
    async def disconnect_on_read(reader, writer):
        writers.append(writer)
        try:
            while header := await reader.readline():
                count = int(header[1:]); command = []
                for _ in range(count):
                    size = int((await reader.readline())[1:])
                    command.append((await reader.readexactly(size + 2))[:-2])
                if command[0].upper() == b'GETRANGE':
                    reads.append(command); break
                writer.write(b'+OK\r\n'); await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):pass
        finally:writer.close(); await writer.wait_closed()
    server = await asyncio.start_server(disconnect_on_read, '127.0.0.1', 0)
    cache = RedisCommonReplyCache(f'redis://127.0.0.1:{server.sockets[0].getsockname()[1]}/0')
    config = GreetingCacheConfig(entries=[{'message': 'hello', 'reply': 'Welcome'}])
    try:
        result = await asyncio.wait_for(lookup_common_reply('hello', config,
            common_reply_scope('one', {}, config), cache), 5)
        assert (result.reply, result.outcome) == ('Welcome', 'local_fallback')
        assert len(reads) == 1, f'{len(reads)} Redis read attempts; cache retries must be disabled'
    finally:
        await cache.aclose(); server.close()
        for writer in writers:writer.close(); await writer.wait_closed()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_worker_closes_its_owned_cache_on_stop(monkeypatch):
    from backend.app import common_replies
    from backend.app.worker import Worker
    from backend.tests.test_common_replies import MemoryCache
    cache = MemoryCache(); entered = asyncio.Event()
    monkeypatch.setattr(common_replies, 'cache_from_env', lambda: cache)
    class Store:
        def worker_seen(self, owner):pass
        def claim_next(self, owner):entered.set(); return None
    monkeypatch.setattr('backend.app.approvals.expire_pending', lambda store: None)
    worker = Worker(Store())
    task = asyncio.create_task(worker.serve())
    try:await asyncio.wait_for(entered.wait(), 5)
    finally:task.cancel(); await asyncio.gather(task, return_exceptions=True)
    assert cache.closed


@pytest.mark.asyncio
async def test_real_redis_expiry_poison_and_tenant_policy_namespaces():
    url = os.environ.get('RELAY_TEST_REDIS_URL')
    if not url:pytest.skip('Isolated Redis not configured; CI sets RELAY_TEST_REDIS_URL.')
    cache = RedisCommonReplyCache(url); unique = uuid.uuid4().hex; keys = []
    config = GreetingCacheConfig(entries=[{'message': 'hello', 'reply': 'Welcome'}])
    try:
        assert await cache_health(cache) == {'status': 'ok', 'required': False}
        current = common_reply_scope(unique, {'name': 'Orders'}, config)
        assert (await lookup_common_reply('hello', config, current, cache)).outcome == 'filled'
        # Identify only this fresh namespace; no FLUSHDB and no shared production keys.
        from backend.app.common_replies import digest
        prefix = 'relay:common:v1:' + digest([unique, current.workflow_digest, current.reply_set_digest]) + ':'
        keys.extend([key async for key in cache.client.scan_iter(match=prefix+'*')])
        assert len(keys) == 1
        ttl = await cache.client.ttl(keys[0]); assert 3500 < ttl <= 3600
        assert (await lookup_common_reply('HELLO', config, current, cache)).outcome == 'hit'
        # Exercise real server expiry without waiting an hour or replacing its clock.
        await cache.client.expire(keys[0], 1)
        async with asyncio.timeout(10):
            while await cache.client.exists(keys[0]):await asyncio.sleep(.05)
        assert (await lookup_common_reply('hello', config, current, cache)).outcome == 'filled'
        await cache.client.set(keys[0], b'x' * 100000, ex=3600)
        assert len(await cache.get(keys[0])) == 4097
        fixed = await lookup_common_reply('hello', config, current, cache)
        assert (fixed.reply, fixed.outcome) == ('Welcome', 'filled')
        assert await cache.client.get(keys[0]) == b'{"v":1,"reply":"Welcome"}'
        for tenant, workflow, settings in [
            (unique+'-other', {'name': 'Orders'}, config),
            (unique, {'name': 'Revised policy'}, config),
            (unique, {'name': 'Orders'}, GreetingCacheConfig(entries=[{'message': 'hello', 'reply': 'New'}])),
        ]:
            revised = common_reply_scope(tenant, workflow, settings)
            revised_prefix = 'relay:common:v1:' + digest([tenant, revised.workflow_digest, revised.reply_set_digest]) + ':'
            assert (await lookup_common_reply('hello', settings, revised, cache)).outcome == 'filled'
            keys.extend([key async for key in cache.client.scan_iter(match=revised_prefix+'*')])
        assert len(set(keys)) == 4
        # Guards against actually leaking connections at shutdown (not just calling close).
        pool_connections = list(cache.client.connection_pool._available_connections)
    finally:
        if keys:await cache.client.delete(*keys)
        await cache.aclose()
    assert pool_connections and all(not c.is_connected for c in pool_connections)
