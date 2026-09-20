"""The Retrieve → Agent path carries the same grounding guarantees as the Query node."""
import json
from types import SimpleNamespace
import pytest
from backend.app.agent_runtime import NO_EVIDENCE,ground_answer,remember_evidence
from backend.app.compiler import compile_workflow
from backend.tests.test_kb_workflow import graph
from backend.tests.test_platform_graph import platform_flow
from backend.app.registry import response_node
from backend.app.models import Workflow

@pytest.mark.asyncio
async def test_empty_evidence_abstention_reason_cannot_smuggle_a_factual_guess():
    from backend.app.agent_runtime import finalize_grounded_answer
    async def repair(*args):raise AssertionError('Valid contract needs no repair')
    raw=json.dumps({'answer':'','citations':[],'abstain':True,'reason':'Maybe employees get 12 weeks of leave [S1].'})
    text,sources,metadata=await finalize_grounded_answer(raw,[],repair)
    assert '12 weeks' not in text and '[S1]' not in text and sources==[]
    assert json.loads(metadata)['reason']=='No evidence passages were retrieved.'

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
    async def model(*args):return {'text':json.dumps({'answer':'The codename is Bluebird [S1]. Launch is in October [S7].','citations':['S1'],'abstain':False,'reason':''}),'provider':'demo'}
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
async def test_grounded_abstention_with_guess_is_repaired_to_clean_abstention(monkeypatch):
    from backend.app import registry
    replies=iter([
        json.dumps({'answer':'My educated guess is Northstar Bank [S1].','citations':['S1'],'abstain':True,'reason':'The evidence does not identify the bank.'}),
        json.dumps({'answer':'','citations':[],'abstain':True,'reason':'The evidence does not identify the bank.'}),
    ])
    async def model(*args):return {'text':next(replies),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    result=await compile_workflow(graph(),message='Which bank?',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    agent=result['values']['agent']
    assert agent['text']==NO_EVIDENCE+'\n\nReason: The evidence does not identify the bank.'
    assert json.loads(agent['sources'])[0]['cited'] is False
    assert json.loads(agent['grounding'])['compliance']=='after_repair'


@pytest.mark.asyncio
async def test_hedged_opening_keeps_supported_partial_answer(monkeypatch):
    from backend.app import registry
    answer='The evidence is insufficient for every detail, but the filing deadline is 30 days [S1].'
    async def model(*args):return {'text':json.dumps({'answer':answer,'citations':['S1'],'abstain':False,'reason':''}),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    result=await compile_workflow(graph(),message='What is known?',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    assert result['values']['agent']['text']==answer
    assert json.loads(result['values']['agent']['grounding'])['compliance']=='first_attempt'


@pytest.mark.asyncio
async def test_invalid_grounded_contract_twice_fails_node(monkeypatch):
    from backend.app import registry
    replies=iter(['not json','still not json'])
    async def model(*args):return {'text':next(replies),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    with pytest.raises(ValueError,match='grounded answer contract'):
        await compile_workflow(graph(),message='What?',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})


@pytest.mark.asyncio
async def test_answer_without_citations_repairs_then_fails(monkeypatch):
    from backend.app import registry
    invalid=json.dumps({'answer':'Bluebird','citations':[],'abstain':False,'reason':''})
    async def model(*args):return {'text':invalid,'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    with pytest.raises(ValueError,match='grounded answer contract'):
        await compile_workflow(graph(),message='What?',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})


def test_grouped_citations_are_canonicalized_and_each_label_validated():
    passages=[dict(source(1),citation='S1'),dict(source(2),citation='S2')]
    text,sources=ground_answer('A [S1, S2]. B [S1,S1]. C [S2, S9]. D [S8, S9]. Keep [section S1].',passages)
    assert text=='A [S1][S2]. B [S1]. C [S2][unsupported reference]. D [unsupported reference]. Keep [section S1].'
    assert [(s['citation'],s['cited']) for s in sources]==[('S1',True),('S2',True)]


@pytest.mark.asyncio
async def test_response_rewrites_citation_when_run_has_no_evidence():
    result=await response_node({'text':'Invented [S1].'},None,SimpleNamespace(run={'evidence':[]}))
    assert result['text']=='Invented [unsupported reference].'
    assert result['sources']=='[]'


def test_run_evidence_registry_preserves_120_passages_for_widened_frontend_domain():
    run={'evidence':[]}
    remember_evidence(run,[dict(source(i),citation=f'S{i}') for i in range(1,121)])
    assert len(run['evidence'])==120
    assert run['evidence'][-1]['citation']=='S120'

@pytest.mark.parametrize('raw,expected',[
    ('[S1]','[S1]'),('[S1], [S2]','[S1], [S2]'),('[S1, S2]','[S1][S2]'),
    ('[S1,S2]','[S1][S2]'),('[S1,S1]','[S1]'),('[S9, S10]','[unsupported reference]'),
    ('[S1,S9]','[S1][unsupported reference]'),('[section S1]','[section S1]'),
])
def test_citation_grammar_variants(raw,expected):
    assert ground_answer(raw,[{'citation':'S1'},{'citation':'S2'}])[0]==expected

@pytest.mark.asyncio
@pytest.mark.parametrize('node_type',['agent','query'])
async def test_shared_contract_rejects_unknown_declared_citation_then_repairs(monkeypatch,node_type):
    workflow=graph()
    if node_type=='query':
        workflow.nodes=[n for n in workflow.nodes if n.id!='agent']
        workflow.nodes[1].type='query'
        workflow.nodes[-1].inputs={'text':'retrieve.text'}
        workflow.edges=[e for e in workflow.edges if e.id!='c']
        workflow.edges[-1].target='out'
    replies=iter([
        json.dumps({'answer':'Fact [S9]','citations':['S9'],'abstain':False,'reason':''}),
        json.dumps({'answer':'Fact 1','citations':['S1'],'abstain':False,'reason':''}),
    ])
    calls=[]
    async def model(inputs,config,ctx):
        calls.append(inputs['prompt'])
        return {'text':next(replies),'provider':'demo'}
    monkeypatch.setattr('backend.app.registry.llm_node',model)
    result=await compile_workflow(workflow,message='What?',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    assert result['values']['out']['text']=='Fact 1 [S1]'
    key='agent' if node_type=='agent' else 'retrieve'
    assert json.loads(result['values'][key]['grounding'])['compliance']=='after_repair'
    assert len(calls)==2 and 'Fact 1' in calls[1]

@pytest.mark.asyncio
async def test_query_malformed_json_twice_fails(monkeypatch):
    from backend.app.platform_nodes import query_node
    from backend.app.registry import QueryConfig
    calls=[]
    async def model(*args):
        calls.append(1)
        return {'text':'not json','provider':'demo'}
    monkeypatch.setattr('backend.app.registry.llm_node',model)
    ctx=SimpleNamespace(platform=platform_with([source(1)],[]),checkpoint_owner=None,node_id='query',run={'evidence':[]})
    with pytest.raises(ValueError,match='grounded answer contract'):
        await query_node({'query':'Question'},QueryConfig(knowledge_base_id='kb1'),ctx)
    assert len(calls)==2

@pytest.mark.asyncio
async def test_retrieve_to_response_preserves_all_120_labels():
    workflow=graph()
    workflow.nodes=[n for n in workflow.nodes if n.id!='agent']
    workflow.nodes[-1].inputs={'text':'input.message'}
    workflow.edges=[e for e in workflow.edges if e.id!='c']
    workflow.edges[-1].target='out'
    result=await compile_workflow(workflow,message='Sources [S1, S100, S120, S121]',platform_resolver=platform_with([source(i) for i in range(1,121)],[])).graph.ainvoke({'values':{}})
    assert result['values']['out']['text']=='Sources [S1][S100][S120][unsupported reference]'
    assert len(json.loads(result['values']['out']['sources']))==120


@pytest.mark.asyncio
async def test_empty_grounded_envelope_with_attached_tool_cannot_emit_factual_claim(monkeypatch):
    from backend.app import registry
    replies=iter([
        '{"action":"final","text":"The policy grants 12 weeks [S1]."}',
        json.dumps({'answer':'','citations':[],'abstain':True,'reason':'No evidence passages were retrieved.'}),
    ])
    async def model(*args):return {'text':next(replies),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    workflow=Workflow.model_validate({'version':1,'name':'Empty grounded tool flow','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'retrieve','type':'retrieve','config':{'knowledge_base_id':'kb1','mode':'keyword'},'inputs':{'query':'input.message'}},
        {'id':'agent','type':'agent','config':{'provider':'demo'},'inputs':{'input':'retrieve.context'}},
        {'id':'calc','type':'tool_python','config':{'code':'print(input_text)'}},
        {'id':'out','type':'response','inputs':{'text':'agent.text'}}],
        'edges':[{'id':'a','source':'input','target':'retrieve'},{'id':'b','source':'retrieve','target':'agent'},
                 {'id':'c','source':'agent','target':'out'},{'id':'t','source':'calc','target':'agent','kind':'tool','targetHandle':'tools'}]})
    result=await compile_workflow(workflow,message='Parental leave?',platform_resolver=platform_with([],[])).graph.ainvoke({'values':{}})
    assert result['values']['agent']['text']==NO_EVIDENCE+'\n\nReason: No evidence passages were retrieved.'
    assert '[S1]' not in result['values']['out']['text']
