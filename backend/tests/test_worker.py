import asyncio
import pytest
from cryptography.fernet import Fernet
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.app.registry import REGISTRY
from backend.tests.test_compiler import sample

@pytest.mark.asyncio
async def test_resume_reuses_completed_nodes(tmp_path,monkeypatch):
    store=Store(f'sqlite:///{tmp_path}/worker.db',Fernet.generate_key())
    calls=[]
    original=REGISTRY['prompt'].handler
    async def prompt(inputs,config,ctx):calls.append('prompt');return await original(inputs,config,ctx)
    async def broken(inputs,config,ctx):raise ValueError('Temporary failure')
    monkeypatch.setattr(REGISTRY['prompt'],'handler',prompt)
    monkeypatch.setattr(REGISTRY['response'],'handler',broken)
    run=store.create_run(sample(),'Ada')
    worker=Worker(store)
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run['id'])['status']=='failed'
    async def response(inputs,config,ctx):return {'text':inputs['text']}
    monkeypatch.setattr(REGISTRY['response'],'handler',response)
    store.resume_run(run['id'])
    await worker.execute(store.claim_next(worker.owner))
    result=store.run(run['id'])
    assert result['output']=='Hello Ada'
    assert calls==['prompt']
    assert any(e.get('cached') for e in result['events'])
