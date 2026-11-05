"""Read-only TA07 design probes. No application edits or paid provider calls.

The adapter rows establish the current baseline. The helper rows demonstrate why
TA07 must retain completeness separately from a numeric subtotal; they are not a
prototype of the future implementation.
"""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from backend.app.providers import CallUsage, record_usage
from backend.tests.test_usage_status import answer, counted, durable, flow, http, outcome, run


async def adapter_case(root, name, provider, body):
    directory = root / name
    directory.mkdir()
    monkeypatch = pytest.MonkeyPatch()
    try:
        seen = http(monkeypatch, [body])
        store, worker, credential = durable(directory, provider)
        try:
            result = await run(store, worker, flow(provider, credential))
            return {'case': name, 'provider': provider, 'requests': len(seen),
                    'run_status': result['status'], 'metrics': counted(store, result['id']),
                    'usage': outcome(result)['usage'], 'tokens_spent': result['accounting']['tokens_spent']}
        finally:
            store.engine.dispose()
    finally:
        monkeypatch.undo()


async def main():
    claude = answer('claude', prompt=10, completion=5)
    claude['usage'].update(cache_creation_input_tokens=400, cache_read_input_tokens=600)
    openai = answer('openai', prompt=100, completion=50)
    openai['usage']['prompt_tokens_details'] = {'cached_tokens': 60}
    ollama = answer('ollama', prompt=6024, completion=5)
    ollama['prompt_eval_cached_count'] = 6016
    with tempfile.TemporaryDirectory(prefix='ta07-review-') as directory:
        root = Path(directory)
        rows = [await adapter_case(root, 'claude-cache', 'claude', claude),
                await adapter_case(root, 'openai-read-only', 'openai', openai),
                await adapter_case(root, 'ollama-cache', 'ollama', ollama),
                await adapter_case(root, 'claude-no-counts', 'claude', answer('claude', usage={}))]
    # Passing only the known subtotal loses the fact that a required input component
    # was invalid. This helper currently counts two valid numbers as complete.
    subtotal = CallUsage()
    record_usage(subtotal, 410, 5)
    # A normalizer must also avoid turning 'nothing reported' into a made-up zero.
    zero_filled = CallUsage()
    record_usage(zero_filled, 0, None)
    helper_rows = [
        {'case': 'known-subtotal-through-unchanged-helper', 'usage': dict(subtotal),
         'actual_status': subtotal.status(), 'needed_if_cache_component_invalid': 'partial'},
        {'case': 'zero-fill-missing-input-through-unchanged-helper', 'usage': dict(zero_filled),
         'actual_status': zero_filled.status(), 'needed_if_no_counts_reported': 'unreported'},
    ]
    assert rows[0]['tokens_spent'] == 15
    assert rows[1]['tokens_spent'] == 150
    assert rows[2]['tokens_spent'] == 6029
    assert rows[3]['metrics']['unreported_calls'] == 1 and rows[3]['tokens_spent'] == 0
    assert subtotal.status() == 'reported' and zero_filled.status() == 'partial'
    destination = Path(__file__).with_name('probe-results.json')
    destination.write_text(json.dumps({'baseline': 'dce6f66', 'adapter_rows': rows,
                                      'helper_rows': helper_rows}, indent=2) + '\n')
    print(json.dumps({'baseline_adapter_rows': len(rows), 'completeness_demonstrations': len(helper_rows),
                      'assertions': 'passed', 'results': str(destination)}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
