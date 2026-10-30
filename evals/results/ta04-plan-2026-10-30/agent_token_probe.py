"""Real llm_node, synthetic HTTP: an agent with evidence runs out of tokens. What happens?"""
import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
import httpx
from pytest import MonkeyPatch
from backend.app import providers
from backend.app.compiler import compile_workflow
from backend.tests.test_token_budget import agent_with_retrieval
from backend.tests.test_agent_grounding import source, platform_with
from backend.tests.test_agent_team_budget import grounded

replies = [json.dumps({'action': 'call', 'target': 'calc', 'input': 'q'}), grounded('Finding [S1]', ['S1'])]
calls = []
def handle(request):
    calls.append(1)
    return httpx.Response(200, json={'message': {'content': replies[len(calls) - 1]}, 'prompt_eval_count': 100, 'eval_count': 0})

async def main():
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
        original = httpx.AsyncClient
        mp.setattr(providers, 'client', lambda timeout: original(transport=httpx.MockTransport(handle), trust_env=False))
        workflow = agent_with_retrieval()
        workflow.nodes[1].config = {'provider': 'ollama', 'model': 'fixture'}
        for policy in ('fail',):
            calls.clear(); workflow.nodes[1].on_error = policy; events = []
            async def emit(e): events.append(e)
            try:
                result = await compile_workflow(workflow, message='facts', platform_resolver=platform_with([source(1)], []), emit=emit).graph.ainvoke({'values': {}})
                agent = result['values'].get('agent', {})
                print(policy, 'provider calls', len(calls), 'agent keys', sorted(agent), 'grounding', agent.get('grounding', '')[:160])
            except Exception as exc:
                print(policy, 'provider calls', len(calls), 'raised', type(exc).__name__, str(exc)[:120])
            print('  failed events', [(e.get('node_id'), e.get('error', '')[:60], e.get('recovered')) for e in events if e.get('status') == 'failed'])
asyncio.run(main())
