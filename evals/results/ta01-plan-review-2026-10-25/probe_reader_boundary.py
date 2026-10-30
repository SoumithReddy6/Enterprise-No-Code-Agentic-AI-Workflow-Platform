"""Verify existing reader repair boundaries; no application edits or remote requests."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import httpx
from pytest import MonkeyPatch
from backend.app import providers, token_budget
from backend.app.registry import Context
from backend.app.request_reader import read_request, RequestReaderConfig
from backend.tests.test_request_reader import settings, reading

async def main():
    plan=ROOT/'docs/superpowers/plans/2026-10-25-token-usage-on-rejected-responses.md'
    before=hashlib.sha256(plan.read_bytes()).hexdigest()
    calls=[];run={}
    def handler(request):
        calls.append(1)
        text='' if len(calls)==1 else reading(item='laptops',quantity=3,unit_price=450)
        return httpx.Response(200,json={'message':{'content':text},'prompt_eval_count':5,'eval_count':2})
    with MonkeyPatch.context() as mp:
        mp.setenv('RELAY_RUN_TOKEN_LIMIT','1000');client=httpx.AsyncClient
        mp.setattr(providers,'client',lambda timeout:client(transport=httpx.MockTransport(handler),trust_env=False))
        try:await read_request('3 laptops at $450 each.',RequestReaderConfig.model_validate(settings()),Context('',lambda _:'',node_id='reader',run=run))
        except providers.ProviderResponseError as exc:error=str(exc)
        else:raise AssertionError('Provider rejection should escape the reader, without repair')
    assert len(calls)==1
    result={'provider_calls':len(calls),'repair_attempted':False,'error':error,
            'accounted_tokens':token_budget.tokens_spent(run),
            'plan_unchanged':hashlib.sha256(plan.read_bytes()).hexdigest()==before}
    assert result['plan_unchanged']
    (Path(__file__).parent/'reader-boundary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':asyncio.run(main())
