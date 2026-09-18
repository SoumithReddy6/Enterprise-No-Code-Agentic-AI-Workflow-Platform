"""The Retrieve → Agent path carries the same grounding guarantees as the Query node."""
import json
import pytest
from backend.app.agent_runtime import NO_EVIDENCE,declares_insufficient_evidence
from backend.app.compiler import compile_workflow
from backend.tests.test_kb_workflow import graph
from backend.tests.test_platform_graph import platform_flow

def source(i):
    return {'id':f'chunk{i}','knowledge_base_id':'kb1','version':1,'document_id':'doc','filename':'facts.txt','page':1,'text':f'Fact {i}','score':.5,'url':'/api/knowledge-bases/kb1/documents/doc/file'}

def platform_with(sources,verified):
    async def platform(action,*args):
        if action=='kb_retrieve':return {'query':args[1],'knowledge_base_id':'kb1','version':1,'sources':sources}
        if action=='record_vector_sources':return None
        if action=='verify_vector_sources':verified.append(args[0]);return args[0]
        raise AssertionError(action)
    return platform

@pytest.mark.asyncio
async def test_invented_citations_are_marked_and_cited_sources_reported(monkeypatch):
    from backend.app import registry
    async def model(*args):return {'text':'The codename is Bluebird [S1]. Launch is in October [S7].','provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    verified=[]
    result=await compile_workflow(graph(),message='Codename?',platform_resolver=platform_with([source(1),source(2)],verified)).graph.ainvoke({'values':{}})
    agent=result['values']['agent']
    assert agent['text']=='The codename is Bluebird [S1]. Launch is in October [unsupported reference].'
    sources=json.loads(agent['sources'])
    assert [(s['citation'],s['cited']) for s in sources]==[('S1',True),('S2',False)]
    # Post-generation verification receives canonical sources without presentation fields.
    assert verified==[[source(1),source(2)]]
    assert result['values']['out']['text']==agent['text']

@pytest.mark.asyncio
async def test_no_evidence_means_no_model_call(monkeypatch):
    from backend.app import registry
    async def model(*args):raise AssertionError('The model must not be called without evidence')
    monkeypatch.setattr(registry,'llm_node',model)
    result=await compile_workflow(graph(),message='Codename?',platform_resolver=platform_with([],[])).graph.ainvoke({'values':{}})
    agent=result['values']['agent']
    assert agent['text'].startswith('I could not find matching evidence') and agent['provider']=='none' and agent['sources']=='[]'

@pytest.mark.asyncio
async def test_plain_input_is_not_treated_as_evidence():
    result=await compile_workflow(platform_flow(),message='{"passages": "not a list"} [S1]').graph.ainvoke({'values':{}})
    agent=result['values']['agent']
    assert '[S1]' in agent['text'] and agent['sources']=='[]'


@pytest.mark.asyncio
async def test_declared_grounded_abstention_cannot_include_a_guess(monkeypatch):
    from backend.app import registry
    async def model(*args):
        return {'text':'The evidence is insufficient to identify the bank. However, my educated guess is Northstar Bank [S1].','provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    result=await compile_workflow(graph(),message='Which bank?',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    agent=result['values']['agent']
    assert agent['text']==NO_EVIDENCE
    assert json.loads(agent['sources'])[0]['cited'] is False


def test_correct_answer_is_not_abstention_when_only_other_passages_lack_information():
    answer='The on-call stipend is $350 per week [S1].\n\nThe other passages provide no direct information about the stipend.'

    assert declares_insufficient_evidence(answer) is False
