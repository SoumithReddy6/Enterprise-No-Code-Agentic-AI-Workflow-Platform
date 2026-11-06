"""Real Worker probe: does a save barrier cover spend arriving during that save?"""
import asyncio
import argparse
import json
import logging
import tempfile
import threading
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from backend.app import accounting_state, providers, worker as worker_module
from backend.app.compiler import validate_workflow
from backend.app.models import Workflow
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_parallel_branches import document, join, model, prompt, response, start, until


async def run_probe(control=False):
    logging.getLogger('relay.journal').setLevel(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix='a53-pending-review-') as directory, pytest.MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT', '1000')
        store = Store(f'sqlite:///{directory}/probe.db', Fernet.generate_key())
        store.allow_model('ollama', 'fixture', '')
        worker = Worker(store)
        strict_entered = threading.Event()
        release_strict = threading.Event()
        release_b_save = threading.Event()
        b_started, release_b = asyncio.Event(), asyncio.Event()
        stale_detected = asyncio.Event()
        durability, observations, calls = [], [], []
        identifier = None
        fast_failure = []

        class ObservedDurability(accounting_state.AccountingDurability):
            def __init__(self, trusted):
                super().__init__(trusted)
                durability.append(self)

        mp.setattr(worker_module, 'AccountingDurability', ObservedDurability)
        if control:
            original_ready = accounting_state.ensure_accounting_ready

            async def rechecking_ready(run):
                while True:
                    await original_ready(run)
                    if not run['durability'].pending:
                        return
                    stale_detected.set()

            mp.setattr(accounting_state, 'ensure_accounting_ready', rechecking_ready)
        original_record, original_reserve = store.record_accounting, store.reserve_accounting

        def record(id, owner, snapshot):
            if snapshot['tokens_spent'] == 5 and not fast_failure:
                fast_failure.append(True)
                raise OSError('controlled fast-save outage')
            if snapshot['tokens_spent'] >= 10:
                assert release_b_save.wait(20), 'B save not released'
            return original_record(id, owner, snapshot)

        def reserve(id, owner, snapshot):
            if snapshot['tokens_spent'] == 5:
                strict_entered.set()
                assert release_strict.wait(20), 'strict save not released'
            return original_reserve(id, owner, snapshot)

        mp.setattr(store, 'record_accounting', record)
        mp.setattr(store, 'reserve_accounting', reserve)

        async def chat(model, system, prompt, usage=None, **kwargs):
            kind = 'b' if system == 'b' else 'a'
            calls.append(kind)
            if kind == 'b':
                b_started.set()
                await release_b.wait()
                result = 'answer b'
            elif calls.count('a') == 1:
                await b_started.wait()
                result = 'invalid JSON requiring a repair'
            else:
                d = durability[0]
                observations.append({'provider': 'a-repair', 'pending_at_dispatch': d.pending,
                    'generation': d.generation, 'acknowledged': d.acknowledged,
                    'stored_tokens': store.run(identifier)['accounting']['tokens_spent']})
                release_b_save.set()
                result = '{"amount": 1}'
            providers.record_usage(usage, 5, 0)
            return result

        mp.setattr(providers, 'ollama_chat', chat)
        agent = {'id': 'a1', 'type': 'agent', 'inputs': {'input': 'fork.text'},
                 'config': {'provider': 'ollama', 'model': 'fixture', 'role': 'extraction',
                            'output_schema': {'type': 'object', 'properties': {'amount': {'type': 'number'}}, 'required': ['amount']}}}
        raw = document([prompt('fork'), agent, model('b1', 'fork.text', 'b'), join(), response('a1.text')],
                       [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'),
                        ('a1', 'join'), ('b1', 'join'), ('join', 'out')])
        assert validate_workflow(Workflow.model_validate(raw)) == []
        task = None
        try:
            identifier, task = await start(store, worker, raw)
            await until(strict_entered.is_set)
            release_b.set()
            await until(lambda: durability[0].generation == 2)
            release_strict.set()
            if control:
                await asyncio.wait_for(stale_detected.wait(), 10)
                assert not observations, 'provider dispatched before the newer spend was saved'
                release_b_save.set()
            await until(lambda: bool(observations))
            await task
            row = store.run(identifier)
            assert observations[0]['pending_at_dispatch'] is (not control)
            assert row['status'] == 'success' and row['accounting']['tokens_spent'] == 15
            return {'control': control, 'observations': observations, 'calls': calls, 'status': row['status'],
                    'final_tokens': row['accounting']['tokens_spent'],
                    'scope': 'Real Worker and Store, actual Agent repair; only external model responses and persistence failures/timing are controlled.'}
        finally:
            release_strict.set()
            release_b_save.set()
            release_b.set()
            if task is not None and not task.done():
                await asyncio.gather(task, return_exceptions=True)
            store.engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--control', action='store_true')
    args = parser.parse_args()
    result = asyncio.run(run_probe(args.control))
    filename = 'pending-spend-control.json' if args.control else 'pending-spend.json'
    Path(__file__).with_name(filename).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
