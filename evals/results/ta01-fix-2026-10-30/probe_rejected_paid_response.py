"""Revised copy of rejected_paid_response from evals/results/token-accounting-review-2026-10-25/probe.py.

The original asserts the broken result (0 tokens) and is kept unchanged as evidence. This
copy sends the same HTTP 200 response (empty visible text, finish_reason length, 100 prompt
and 50 completion tokens), measures what Relay accounted, asserts 150 and writes the
measured value. Synthetic HTTP only; no remote request is made.
"""
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import httpx
from pytest import MonkeyPatch
from backend.app import providers, registry, token_budget
from backend.app.execution_policy import ExecutionIdentity
from backend.app.registry import Context, LLMConfig

BODY = {'choices': [{'message': {'role': 'assistant', 'content': ''}, 'finish_reason': 'length'}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150,
                  'completion_tokens_details': {'reasoning_tokens': 50}}}


async def main():
    snapshots = []
    async def persist(snapshot): snapshots.append(snapshot)
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT', '1000')
        client = httpx.AsyncClient
        mp.setattr(providers, 'client', lambda timeout: client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=BODY)), trust_env=False))
        run = {'usage': {}, 'usage_by_invocation': {}, 'persist_spend': persist}
        ctx = Context('', lambda _: 'fixture-secret', node_id='model', checkpoint_owner='model',
                      execution_identity=ExecutionIdentity('model', 'model', 'model'), run=run)
        try:
            await registry.llm_node({'prompt': 'fixture'}, LLMConfig(provider='openai', model='fixture', credential_id='key'), ctx)
        except providers.ProviderResponseError as exc:
            error = str(exc)
        else:
            raise AssertionError('expected the empty answer to be rejected')
    accounted = token_budget.tokens_spent(run)
    assert accounted == 150, accounted
    assert [s['tokens_spent'] for s in snapshots] == [150]
    result = {'response_reported_tokens': 150, 'accounted_tokens': accounted,
              'durable_snapshots_tokens_spent': [s['tokens_spent'] for s in snapshots],
              'calls': run['usage']['model']['calls'], 'error': error}
    (Path(__file__).parent / 'rejected-response.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
