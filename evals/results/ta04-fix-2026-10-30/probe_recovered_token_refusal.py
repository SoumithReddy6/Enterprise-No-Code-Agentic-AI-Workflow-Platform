"""Revised copy of recovered_token_refusal from
evals/results/token-accounting-review-2026-10-25/probe.py (not run, not changed).

Same flow, same synthetic HTTP (8 + 2 tokens per call, limit 10), real adapter, worker
and SQLite. The original asserts the defect (truncated false under continue and route);
this copy asserts the TA04 result: the run is truncated with a budget cause.

Run: .venv/bin/python -I probe_recovered_token_refusal.py <repo root> <out.json>
"""
import asyncio, json, sys, tempfile
from contextlib import contextmanager
from pathlib import Path
ROOT = Path(sys.argv[1]); OUT = Path(sys.argv[2])
sys.path.insert(0, str(ROOT))
import httpx
from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from backend.app import providers
from backend.app.storage import Store
from backend.app.worker import Worker

MODEL = 'token-review-fixture'


def model_node(name, source='input.message', **extra):
    return {'id': name, 'type': 'llm', 'inputs': {'prompt': source},
            'config': {'provider': 'ollama', 'model': MODEL, 'max_tokens': 128}, **extra}


def flow(count=1):
    nodes = [{'id': 'input', 'type': 'chat_input'}]
    for i in range(count): nodes.append(model_node(f'm{i}', 'input.message' if i == 0 else f'm{i-1}.text'))
    nodes.append({'id': 'out', 'type': 'response', 'inputs': {'text': f'm{count-1}.text'}})
    return {'version': 1, 'name': 'Synthetic token review', 'nodes': nodes,
            'edges': [{'id': str(i), 'source': a['id'], 'target': b['id']} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))]}


def recovery_flow(policy):
    w = flow(2); w['nodes'][2]['on_error'] = policy
    w['nodes'][-1]['inputs'] = {'text': 'input.message'}
    if policy == 'route':
        w['nodes'].append({'id': 'fallback', 'type': 'response', 'inputs': {'text': 'input.message'}})
        w['edges'][-1]['sourceHandle'] = 'output'
        w['edges'].append({'id': 'error', 'source': 'm1', 'target': 'fallback', 'sourceHandle': 'error'})
    return w


@contextmanager
def database():
    with tempfile.TemporaryDirectory(prefix='relay-ta04-probe-') as directory:
        store = Store('sqlite:///' + str(Path(directory) / 'review.db'), Fernet.generate_key())
        store.allow_model('ollama', MODEL)
        try: yield store
        finally: store.engine.dispose()


async def recovered_token_refusal():
    results = {}
    for policy in ['fail', 'continue', 'route']:
        with MonkeyPatch.context() as mp, database() as store:
            mp.setenv('RELAY_RUN_TOKEN_LIMIT', '10'); calls = []
            def handler(request):
                calls.append(1)
                return httpx.Response(200, json={'message': {'content': 'answer'}, 'prompt_eval_count': 8, 'eval_count': 2})
            original = httpx.AsyncClient
            mp.setattr(providers, 'client', lambda timeout: original(transport=httpx.MockTransport(handler), trust_env=False))
            row = store.create_run(recovery_flow(policy), 'synthetic input'); worker = Worker(store)
            await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 10)
            saved = store.run(row['id'])
            failure = next(e for e in saved['events'] if e.get('node_id') == 'm1' and e['status'] == 'failed')
            assert len(calls) == 1 and 'Run token budget exhausted' in failure['error']
            assert saved['status'] == ('failed' if policy == 'fail' else 'success')
            results[policy] = {'status': saved['status'], 'provider_calls': len(calls),
                               'truncated': saved.get('truncated', False),
                               'truncation_reason': saved.get('truncation_reason', ''),
                               'truncation_causes': saved.get('truncation_causes', []),
                               'failure': {k: failure.get(k) for k in ('error', 'recovered', 'budget_exhausted')}}
    for policy in ('continue', 'route'):
        result = results[policy]
        assert result['truncated'] is True, policy
        assert result['truncation_reason'].startswith('m1 did not run: Run token budget exhausted'), policy
        assert [(c['node_id'], c['kind'], c['source']) for c in result['truncation_causes']] == [('m1', 'budget', 'confirmed')]
        assert result['failure']['budget_exhausted'] is True and result['failure']['recovered'] is True
    return results


OUT.write_text(json.dumps(asyncio.run(recovered_token_refusal()), indent=2) + '\n')
print('ok', OUT)
