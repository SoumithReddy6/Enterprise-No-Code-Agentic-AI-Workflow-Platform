"""Event storage for an agent running inside a loop.

Each loop item runs an agent that calls one read tool (returning VALUE_BYTES, default
40,000 - under the agent's 60,000-character request limit) and then answers. The model
fixture answers once the tool result is in its request, so every item makes exactly one
tool call. Real Worker and SQLite; the model and the tool transport are fixtures.
Reports stored run_events bytes and whether nested events carry the loop identity.
"""
import asyncio, json, sys, tempfile
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from backend.app import registry
from backend.app.models import Workflow
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_execution_safety import batch_write_flow, http_connection, jira_connection, jira_payload


def agent_loop_flow(store, items):
    http = http_connection(store)
    flow = batch_write_flow(jira_connection(store), http).model_dump()
    next(n for n in flow['nodes'] if n['id'] == 'each')['config']['max_items'] = 1000
    agent = next(n for n in flow['nodes'] if n['id'] == 'send')
    agent.update(type='agent', config={'provider': 'demo'})
    flow['nodes'].append({'id': 'lookup', 'type': 'tool_http', 'config': {'connection_id': http, 'method': 'GET'}})
    flow['edges'].append({'id': 'lookup', 'source': 'lookup', 'target': 'send', 'kind': 'tool', 'targetHandle': 'tools'})
    return Workflow.model_validate(flow)


async def measure(n, value_bytes):
    mp = pytest.MonkeyPatch()
    with tempfile.TemporaryDirectory() as d:
        store = Store(f'sqlite:///{d}/db', Fernet.generate_key()); worker = Worker(store)
        marker = 'x' * 64
        async def model(*args, **kwargs):
            seen = marker in json.dumps([args, kwargs], default=str)
            action = {'action': 'final', 'text': 'handled'} if seen else {'action': 'call', 'target': 'lookup', 'input': 'details'}
            return {'text': json.dumps(action), 'provider': 'demo'}
        async def prepared(p, *args): return jira_payload(n)
        async def execute(*args): return 'x' * value_bytes
        mp.setattr(registry, 'llm_node', model)
        mp.setattr(worker.tools, 'execute_prepared', prepared)
        mp.setattr(worker.tools, 'execute', execute)
        row = store.create_run(agent_loop_flow(store, n).model_dump(), 'go')
        await worker.execute(store.claim_next(worker.owner))
        run = store.run(row['id'])
        with store.engine.connect() as c:
            stored = c.execute(text('SELECT coalesce(sum(length(payload)),0), count(*), max(length(payload)) FROM run_events WHERE run_id=:i'), {'i': row['id']}).one()
        nested = [e for e in run['events'] if e.get('node_id') == 'lookup']
        with_loop = [e for e in nested if e.get('loop_node_id') == 'each' and e.get('item_index') is not None]
        failed_items = sum(1 for e in run['events'] if e.get('node_id') == 'send' and e.get('status') == 'failed')
    mp.undo()
    return {'items': n, 'status': run['status'], 'error': run.get('error', ''), 'run_events_bytes': stored[0], 'events': stored[1],
            'largest_event_bytes': stored[2], 'nested_tool_events': len(nested), 'nested_with_loop_identity': len(with_loop), 'failed_items': failed_items}


async def main():
    value = int(sys.argv[2]) if len(sys.argv) > 2 else 40000
    rows = [await measure(n, value) for n in (25, 50, 100)]
    for a, b in zip(rows, rows[1:]): b['growth_vs_previous'] = round(b['run_events_bytes'] / a['run_events_bytes'], 2)
    print(json.dumps(rows, indent=1))
    if len(sys.argv) > 1: Path(sys.argv[1]).write_text(json.dumps(rows, indent=1))
if __name__ == '__main__':
    asyncio.run(main())
