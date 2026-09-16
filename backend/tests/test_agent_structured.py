"""Structured agent outputs are parsed and schema-checked by the runtime, never taken on trust."""
import json
import pytest
from backend.app.models import Workflow
from backend.app.compiler import compile_workflow,validate_workflow

SCHEMA={'type':'object','properties':{'category':{'type':'string','enum':['billing','shipping','other']},'reason':{'type':'string'}},'required':['category','reason'],'additionalProperties':False}

def flow(**config):
    return Workflow.model_validate({'version':1,'name':'Classify','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'agent','type':'agent','inputs':{'input':'input.message'},'config':{'provider':'demo',**config}},
        {'id':'out','type':'response','inputs':{'text':'agent.text'}}],
        'edges':[{'id':'a','source':'input','target':'agent'},{'id':'b','source':'agent','target':'out'}]})

def scripted(monkeypatch,responses,seen=None):
    from backend.app import registry
    replies=iter(responses)
    async def model(inputs,config,ctx):
        if seen is not None:seen.append((config.system,inputs['prompt']))
        return {'text':next(replies),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)

@pytest.mark.asyncio
async def test_schema_valid_output_is_normalised(monkeypatch):
    seen=[];scripted(monkeypatch,['```json\n{"category": "billing", "reason": "invoice question"}\n```'],seen)
    result=await compile_workflow(flow(role='classification',output_schema=SCHEMA),message='Why was I charged twice?').graph.ainvoke({'values':{}})
    assert json.loads(result['values']['out']['text'])=={'category':'billing','reason':'invoice question'}
    assert 'matching this JSON Schema' in seen[0][0] and '"enum"' in seen[0][0]

@pytest.mark.asyncio
async def test_invalid_output_gets_one_repair_then_passes(monkeypatch):
    seen=[];scripted(monkeypatch,['Sure! Here is the category: billing','{"category":"billing","reason":"invoice question"}'],seen)
    result=await compile_workflow(flow(role='classification',output_schema=SCHEMA),message='Charged twice').graph.ainvoke({'values':{}})
    assert json.loads(result['values']['out']['text'])['category']=='billing'
    assert len(seen)==2 and 'not valid JSON' in seen[1][1] and 'previous_output' in seen[1][1]

@pytest.mark.asyncio
async def test_output_violating_schema_twice_fails_the_node(monkeypatch):
    scripted(monkeypatch,['{"category":"refund","reason":"x"}','{"category":"refund"}'])
    with pytest.raises(ValueError,match='did not match the required structure'):
        await compile_workflow(flow(role='classification',output_schema=SCHEMA),message='Refund').graph.ainvoke({'values':{}})

@pytest.mark.asyncio
async def test_extraction_role_requires_a_json_object_even_without_schema(monkeypatch):
    scripted(monkeypatch,['["not","an","object"]','{"name":"Ada"}'])
    result=await compile_workflow(flow(role='extraction'),message='Extract').graph.ainvoke({'values':{}})
    assert result['values']['out']['text']=='{"name": "Ada"}'

def test_invalid_schema_is_rejected_at_validation():
    errors=validate_workflow(flow(output_schema={'type':'not-a-type'}))
    assert any('invalid configuration' in e for e in errors)
