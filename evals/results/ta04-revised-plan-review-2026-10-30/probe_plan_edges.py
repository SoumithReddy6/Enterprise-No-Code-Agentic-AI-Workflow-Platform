"""Bounded checks of the revised TA04 plan, without changing application code.

Uses real llm_node/provider code with synthetic HTTP. compact_reason models only
the stated new marker preservation/replay rule; branch_coverage runs current code.
Run: .venv/bin/python -I <file> "$PWD"
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock

sys.path.insert(0, sys.argv[1])

import httpx
from pytest import MonkeyPatch
from backend.app import agent_runtime, iteration, providers, token_budget
from backend.app.compiler import compile_workflow
from backend.app.models import Workflow
from backend.tests.test_agent_grounding import platform_with, source
from backend.tests.test_kb_workflow import graph


def compact_reason():
    reason = token_budget.reason({'token_baseline': 100}, limit=100)
    entry = {'index': 1, 'status': 'failed', 'value': '', 'error': reason,
             'budget_halt': True}
    # Exactly the marker preservation promised in section 4h, not a TA04 fix.
    compacted = {**iteration.compact(entry), 'budget_halt': True}
    prior = {'index': 0, 'status': 'success', 'value': 'Finding', 'error': ''}
    fresh = iteration.completeness('each', 3, 3, [prior, compacted], 1,
                                   ('budget', 2, reason))
    replay = iteration.completeness('each', 3, 3, [prior, compacted], 1,
                                    ('budget', 2, 'A run budget was exhausted.'))
    assert fresh != replay
    return {'kind': 'model-of-section-4h-not-an-implemented-fix',
            'fresh_reason': fresh['truncation_reason'],
            'replayed_reason': replay['truncation_reason'],
            'same_summary': fresh == replay,
            'marker_survives': compacted['budget_halt']}


async def branch_coverage(with_evidence):
    workflow = graph() if with_evidence else Workflow.model_validate({
        'version': 1, 'name': 'Structured without grounding',
        'nodes': [{'id': 'input', 'type': 'chat_input'},
                  {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'}},
                  {'id': 'out', 'type': 'response', 'inputs': {'text': 'agent.text'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'agent'},
                  {'id': 'b', 'source': 'agent', 'target': 'out'}]})
    agent = next(n for n in workflow.nodes if n.type == 'agent')
    agent.config = {'provider': 'ollama', 'model': 'fixture', 'output_schema': {
        'type': 'object', 'required': ['amount'],
        'properties': {'amount': {'type': 'number'}}, 'additionalProperties': False}}
    calls = []

    def handle(request):
        calls.append(1)
        return httpx.Response(200, json={'message': {'content': 'not JSON'},
            'prompt_eval_count': 100, 'eval_count': 0})

    original_client = httpx.AsyncClient
    finalizer = AsyncMock(wraps=agent_runtime.finalize_grounded_answer)
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
        mp.setattr(providers, 'client', lambda timeout: original_client(
            transport=httpx.MockTransport(handle), trust_env=False))
        mp.setattr(agent_runtime, 'finalize_grounded_answer', finalizer)
        try:
            await compile_workflow(workflow, message='facts',
                platform_resolver=platform_with([source(1)], [])).graph.ainvoke({'values': {}})
        except ValueError as exc:
            error = str(exc)
        else:
            raise AssertionError('Expected refusal during repair')
    assert len(calls) == 1
    assert finalizer.await_count == (1 if with_evidence else 0)
    expected = 'Invalid grounded answer contract' if with_evidence else 'Agent output did not match the required structure'
    assert error.startswith(expected)
    return {'kind': 'current-code-branch-observation', 'with_evidence': with_evidence,
            'provider_calls': len(calls), 'grounded_finalizer_calls': finalizer.await_count,
            'structured_only_path_exercised': finalizer.await_count == 0,
            'error': error}


async def main():
    print(json.dumps({'compaction': compact_reason(),
                      'structured_with_evidence': await branch_coverage(True),
                      'structured_without_evidence': await branch_coverage(False)}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
