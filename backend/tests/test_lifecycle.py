import asyncio
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.main import create_app as _create_app
from functools import partial
create_app = partial(_create_app, auth_enabled=False)
from backend.app.storage import Store
from backend.app.models import Workflow
from backend.app.compiler import validate_workflow
from backend.app.registry import REGISTRY
from backend.tests.test_compiler import sample


def test_workflows_survive_reopening_database(tmp_path):
    url=f'sqlite:///{tmp_path}/persist.db';key=Fernet.generate_key()
    first=Store(url,key)
    saved=first.save_workflow(sample())
    pending=first.create_run(sample(),'Ada')
    first.engine.dispose()
    second=Store(url,key)
    recovered=second.claim_next('replacement')
    assert second.workflow(saved['id'])['workflow']['name']=='Greeting'
    assert recovered['id']==pending['id']
    assert second.run(pending['id'])['status']=='running'
    second.engine.dispose()


def test_cancellation_is_terminal_and_recorded(tmp_path,monkeypatch):
    async def slow(inputs,config,ctx):
        await asyncio.sleep(10)
        return {'text':'never reached'}
    monkeypatch.setattr(REGISTRY['prompt'],'handler',slow)
    app=create_app(f'sqlite:///{tmp_path}/cancel.db',Fernet.generate_key())
    with TestClient(app) as client:
        id=client.post('/api/runs',json={'workflow':sample(),'message':'Ada'}).json()['id']
        assert client.post(f'/api/runs/{id}/cancel').json()['status']=='cancelled'
        run=client.get(f'/api/runs/{id}').json()
        assert run['status']=='cancelled'
        assert run['output']==''
        assert any(e['status']=='cancelled' for e in run['events'])


def test_branch_specific_output_cannot_feed_shared_successor():
    raw={'version':1,'name':'Unsafe merge','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'check','type':'condition','config':{'contains':'yes'},'inputs':{'value':'input.message'}},
        {'id':'a','type':'prompt','inputs':{'message':'input.message'}},
        {'id':'b','type':'prompt','inputs':{'message':'input.message'}},
        {'id':'out','type':'response','inputs':{'text':'a.text'}},
    ],'edges':[
        {'id':'one','source':'input','target':'check'},
        {'id':'two','source':'check','target':'a','sourceHandle':'true'},
        {'id':'three','source':'check','target':'b','sourceHandle':'false'},
        {'id':'four','source':'a','target':'out'},
        {'id':'five','source':'b','target':'out'},
    ]}
    assert any('binding' in e for e in validate_workflow(Workflow.model_validate(raw)))
