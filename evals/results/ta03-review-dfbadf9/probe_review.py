"""Independent TA03 review scenarios with synthetic providers and disposable storage."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile

import httpx
from pytest import MonkeyPatch

from backend.tests import test_usage_attribution as helpers

ROOT = Path.cwd()


def load_probe(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def loop_with_specialist(directory):
    with MonkeyPatch.context() as patch:
        calls = []
        helpers.script(patch, [(helpers.call('helper'), 10, 5), (asyncio.CancelledError(), 10, 5)], calls)
        store, worker, row = helpers.loop_setup(patch, directory, 'nested-loop')
        try:
            document = store.run(row['id'])['workflow']
            document['nodes'].append({'id': 'helper', 'type': 'agent', 'config': dict(helpers.B)})
            document['edges'].append({'id': 'helper', 'source': 'work', 'target': 'helper', 'kind': 'agent'})
            # Create the actual scenario after the helper has been attached.
            row = store.create_run(document, 'go')
            # The fixture also created an unused queued run. Execute only our claim.
            unused = store.claim_next('unused-fixture')
            assert unused['id'] != row['id']
            await helpers.attempt(store, worker)
            saved = store.run(row['id'])
            metrics = helpers.metrics(store, row['id'])
            assert saved['accounting']['tokens_spent'] == 30
            assert metrics == {'tokens': 30, 'calls': 2,
                               'by_model': {'fixture': (15, 1), 'fixture-b': (15, 1)}}
            assert helpers.attributed(saved['events']) == {'work': 15, 'helper': 15}
            assert not saved.get('loop_progress', {}).get('each')
            return {'case': 'loop_with_cancelled_specialist', 'metrics': metrics,
                    'accounting_tokens': 30, 'unfinished_item_not_reusable': True}
        finally:
            store.engine.dispose()


async def paused_commit(directory, target, deadline=False):
    with MonkeyPatch.context() as patch:
        deadlines = []
        if deadline:
            from backend.app import worker as worker_module
            patch.setattr(worker_module, 'RUN_TIMEOUT_SECONDS', 60)
            real_timeout = asyncio.timeout

            def capture(delay):
                value = real_timeout(delay)
                if delay == 60:
                    deadlines.append(value)
                return value

            patch.setattr(worker_module.asyncio, 'timeout', capture)
        patch.setattr(helpers.tool_service, 'public_addresses', lambda *_: ['93.184.216.34'])
        calls, tasks, requests = [], [], []
        store, worker = helpers.durable(directory / target)
        worker.tools.transport = httpx.MockTransport(lambda request: requests.append(request.method) or httpx.Response(200, text='sent'))
        connection = worker.tools.save_connection({'name': 'sink', 'provider': 'http', 'endpoint': 'https://example.com'})
        document = helpers.team(helper=helpers.B)
        document['nodes'].append({'id': 'sink', 'type': 'tool_http',
                                  'config': {'connection_id': connection['id'], **helpers.POST}})
        document['edges'].append({'id': 'sink', 'source': 'sink', 'target': 'helper',
                                  'kind': 'tool', 'targetHandle': 'tools'})
        helpers.script(patch, [(helpers.call('helper'), 10, 5), (helpers.call('sink', 'hello'), 10, 5)], calls, tasks)
        committed, release = helpers.hold(patch, store, lambda event: event.get('node_id') == target and event.get('status') == 'paused')
        row = store.create_run(document, 'send')
        task = asyncio.create_task(worker.execute(store.claim_next(worker.owner)))
        try:
            assert await asyncio.to_thread(committed.wait, 10)
            if deadline:
                assert len(deadlines) == 1
                deadlines[0].reschedule(asyncio.get_running_loop().time())
                while not deadlines[0].expired():
                    await asyncio.sleep(0)
            else:
                store.request_cancel(row['id'])
                tasks[-1].cancel()
            release.set()
            await asyncio.wait_for(task, 15)
            saved = store.run(row['id'])
            metrics = helpers.metrics(store, row['id'])
            assert metrics == {'tokens': 30, 'calls': 2,
                               'by_model': {'fixture': (15, 1), 'fixture-b': (15, 1)}}
            assert saved['accounting']['tokens_spent'] == 30 and requests == []
            assert helpers.attributed(saved['events']) == {'agent': 15, 'helper': 15}
            return {'case': f'cancel_during_{target}_pause_commit', 'deadline': deadline,
                    'status': saved['status'], 'error': saved.get('error', ''), 'metrics': metrics,
                    'accounting_tokens': 30, 'sent_requests': len(requests)}
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            store.engine.dispose()


async def main():
    diagnostic = load_probe('ta03_diagnostic', 'evals/results/ta03-design-2026-11-02/probe_nested_boundaries.py')
    rows = []
    for case in ('cancel_after_child_success', 'cancel_inside_nested_agent', 'cancel_during_child_commit',
                 'unsafe_finalization', 'uncertain_finalization'):
        row = await diagnostic.scenario(case, False)
        assert row['metrics']['tokens'] == row['known_tokens'] == row['accounting_tokens'], row
        assert row['metrics']['calls'] == row['known_tokens'] // 15, row
        rows.append(row)
    control = await diagnostic.original.nested_usage(False)
    assert control['operator_metrics']['tokens'] == control['persisted_accounting_tokens'] == 45
    rows.append({'case': 'ordinary_success_control', **control})
    with tempfile.TemporaryDirectory(prefix='ta03-auditor-') as directory:
        root = Path(directory)
        rows.append(await loop_with_specialist(root))
        rows.append(await paused_commit(root, 'helper'))
        rows.append(await paused_commit(root, 'agent'))
    report = {'passed': True, 'rows': rows}
    Path(__file__).with_name('independent-probes.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{len(rows)} independent scenarios passed')


if __name__ == '__main__':
    asyncio.run(main())
