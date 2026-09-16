import json
import pytest
from backend.app.models import Workflow
from backend.app.registry import REGISTRY,Context,LLMConfig
from backend.app.compiler import compile_workflow,validate_workflow


def pdf_flow():
    return Workflow.model_validate({'version':1,'name':'PDF question','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'retrieve','type':'retrieval','inputs':{'query':'input.message'},'config':{'knowledge_base_id':'base','limit':4}},
        {'id':'answer','type':'grounded_answer','inputs':{'query':'retrieve.query','context':'retrieve.context','sources':'retrieve.sources'},'config':{'provider':'demo'}},
        {'id':'out','type':'response','inputs':{'text':'answer.text'}}],
        'edges':[{'id':'a','source':'input','target':'retrieve'},{'id':'b','source':'retrieve','target':'answer'},{'id':'c','source':'answer','target':'out'}]})

SOURCES=[{'id':'chunk','document_id':'doc','filename':'facts.pdf','page':2,'text':'The launch is on Monday.','score':1.0}]

def resolve(action,*args):
    if action=='retrieve':return SOURCES
    if action=='verify_sources':
        assert args[0]==SOURCES
        return SOURCES
    raise AssertionError(action)

@pytest.mark.asyncio
async def test_pdf_graph_retrieves_and_returns_page_evidence():
    assert 'retrieval' in REGISTRY
    graph=compile_workflow(pdf_flow(),message='When is launch?',knowledge_resolver=resolve).graph
    result=await graph.ainvoke({'values':{}})
    sources=json.loads(result['values']['answer']['sources'])
    assert sources[0]['filename']=='facts.pdf' and sources[0]['page']==2
    assert sources[0]['citation']=='S1'
    assert result['values']['out']['text']

@pytest.mark.asyncio
async def test_no_matches_skip_model_call(monkeypatch):
    from backend.app import registry
    async def forbidden(*args):raise AssertionError('No evidence must not call a model')
    monkeypatch.setattr(registry,'llm_node',forbidden)
    graph=compile_workflow(pdf_flow(),knowledge_resolver=lambda action,*args:[]).graph
    result=await graph.ainvoke({'values':{}})
    assert 'could not find' in result['values']['out']['text'].lower()

@pytest.mark.asyncio
async def test_grounded_answer_rejects_tampered_context():
    from backend.app.knowledge_nodes import grounded_answer_node
    with pytest.raises(ValueError,match='context'):
        await grounded_answer_node({'query':'Q','context':'FORGED','sources':json.dumps(SOURCES)},LLMConfig(),Context('',lambda _: '',knowledge=resolve))

@pytest.mark.asyncio
async def test_unknown_citations_are_not_presented_as_sources(monkeypatch):
    from backend.app import registry
    from backend.app.knowledge_nodes import grounded_answer_node,format_context
    async def model(*args):return {'text':'Monday [S1]. Invented [S99].','provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    output=await grounded_answer_node({'query':'Q','context':format_context(SOURCES),'sources':json.dumps(SOURCES)},LLMConfig(),Context('',lambda _: '',knowledge=resolve))
    assert '[S99]' not in output['text']
    assert 'unsupported reference' in output['text']
    assert json.loads(output['sources'])[0]['cited'] is True


def test_grounded_answer_uses_existing_model_configuration_validation():
    workflow=pdf_flow();workflow.nodes[2].config={'provider':'claude'}
    assert any('credential' in error for error in validate_workflow(workflow))

def test_invalid_retrieval_config_does_not_reach_storage():
    from backend.app.knowledge_nodes import knowledge_errors
    class Storage:
        def check_base(self,*args):raise AssertionError('Invalid config must be rejected before a database lookup')
    workflow=pdf_flow();workflow.nodes[1].config['knowledge_base_id']=['not','an','id']
    assert validate_workflow(workflow)
    assert knowledge_errors(Storage(),workflow,'local')==[]
