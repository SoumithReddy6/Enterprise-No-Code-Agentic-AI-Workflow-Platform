"""Model TA04's typed re-raise in grounded repair, without changing app files.

The original llm_node, provider adapter and Agent run. Two narrow shims model the
proposed typed entry refusal and re-raise through finalize_grounded_answer. They do
not implement D1. The Agent has evidence before its first model call; that call
returns invalid grounding and spends its remaining tokens. Its repair is refused.

Run: .venv/bin/python -I <this file> "$PWD"
"""
import asyncio
import json
import sys

sys.path.insert(0, sys.argv[1])

import httpx
from pytest import MonkeyPatch
from backend.app import agent_runtime, providers, registry, token_budget
from backend.app.compiler import compile_workflow
from backend.tests.test_agent_grounding import platform_with, source
from backend.tests.test_kb_workflow import graph


class ProposedTokenBudgetExhausted(ValueError):
    pass


async def main():
    calls = []
    repair_refusals = []
    evidence_in_prompt = []
    original_model = registry.llm_node
    original_finalize = agent_runtime.finalize_grounded_answer
    original_client = httpx.AsyncClient

    async def typed_model(inputs, config, ctx):
        if token_budget.exhausted(ctx.run):
            raise ProposedTokenBudgetExhausted(token_budget.reason(ctx.run))
        return await original_model(inputs, config, ctx)

    async def typed_finalize(*args, **kwargs):
        try:
            return await original_finalize(*args, **kwargs)
        except agent_runtime.GroundedContractError as exc:
            # Models the plan's except TokenBudgetExhausted: raise before wrapping.
            if isinstance(exc.__context__, ProposedTokenBudgetExhausted):
                repair_refusals.append(type(exc.__context__).__name__)
                raise exc.__context__
            raise

    def handle(request):
        calls.append(1)
        raw = request.content.decode()
        evidence_in_prompt.append('Fact 1' in raw and 'S1' in raw)
        return httpx.Response(200, json={
            'message': {'content': json.dumps({'answer': 'Finding [S1]',
                'citations': 'invalid: not a list', 'abstain': False, 'reason': ''})},
            'prompt_eval_count': 100, 'eval_count': 0})

    workflow = graph()
    agent = next(node for node in workflow.nodes if node.type == 'agent')
    agent.config = {'provider': 'ollama', 'model': 'fixture'}
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
        mp.setattr(providers, 'client', lambda timeout: original_client(
            transport=httpx.MockTransport(handle), trust_env=False))
        mp.setattr(registry, 'llm_node', typed_model)
        mp.setattr(agent_runtime, 'finalize_grounded_answer', typed_finalize)
        try:
            await compile_workflow(workflow, message='facts',
                platform_resolver=platform_with([source(1)], [])).graph.ainvoke({'values': {}})
        except ValueError as exc:
            print(json.dumps({'kind': 'plan-counterexample-with-typed-repair-shims',
                'provider_calls': len(calls), 'outcome': 'Agent fails under on_error=fail',
                'error_type': type(exc).__name__, 'error': str(exc),
                'typed_refusal_escaped_agent_finalization': repair_refusals,
                'model_received_evidence': evidence_in_prompt}, indent=2))
            assert len(calls) == 1
            assert repair_refusals == ['ProposedTokenBudgetExhausted']
            assert str(exc).startswith('Run token budget exhausted')
            assert evidence_in_prompt == [True]
        else:
            raise AssertionError('The expected typed repair refusal did not escape the Agent')


if __name__ == '__main__':
    asyncio.run(main())
