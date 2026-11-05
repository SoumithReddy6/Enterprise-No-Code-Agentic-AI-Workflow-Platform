"""TA06 draft review. Simulate only its proposed marker positions in memory.

No adapter or accounting implementation is changed on disk. Results describe draft
boundary problems, rather than a finished TA06 implementation.
"""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
from pytest import MonkeyPatch

from backend.app import providers

ROOT = Path.cwd()
OUT = Path(__file__).parent


async def marker_case(provider, kind):
    usage = {}
    observed = {'transport_calls': 0, 'complete_body_received': False,
                'object_parsed': False, 'record_usage_called': False}
    real_record, real_object = providers.record_usage, providers._json_object

    def endpoint(request):
        observed['transport_calls'] += 1
        if kind == 'invalid_json':
            return httpx.Response(200, text='not-json')
        if kind == 'http_error':
            return httpx.Response(401, json={'error': 'synthetic credentials rejection'})
        if provider == 'ollama':
            return httpx.Response(200, json={'message': {'content': 'answer'}})
        body = {'content': [{'type': 'text', 'text': 'answer'}]} if provider == 'claude' else {'choices': [{'message': {'content': 'answer'}}]}
        body['usage'] = [] if kind == 'bad_usage_shape' else None
        if kind == 'reported':
            body['usage'] = {'input_tokens': 10, 'output_tokens': 5} if provider == 'claude' else {'prompt_tokens': 10, 'completion_tokens': 5}
        return httpx.Response(200, json=body)

    class MarkedClient:
        def __init__(self):
            self.http = httpx.AsyncClient(transport=httpx.MockTransport(endpoint), trust_env=False)

        async def __aenter__(self):
            await self.http.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.http.__aexit__(*args)

        async def post(self, *args, **kwargs):
            # Exactly the draft's position, immediately before http.post.
            usage['_request_sent'] = True
            response = await self.http.post(*args, **kwargs)
            observed['complete_body_received'] = response.is_stream_consumed
            return response

    def marked_record(current, prompt_tokens, completion_tokens):
        observed['record_usage_called'] = True
        current['_response_read'] = True
        return real_record(current, prompt_tokens, completion_tokens)

    def parsed_object(*args):
        result = real_object(*args)
        observed['object_parsed'] = True
        return result

    with MonkeyPatch.context() as patch:
        patch.setattr(providers, 'client', lambda timeout: MarkedClient())
        patch.setattr(providers, 'record_usage', marked_record)
        patch.setattr(providers, '_json_object', parsed_object)
        if kind == 'invalid_url':
            patch.setenv('OLLAMA_BASE_URL', 'http://127.0.0.1:not-a-port')
        returned, error = False, None
        try:
            args = ['fixture', 'system', 'prompt'] + ([] if provider == 'ollama' else ['synthetic-key'])
            await getattr(providers, provider + '_chat')(*args, usage=usage)
            returned = True
        except Exception as exc:
            error = type(exc).__name__
    valid = [type(usage.get(key)) is int and usage[key] >= 0 for key in ('prompt_tokens', 'completion_tokens')]
    if returned or usage.get('_response_read'):
        status = 'reported' if all(valid) else 'partial' if any(valid) else 'unreported'
    else:
        status = 'unanswered' if usage.get('_request_sent') else None
    return {'provider': provider, 'case': kind, **observed, 'error_type': error,
            'status_from_literal_draft_markers': status, 'adapter_usage_dict': usage}


async def main():
    paths = list((ROOT / 'backend/app').rglob('*.py')) + [ROOT / 'docs/superpowers/specs/2026-11-05-unreported-usage-design.md']
    original = ROOT / 'evals/results/token-accounting-review-2026-10-25'
    paths.extend(p for p in original.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    before = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    spec = importlib.util.spec_from_file_location('original_token_probe', original / 'probe.py')
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    baseline = await probe.missing_usage()
    cases = [('ollama', 'invalid_url'), ('openai', 'bad_usage_shape'), ('claude', 'bad_usage_shape'),
             ('ollama', 'invalid_json'), ('ollama', 'http_error'), ('openai', 'unreported'),
             ('openai', 'reported')]
    rows = [await marker_case(provider, kind) for provider, kind in cases]
    assert rows[0]['transport_calls'] == 0 and rows[0]['status_from_literal_draft_markers'] == 'unanswered'
    assert all(row['object_parsed'] and not row['record_usage_called'] for row in rows[1:3])
    after = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert before == after
    report = {'head': 'bcc8d9b1e1e421a144b2738ce0630a21150c9061', 'baseline': baseline,
              'literal_draft_marker_probe': rows, 'preserved_sha256': before, 'original_files_unchanged': True}
    OUT.joinpath('boundary-results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'baseline': baseline, 'rows': rows}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
