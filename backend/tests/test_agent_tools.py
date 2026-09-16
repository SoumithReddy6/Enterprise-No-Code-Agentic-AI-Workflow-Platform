"""Agent tool loop: contracts the model sees, errors as observations, rendered evidence, run-wide citations."""
import json
import pytest
from backend.app.models import Workflow
from backend.app.compiler import compile_workflow
from backend.tests.test_agent_grounding import source, platform_with

def calc_flow():
    return Workflow.model_validate({'version':1,'name':'Calc','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'agent','type':'agent','inputs':{'input':'input.message'},'config':{'provider':'demo'}},
        {'id':'calc','type':'tool_python','config':{'code':'print(eval(input_text))','description':'Evaluates one Python arithmetic expression.'}},
        {'id':'out','type':'response','inputs':{'text':'agent.text'}}],
        'edges':[{'id':'a','source':'input','target':'agent'},{'id':'b','source':'agent','target':'out'},{'id':'t','source':'calc','target':'agent','kind':'tool','targetHandle':'tools'}]})

def scripted(monkeypatch,responses,seen=None):
    from backend.app import registry
    replies=iter(responses)
    async def model(inputs,config,ctx):
        if seen is not None:seen.append(inputs['prompt'])
        return {'text':next(replies),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)

@pytest.mark.asyncio
async def test_tool_error_is_an_observation_not_a_run_failure(monkeypatch):
    seen=[];scripted(monkeypatch,['{"action":"call","target":"calc","input":"15% of 240"}','{"action":"call","target":"calc","input":"240*0.15+17"}','{"action":"final","text":"53.0"}'],seen)
    calls=[]
    async def platform(action,*args):
        if action=='tool':
            calls.append(args[2])
            if len(calls)==1:raise ValueError('Python container failed: SyntaxError')
            return '53.0'
        raise AssertionError(action)
    events=[]
    async def emit(e):events.append(e)
    result=await compile_workflow(calc_flow(),platform_resolver=platform,emit=emit,message='Compute').graph.ainvoke({'values':{}})
    assert result['values']['out']['text']=='53.0' and calls==['15% of 240','240*0.15+17']
    assert any(e.get('node_id')=='calc' and e['status']=='failed' and e.get('transient') for e in events)
    # The second model call saw the failure as an observation, with the tool's own description.
    assert 'tool_error' in seen[1] and 'SyntaxError' in seen[1]
    assert 'Evaluates one Python arithmetic expression' in seen[0] and '"input": "text the configured code receives as input_text"' in seen[0]

@pytest.mark.asyncio
async def test_exhausted_budget_after_repeated_tool_errors_fails_clearly(monkeypatch):
    scripted(monkeypatch,['{"action":"call","target":"calc","input":"x"}']*8)
    async def platform(action,*args):raise ValueError('always broken')
    with pytest.raises(ValueError,match='limit'):
        await compile_workflow(calc_flow(),platform_resolver=platform,message='Compute').graph.ainvoke({'values':{}})

@pytest.mark.asyncio
async def test_retrieve_as_tool_is_rendered_and_citations_validated(monkeypatch):
    seen=[];scripted(monkeypatch,['{"action":"call","target":"search","input":"codename"}','{"action":"final","text":"Bluebird [S1]. Also [S9]."}'],seen)
    workflow=Workflow.model_validate({'version':1,'name':'Search','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'agent','type':'agent','inputs':{'input':'input.message'},'config':{'provider':'demo'}},
        {'id':'search','type':'retrieve','config':{'knowledge_base_id':'kb1','mode':'keyword'}},
        {'id':'out','type':'response','inputs':{'text':'agent.text'}}],
        'edges':[{'id':'a','source':'input','target':'agent'},{'id':'b','source':'agent','target':'out'},{'id':'t','source':'search','target':'agent','kind':'tool','targetHandle':'tools'}]})
    verified=[]
    result=await compile_workflow(workflow,platform_resolver=platform_with([source(1),source(2)],verified),message='Codename?').graph.ainvoke({'values':{}})
    # The model saw numbered passages, not a JSON envelope.
    assert 'Evidence passages:' in seen[1] and '[S1] facts.txt (page 1): Fact 1' in seen[1] and '"context"' not in seen[1]
    agent=result['values']['agent']
    assert agent['text']=='Bluebird [S1]. Also [unsupported reference].'
    assert [(s['citation'],s['cited']) for s in json.loads(agent['sources'])]==[('S1',True),('S2',False)]
    assert result['values']['out']['text']==agent['text']

@pytest.mark.asyncio
async def test_citation_labels_are_unique_across_the_run_and_validated_at_response(monkeypatch):
    # Retrieve twice, then a plain-text critic cites one real and one invented label.
    scripted(monkeypatch,['Summary uses [S1].','Critique: fine [S1], but [S3] is weak and [S7] is missing.'])
    workflow=Workflow.model_validate({'version':1,'name':'Chain','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'r1','type':'retrieve','config':{'knowledge_base_id':'kb1','mode':'keyword'},'inputs':{'query':'input.message'}},
        {'id':'r2','type':'retrieve','config':{'knowledge_base_id':'kb1','mode':'keyword'},'inputs':{'query':'r1.query'}},
        {'id':'summ','type':'agent','inputs':{'input':'r2.context'},'config':{'provider':'demo','role':'summarizer'}},
        {'id':'critic','type':'agent','inputs':{'input':'summ.text'},'config':{'provider':'demo','role':'critic'}},
        {'id':'out','type':'response','inputs':{'text':'critic.text'}}],
        'edges':[{'id':'a','source':'input','target':'r1'},{'id':'b','source':'r1','target':'r2'},{'id':'c','source':'r2','target':'summ'},{'id':'d','source':'summ','target':'critic'},{'id':'e','source':'critic','target':'out'}]})
    result=await compile_workflow(workflow,platform_resolver=platform_with([source(1),source(2)],[]),message='Q').graph.ainvoke({'values':{}})
    first=[p['citation'] for p in json.loads(result['values']['r1']['context'])['passages']]
    second=[p['citation'] for p in json.loads(result['values']['r2']['context'])['passages']]
    assert first==['S1','S2'] and second==['S3','S4']
    # The critic never saw an envelope, yet the Response boundary validates its labels against the run's evidence.
    assert result['values']['critic']['sources']=='[]'
    assert result['values']['out']['text']=='Critique: fine [S1], but [S3] is weak and [unsupported reference] is missing.'
    cited=[s['citation'] for s in json.loads(result['values']['out']['sources']) if s['cited']]
    assert cited==['S1','S3']
