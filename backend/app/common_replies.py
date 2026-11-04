"""Exact prepared replies, looked up in the saved configuration only. No cache, no model."""
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from .request_contracts import GreetingCacheConfig


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
    # local_fallback names a configuration reply; hit/filled exist only in older run records.
    outcome: Literal['local_fallback', 'not_common']


def normalise_common_message(message: str) -> str:
    """Lookup only: punctuation/words remain; downstream receives the original input."""
    return ' '.join(unicodedata.normalize('NFC', message).casefold().split())


def common_reply_scope(tenant_id, workflow, config):
    entries = {normalise_common_message(e.message): e.reply for e in config.entries}
    return ReplyScope(tenant_id, digest(workflow), digest(entries))


def lookup_common_reply(message, config):
    """Only the saved configuration can select a branch or supply reply content."""
    normalised = normalise_common_message(message)
    configured = {normalise_common_message(e.message): e.reply for e in config.entries}
    if normalised not in configured:
        return CommonReplyResult('continue', '', 'not_common')
    return CommonReplyResult('reply', configured[normalised], 'local_fallback')


async def greeting_cache_node(inputs, config: GreetingCacheConfig, ctx):
    if ctx.platform:
        result, current = await ctx.platform('common_reply', inputs['message'], config)
    else:
        # Direct compiler runs use no shared cache; production scopes in the worker.
        workflow = ctx.workflow.model_dump(mode='json') if ctx.workflow else {}
        current = common_reply_scope('local', workflow, config)
        result = lookup_common_reply(inputs['message'], config)
    from .observability import journal
    journal(event='common_reply.lookup', node_id=ctx.node_id, reason_code=result.outcome)
    receipt = {'v': 1, 'outcome': result.outcome, 'workflow_digest': current.workflow_digest,
               'reply_set_digest': current.reply_set_digest}
    return {'branch': result.branch, 'reply': result.reply,
            'receipt': json.dumps(receipt, separators=(',', ':'))}
