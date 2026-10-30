"""Probe the TA04 proposal's loop replay boundary without changing application code.

The narrow shim models the proposed policy outcome for a token refusal. Everything
else is current production code, including Query, llm_node, item persistence shape,
result budgeting and loop replay. HTTP is synthetic; no external service is used.
This is a plan counterexample, not a test of an implemented TA04 fix.

Run: .venv/bin/python -I <this file> "$PWD"
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import sys

sys.path.insert(0, sys.argv[1])

import httpx
from pytest import MonkeyPatch
from backend.app import iteration, providers
from backend.app.execution_policy import accounting_snapshot
from backend.app.registry import Context, ForEachConfig
from backend.tests.test_agent_grounding import platform_with, source
from backend.tests.test_agent_team_budget import grounded
from backend.tests.test_iteration import loop_flow


async def main():
    workflow = loop_flow()
    body = next(node for node in workflow.nodes if node.id == 'work')
    body.type = 'query'
    body.config = {'provider': 'ollama', 'model': 'fixture',
                   'knowledge_base_id': 'kb1', 'mode': 'keyword'}
    config = ForEachConfig(body='work', max_items=3, on_item_error='continue')
    original_policy = iteration.execute_with_policy

    async def proposed_outcome(*args, **kwargs):
        outcome = await original_policy(*args, **kwargs)
        # Models TA04 sections 4b/4c for this one known refusal. This shim is not a
        # proposed implementation: it deliberately leaves the replay path unchanged.
        if outcome.status == 'failed' and (outcome.error or '').startswith('Run token budget exhausted'):
            return replace(outcome, budget_exhausted=True, attempts=1)
        return outcome

    async def run_case(interrupt):
        reads = []
        model_calls = []
        interrupted = False
        backing = platform_with([source(1)], [])

        async def platform(action, *args):
            if action == 'kb_retrieve':
                reads.append(args[1])
            return await backing(action, *args)

        def handle(request):
            model_calls.append(1)
            return httpx.Response(200, json={
                'message': {'content': grounded('Finding [S1]', ['S1'])},
                'prompt_eval_count': 100, 'eval_count': 0})

        async def emit(event):
            nonlocal interrupted
            if (interrupt and not interrupted and event.get('kind') == 'loop_item'
                    and event.get('item_index') == 1):
                # for_each_node has already called record_progress for this entry.
                interrupted = True
                raise asyncio.CancelledError('after failed item progress was saved')

        run = {'contract': 1, 'usage': {}, 'usage_by_invocation': {},
               'action_budget': {'remaining': 40, 'counter': 0, 'ceiling': 40}}
        ctx = Context(message='facts', resolve_credential=lambda _: '',
                      platform=platform, node_id='each', node_type='for_each',
                      workflow=workflow, emit=emit, run=run)
        original_client = httpx.AsyncClient
        with MonkeyPatch.context() as mp:
            mp.setattr(providers, 'client', lambda timeout: original_client(
                transport=httpx.MockTransport(handle), trust_env=False))
            try:
                result = await iteration.for_each_node({'items': ['q0', 'q1', 'q2']}, config, ctx)
            except asyncio.CancelledError:
                snapshot = accounting_snapshot(run)
                resumed = {
                    'contract': 1, 'token_baseline': snapshot['tokens_spent'],
                    'usage': {}, 'usage_by_invocation': {},
                    'action_budget': deepcopy(snapshot['action_budget']),
                    'loop_progress': deepcopy(run['loop_progress']),
                    'citation_counter': run.get('citation_counter', 0),
                }
                result = await iteration.for_each_node(
                    {'items': ['q0', 'q1', 'q2']}, config, replace(ctx, run=resumed))
                run = resumed
        return {'retrieved_items': reads, 'provider_calls': len(model_calls),
                'actions': run['action_budget']['counter'],
                'result_statuses': [entry['status'] for entry in result['results']],
                'summary': json.loads(result['summary'])}

    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
        mp.setattr(iteration, 'execute_with_policy', proposed_outcome)
        uninterrupted = await run_case(False)
        resumed = await run_case(True)
    report = {'kind': 'plan-counterexample-with-policy-outcome-shim',
              'uninterrupted': uninterrupted, 'interrupted_then_resumed': resumed}
    print(json.dumps(report, indent=2))
    assert uninterrupted['retrieved_items'] == ['q0', 'q1']
    assert resumed['retrieved_items'] == ['q0', 'q1', 'q2']
    assert uninterrupted['provider_calls'] == resumed['provider_calls'] == 1
    assert uninterrupted['actions'] == 2 and resumed['actions'] == 3
    assert len(uninterrupted['result_statuses']) == 2
    assert len(resumed['result_statuses']) == 3


if __name__ == '__main__':
    asyncio.run(main())
