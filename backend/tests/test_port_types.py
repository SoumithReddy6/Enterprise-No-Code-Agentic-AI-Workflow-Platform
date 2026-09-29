"""Phase 1: declaration checks are advisory and existing string ports stay silent."""
import ast
import asyncio
import json
from dataclasses import replace
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.port_types import valid_type,compatibility
from backend.app.compiler import type_warnings
from backend.app.models import Workflow
from backend.app.registry import REGISTRY
from backend.tests.test_compiler import sample

TYPES=['string','number','boolean','object','array','array<string>','array<object>']
# Each row lists expected results in TYPES order; covers the full 7 x 7 matrix.
MATRIX=[
 ['ok','coerce','coerce','coerce','coerce','coerce','coerce'],
 ['coerce','ok','mismatch','mismatch','mismatch','mismatch','mismatch'],
 ['coerce','mismatch','ok','mismatch','mismatch','mismatch','mismatch'],
 ['coerce','mismatch','mismatch','ok','mismatch','mismatch','mismatch'],
 ['coerce','mismatch','mismatch','mismatch','ok','coerce','coerce'],
 ['coerce','mismatch','mismatch','mismatch','ok','ok','mismatch'],
 ['coerce','mismatch','mismatch','mismatch','ok','mismatch','ok'],
]

@pytest.mark.parametrize('name',TYPES)
def test_vocabulary(name):assert valid_type(name)

@pytest.mark.parametrize('name',['String','integer','array<number>','array<boolean>','array<array>','',None,42,[]])
def test_unknown_vocabulary(name):assert not valid_type(name)

@pytest.mark.parametrize('i',range(7))
@pytest.mark.parametrize('j',range(7))
def test_compatibility_matrix(i,j):assert compatibility(TYPES[i],TYPES[j])==MATRIX[i][j]

def test_unknown_types_never_report_compatibility():
    assert compatibility('typo','typo')=='mismatch'
    assert compatibility('typo','string')=='mismatch'

def test_every_registry_port_is_known_and_structured_ports_are_declared():
    """Structured ports are added deliberately, one at a time. This list is the record of
    which nodes have left the all-strings baseline, so an accidental relabel is caught."""
    STRUCTURED={('tool_jira','items'),('for_each','items'),('for_each','results')}
    assert REGISTRY
    found=set()
    for definition in REGISTRY.values():
        for port,name in [*definition.inputs.items(),*definition.outputs.items()]:
            assert valid_type(name),(definition.type,port,name)
            if name!='string':found.add((definition.type,port))
    assert found==STRUCTURED,found.symmetric_difference(STRUCTURED)
    assert REGISTRY['tool_jira'].outputs=={'text':'string','items':'array<object>'}
    assert REGISTRY['for_each'].inputs=={'items':'array'}
    assert REGISTRY['for_each'].outputs=={'results':'array<object>','failed':'string','summary':'string'}

def test_examples_are_silent():
    paths=list(Path('examples').glob('*.json'));assert paths
    for path in paths:assert type_warnings(Workflow.model_validate(json.loads(path.read_text())))==[],path

def test_every_campaign_workflow_is_silent():
    # Evaluate only the campaign's workflow-building expressions, with resource
    # placeholders. No indexing, networking, worker execution, or copied fixtures.
    from scripts import check_workflows as campaign
    tree=ast.parse(Path('scripts/check_workflows.py').read_text())
    helpers=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in ('agent','retrieve')]
    namespace={**vars(campaign),'LLM':{'provider':'demo'},'KB':'kb','github':'http','c':'http'}
    exec(compile(ast.Module(body=helpers,type_ignores=[]),'campaign-builders','exec'),namespace)
    expressions=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='wf']
    assert len(expressions)>=13
    for expression in expressions:
        document=eval(compile(ast.Expression(expression),'campaign-workflow','eval'),namespace)
        assert type_warnings(Workflow.model_validate(document))==[],document['name']

def patch_types(monkeypatch,source,target):
    monkeypatch.setitem(REGISTRY,'chat_input',replace(REGISTRY['chat_input'],outputs={'message':source}))
    monkeypatch.setitem(REGISTRY,'prompt',replace(REGISTRY['prompt'],inputs={'message':target}))

@pytest.mark.parametrize('source,target,phrase',[('object','number','incompatible'),('array<object>','string','conversion to text'),('string','array<string>','parsing or element validation')])
def test_warning_names_binding_types_and_advisory_action(monkeypatch,source,target,phrase):
    patch_types(monkeypatch,source,target)
    warnings=type_warnings(Workflow.model_validate(sample()))
    assert len(warnings)==1
    assert all(text in warnings[0] for text in ('prompt',"'message'",'input.message',source,target,phrase,'no conversion or rejection'))

def test_invalid_binding_remains_error_validators_responsibility():
    document=sample();document['nodes'][1]['inputs']={'message':'missing.port'}
    assert type_warnings(Workflow.model_validate(document))==[]

def test_warning_does_not_block_validate_save_or_run(monkeypatch,tmp_path):
    from backend.app.main import create_app
    from backend.app.worker import Worker
    patch_types(monkeypatch,'object','number')
    app=create_app(f'sqlite:///{tmp_path}/ports.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False)
    with TestClient(app) as client:
        checked=client.post('/api/validate',json=sample())
        assert checked.status_code==200
        assert checked.json()['valid'] and checked.json()['errors']==[] and checked.json()['warnings']
        assert client.post('/api/workflows',json=sample()).status_code==201
        response=client.post('/api/runs',json={'workflow':sample(),'message':'Ada'})
        assert response.status_code==201
        worker=Worker(app.state.store);asyncio.run(worker.execute(app.state.store.claim_next(worker.owner)))
        run=client.get('/api/runs/'+response.json()['id']).json()
        assert run['status']=='success' and run['output']=='Hello Ada'
