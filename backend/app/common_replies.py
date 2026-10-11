"""Exact prepared replies; Redis is disposable, configuration is authoritative."""
import asyncio
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit

from .request_contracts import GreetingCacheConfig

TTL_SECONDS = 3600
OPERATION_SECONDS = 0.2
MAX_CACHE_BYTES = 4096


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ReplyScope:
    tenant_id: str
    workflow_digest: str
    reply_set_digest: str

    def __post_init__(self):
        if not isinstance(self.tenant_id, str) or not self.tenant_id or len(self.tenant_id) > 128:
            raise ValueError('Prepared replies require a tenant identity.')
        for value in (self.workflow_digest, self.reply_set_digest):
            if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
                raise ValueError('Prepared replies require valid content digests.')


@dataclass(frozen=True)
class CommonReplyResult:
    branch: Literal['continue', 'reply']
    reply: str
    outcome: Literal['hit', 'filled', 'local_fallback', 'not_common']


class CommonReplyCache(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl: int) -> None: ...
    async def health(self) -> dict: ...
    async def aclose(self) -> None: ...


def normalise_common_message(message: str) -> str:
    """Lookup only: punctuation/words remain; downstream receives the original input."""
    return ' '.join(unicodedata.normalize('NFC', message).casefold().split())


def common_reply_scope(tenant_id, workflow, config):
    entries = {normalise_common_message(e.message): e.reply for e in config.entries}
    return ReplyScope(tenant_id, digest(workflow), digest(entries))


async def lookup_common_reply(message, config, scope, cache):
    """Only the saved configuration can select a branch or supply reply content."""
    normalised = normalise_common_message(message)
    configured = {normalise_common_message(e.message): e.reply for e in config.entries}
    if normalised not in configured:
        return CommonReplyResult('continue', '', 'not_common')
    reply = configured[normalised]
    fallback = CommonReplyResult('reply', reply, 'local_fallback')
    payload = json.dumps({'v': 1, 'reply': reply}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if cache is None or len(payload) > MAX_CACHE_BYTES:
        return fallback
    namespace = digest([scope.tenant_id, scope.workflow_digest, scope.reply_set_digest])
    key = 'relay:common:v1:' + namespace + ':' + digest(normalised)
    try:
        # One total budget, including connecting, reading and optional filling.
        async with asyncio.timeout(OPERATION_SECONDS):
            cached = await cache.get(key)
            # Exact byte equality with today's reply rejects malformed, stale and altered
            # cache values without even parsing untrusted JSON.
            if isinstance(cached, bytes) and cached == payload:
                return CommonReplyResult('reply', reply, 'hit')
            await cache.set(key, payload, TTL_SECONDS)
            return CommonReplyResult('reply', reply, 'filled')
    except Exception:
        return fallback  # CancelledError (BaseException) still escapes.


class RedisCommonReplyCache:
    def __init__(self, url: str):
        from redis.asyncio import Redis
        from redis.asyncio.retry import Retry
        from redis.backoff import NoBackoff
        parsed = urlsplit(url)
        if parsed.scheme not in ('redis', 'rediss') or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError('Use a Redis URL without query overrides.')
        self.client = Redis.from_url(url, decode_responses=False, protocol=2,
            socket_connect_timeout=OPERATION_SECONDS, socket_timeout=OPERATION_SECONDS,
            retry=Retry(NoBackoff(), 0), retry_on_timeout=False, retry_on_error=[],
            max_connections=16, health_check_interval=0)

    async def get(self, key):
        # GETRANGE prevents a poisoned multi-megabyte value from being read into memory.
        return await self.client.getrange(key, 0, MAX_CACHE_BYTES)

    async def set(self, key, value, ttl):
        if not isinstance(value, bytes) or len(value) > MAX_CACHE_BYTES or ttl != TTL_SECONDS:
            raise ValueError('Prepared cache values require the byte limit and fixed expiry.')
        await self.client.set(key, value, ex=ttl)

    async def health(self):
        return {'status': 'ok' if await self.client.ping() is True else 'unavailable'}

    async def aclose(self):
        await self.client.aclose()


class UnavailableCache:
    """Invalid optional configuration degrades locally without printing its URL."""
    async def get(self, key): raise ConnectionError('Prepared reply cache unavailable.')
    async def set(self, key, value, ttl): raise ConnectionError('Prepared reply cache unavailable.')
    async def health(self): return {'status': 'unavailable'}
    async def aclose(self): pass


def cache_from_env() -> CommonReplyCache | None:
    url = os.environ.get('COMMON_REPLY_REDIS_URL', '')
    if not url: return None
    try: return RedisCommonReplyCache(url)
    except (ValueError, TypeError): return UnavailableCache()


async def cache_health(cache):
    if cache is None: return {'status': 'disabled', 'required': False}
    try:
        async with asyncio.timeout(OPERATION_SECONDS):
            state = await cache.health()
        status = 'ok' if isinstance(state, dict) and state.get('status') == 'ok' else 'unavailable'
    except Exception: status = 'unavailable'
    return {'status': status, 'required': False}


async def greeting_cache_node(inputs, config: GreetingCacheConfig, ctx):
    if ctx.platform:
        result, current = await ctx.platform('common_reply', inputs['message'], config)
    else:
        # Direct compiler runs use no shared cache; production scopes in the worker.
        workflow = ctx.workflow.model_dump(mode='json') if ctx.workflow else {}
        current = common_reply_scope('local', workflow, config)
        result = await lookup_common_reply(inputs['message'], config, current, None)
    from .observability import journal
    journal(event='common_reply.lookup', node_id=ctx.node_id, reason_code=result.outcome)
    receipt = {'v': 1, 'outcome': result.outcome, 'workflow_digest': current.workflow_digest,
               'reply_set_digest': current.reply_set_digest}
    return {'branch': result.branch, 'reply': result.reply,
            'receipt': json.dumps(receipt, separators=(',', ':'))}
