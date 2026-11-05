"""Diagnostic counterfactual only: no product files or old evidence are edited.

Run from the repository root with PYTHONPATH=. The counterfactual uses the existing
invocation helper only for the compiler cancellation event. It is deliberately not a fix.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import threading

import httpx
from pytest import MonkeyPatch

from backend.app import compiler, worker as worker_module
from backend.app.worker import Worker

ROOT = Path.cwd()
spec = importlib.util.spec_from_file_location('old_usage_probe', ROOT / 'evals/results/token-accounting-review-2026-10-25/probe.py')
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)


def cancellation_only_change():
    source = (ROOT / 'backend/app/compiler.py').read_text()
    source = source[source.index('def compile_workflow('):]
    before = "                      await emit({'node_id': node.id, 'status': 'cancelled',**({'usage':run_state['usage'][node.id]} if run_state.get('usage',{}).get(node.id) else {})})"
    after = "                      from .execution_policy import usage_for\n                      usage=usage_for(run_state,identity)\n                      await emit({'node_id':node.id,'status':'cancelled',**({'usage':usage} if usage else {})})"
    assert source.count(before) == 1
    namespace = dict(vars(compiler))
    exec(compile(source.replace(before, after), '<diagnostic-cancellation-only-change>', 'exec'), namespace)
    return namespace['compile_workflow']


async def scenario(case, counterfactual):
    with MonkeyPatch.context() as patch, original.database() as store:
        patch.setenv('RELAY_RUN_TOKEN_LIMIT', '10000')
        if counterfactual:
            patch.setattr(worker_module, 'compile_workflow', cancellation_only_change())
        flow = original.team_flow()
        if case == 'cancel_inside_nested_agent':
            flow['nodes'].insert(-1, {'id': 'grand', 'type': 'agent',
                                      'config': {'provider': 'ollama', 'model': original.MODEL}})
            flow['edges'].append({'id': 'grand', 'source': 'helper', 'target': 'grand', 'kind': 'agent'})
        replies = [json.dumps({'action': 'call', 'target': 'helper', 'input': 'task'}),
                   json.dumps({'action': 'call', 'target': 'grand', 'input': 'task'})
                   if case == 'cancel_inside_nested_agent' else 'helper answer',
                   json.dumps({'action': 'final', 'text': 'combined answer'})]
        injected_failure = case in ('unsafe_finalization', 'uncertain_finalization')
        if injected_failure:
            from backend.app import request_safety
            from backend.app.tool_service import UncertainWriteError
            finalize = request_safety.finalize

            async def refuse(ctx, node, inputs, outputs, config):
                if node.id == 'agent':
                    error_type = request_safety.UnsafeExecutionError if case == 'unsafe_finalization' else UncertainWriteError
                    raise error_type('synthetic refusal after paid work')
                return await finalize(ctx, node, inputs, outputs, config)

            patch.setattr(request_safety, 'finalize', refuse)
        entered = asyncio.Event()
        requests, node_tasks = [], []

        async def endpoint(request):
            index = len(requests)
            requests.append(index)
            node_tasks.append(asyncio.current_task())
            if not injected_failure and case != 'cancel_during_child_commit' and index == 2:
                entered.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json={'message': {'content': replies[index]},
                                             'prompt_eval_count': 10, 'eval_count': 5})

        original.transport(patch, endpoint)
        committed, release = threading.Event(), threading.Event()
        if case == 'cancel_during_child_commit':
            writer = store.worker_event

            def hold(identifier, owner, event, **kwargs):
                result = writer(identifier, owner, event, **kwargs)
                if event.get('node_id') == 'helper' and event.get('status') == 'success':
                    committed.set()
                    assert release.wait(10)
                return result

            patch.setattr(store, 'worker_event', hold)
        row = store.create_run(flow, 'synthetic task')
        worker = Worker(store)
        task = asyncio.create_task(worker.execute(store.claim_next(worker.owner)))
        try:
            if injected_failure:
                pass
            elif case == 'cancel_during_child_commit':
                assert await asyncio.to_thread(committed.wait, 10)
                store.request_cancel(row['id'])
                # Cancel the real graph task while its child's committed event is awaiting
                # acknowledgment. The shield still settles that event before unwinding.
                node_tasks[1].cancel()
                release.set()
            else:
                await asyncio.wait_for(entered.wait(), 10)
                store.request_cancel(row['id'])
            await asyncio.wait_for(task, 15)
            saved = store.run(row['id'])
            totals = original.totals(store, row['id'])
            known = 45 if injected_failure else 30
            assert saved['accounting']['tokens_spent'] == known, saved
            return {'case': case, 'counterfactual': counterfactual, 'status': saved['status'],
                    'known_tokens': known, 'accounting_tokens': saved['accounting']['tokens_spent'],
                    'metrics': totals,
                    'usage_events': [{'node': e.get('node_id'), 'status': e['status'],
                                      'calls': e['usage'].get('calls'),
                                      'tokens': e['usage'].get('prompt_tokens', 0) + e['usage'].get('completion_tokens', 0)}
                                     for e in saved['events'] if e.get('usage')]}
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def main():
    rows = []
    for case in ('cancel_after_child_success', 'cancel_inside_nested_agent', 'cancel_during_child_commit'):
        for counterfactual in (False, True):
            rows.append(await scenario(case, counterfactual))
    for case in ('unsafe_finalization', 'uncertain_finalization'):
        rows.append(await scenario(case, False))
    Path(__file__).with_name('nested-boundaries.json').write_text(json.dumps({'rows': rows}, indent=2) + '\n')
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
