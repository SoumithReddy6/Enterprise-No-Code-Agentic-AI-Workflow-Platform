"""Probe the new Agent early returns in a disposable partial-plan candidate.

No production database, model endpoint or external write is used. This demonstrates
what must be specified/tested; it is not a claim that TA04 has been implemented.
Run after prepare_agent_candidate.py:
  .venv/bin/python -I <file> /private/tmp/<archive-export>
"""
import asyncio
import json
import sys

sys.path.insert(0, sys.argv[1])

import httpx
from pytest import MonkeyPatch
from backend.app import providers
from backend.app.agent_runtime import validate_structured
from backend.app.compiler import compile_workflow
from backend.app.models import Workflow
from backend.tests.test_agent_grounding import source
from backend.tests.test_agent_team_budget import grounded
from backend.tests.test_kb_workflow import graph
from backend.tests.test_token_budget import agent_with_retrieval

SCHEMA = {'type': 'object', 'required': ['amount'],
          'properties': {'amount': {'type': 'number'}}, 'additionalProperties': False}


async def scenario(mode):
    plain = mode == 'structured_no_evidence'
    step = mode == 'step_budget_revoked'
    workflow = agent_with_retrieval() if step else graph()
    if plain:
        workflow = Workflow.model_validate({'version': 1, 'name': 'Structured',
            'nodes': [{'id': 'input', 'type': 'chat_input'},
                      {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'}},
                      {'id': 'out', 'type': 'response', 'inputs': {'text': 'agent.text'}}],
            'edges': [{'id': 'a', 'source': 'input', 'target': 'agent'},
                      {'id': 'b', 'source': 'agent', 'target': 'out'}]})
    agent = next(n for n in workflow.nodes if n.id == 'agent')
    agent.config = {'provider': 'ollama', 'model': 'fixture',
                    **({'output_schema': SCHEMA} if plain else {})}
    available = True
    calls = []
    verification = []
    events = []

    async def platform(action, *args):
        if action == 'kb_retrieve':
            assert available
            return {'query': args[1], 'knowledge_base_id': 'kb1', 'version': 1,
                    'sources': [source(1)]}
        if action in ('record_vector_sources', 'verify_vector_sources'):
            verification.append({'operation': action, 'available': available})
            if not available:
                raise ValueError('Knowledge source was removed during the call')
            return args[0] if action == 'verify_vector_sources' else None
        raise AssertionError(action)

    async def emit(event):
        nonlocal available
        events.append(event)
        if step and event.get('kind') == 'agent_budget':
            available = False

    def handle(request):
        nonlocal available
        calls.append(1)
        if step:
            text = json.dumps({'action': 'call', 'target': 'calc', 'input': 'q'})
        elif mode.startswith('valid_final'):
            text = grounded('Finding [S1]', ['S1'])
        else:
            text = 'not JSON'
        if mode in ('valid_final_revoked', 'repair_budget_revoked'):
            available = False
        return httpx.Response(200, json={'message': {'content': text},
            'prompt_eval_count': 100, 'eval_count': 0})

    original_client = httpx.AsyncClient
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
        mp.setattr(providers, 'client', lambda timeout: original_client(
            transport=httpx.MockTransport(handle), trust_env=False))
        try:
            result = await compile_workflow(workflow, message='facts',
                platform_resolver=platform, emit=emit).graph.ainvoke({'values': {}})
        except ValueError as exc:
            return {'mode': mode, 'status': 'failed', 'error': str(exc),
                    'provider_calls': len(calls), 'verification': verification}
    output = result['values']['agent']
    grounding = json.loads(output['grounding'])
    row = {'mode': mode, 'status': 'success', 'provider_calls': len(calls),
           'verification': verification, 'source_available_at_return': available,
           'sources_returned': len(json.loads(output['sources'])),
           'abstain': grounding.get('abstain'), 'truncated': grounding.get('truncated', False)}
    if plain:
        try:
            validate_structured(output['text'], SCHEMA)
        except ValueError:
            row['business_output_matches_schema'] = False
        else:
            row['business_output_matches_schema'] = True
    return row


async def main():
    guarded = '--guarded' in sys.argv[2:]
    rows = [await scenario(mode) for mode in (
        'valid_final_at_limit', 'valid_final_revoked', 'repair_budget_sources_valid',
        'repair_budget_revoked', 'step_budget_revoked', 'structured_no_evidence')]
    print(json.dumps({'kind': 'partial-plan-Agent-candidate', 'guards_preserved': guarded,
                      'rows': rows}, indent=2))
    by_mode = {row['mode']: row for row in rows}
    assert all(row['provider_calls'] == 1 for row in rows)
    assert by_mode['valid_final_at_limit']['status'] == 'success'
    assert by_mode['valid_final_at_limit']['truncated'] is False
    assert by_mode['valid_final_revoked']['status'] == 'failed'
    assert by_mode['repair_budget_sources_valid']['status'] == 'success'
    for mode in ('repair_budget_revoked', 'step_budget_revoked'):
        if guarded:
            assert by_mode[mode]['status'] == 'failed'
            assert by_mode[mode]['error'] == 'Knowledge source was removed during the call'
        else:
            assert by_mode[mode]['status'] == 'success'
            assert by_mode[mode]['sources_returned'] == 1
            assert by_mode[mode]['source_available_at_return'] is False
    if guarded:
        assert by_mode['structured_no_evidence']['status'] == 'failed'
        assert by_mode['structured_no_evidence']['error'].startswith('Run token budget exhausted')
    else:
        assert by_mode['structured_no_evidence']['status'] == 'success'
        assert by_mode['structured_no_evidence']['business_output_matches_schema'] is False


if __name__ == '__main__':
    asyncio.run(main())
