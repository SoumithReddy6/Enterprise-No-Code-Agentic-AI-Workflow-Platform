import json
import pytest
from backend.app.models import Workflow
from backend.app.compiler import compile_workflow
from backend.tests.test_agent_tools import scripted
from backend.tests.test_agent_grounding import source,platform_with

def team_flow(specialists=3):
    """A supervisor delegating to N specialists, each with its own retrieval tool."""
    nodes=[{'id':'input','type':'chat_input'},
           {'id':'agent','type':'agent','inputs':{'input':'input.message'},'config':{'provider':'demo','role':'router'}},
           {'id':'out','type':'response','inputs':{'text':'agent.text'}}]
    edges=[{'id':'a','source':'input','target':'agent'},{'id':'b','source':'agent','target':'out'}]
    for i in range(specialists):
        name=f'spec{i}';tool=f'kb{i}'
        nodes.append({'id':name,'type':'agent','config':{'provider':'demo','role':'reasoner'}})
        nodes.append({'id':tool,'type':'retrieve','config':{'knowledge_base_id':'kb1','mode':'keyword'}})
        edges.append({'id':'d'+name,'source':'agent','target':name,'kind':'agent','sourceHandle':'agents','targetHandle':'input'})
        edges.append({'id':'t'+tool,'source':tool,'target':name,'kind':'tool','targetHandle':'tools'})
    return Workflow.model_validate({'version':1,'name':'Team','nodes':nodes,'edges':edges})

def grounded(text,cites=('S1',)):
    return json.dumps({'answer':text,'citations':list(cites),'abstain':False,'reason':''})

@pytest.mark.asyncio
async def test_supervisor_with_three_specialists_completes_without_truncation(monkeypatch):
    replies=[]
    for i in range(3):
        replies.append(json.dumps({'action':'call','target':f'spec{i}','input':'look it up'}))
        replies.append(json.dumps({'action':'call','target':f'kb{i}','input':'query'}))
        replies.append(grounded(f'Finding {i} [S{i+1}]',[f'S{i+1}']))
    replies.append(grounded('Combined [S1][S2][S3]',['S1','S2','S3']))
    scripted(monkeypatch,replies)
    events=[]
    async def emit(event):events.append(event)
    result=await compile_workflow(team_flow(),message='facts',platform_resolver=platform_with([source(1)],[]),emit=emit).graph.ainvoke({'values':{}})
    metadata=json.loads(result['values']['agent']['grounding'])
    print('TRUNCATED:',metadata.get('truncated'),'REASON:',metadata.get('truncation_reason'))
    assert not metadata.get('truncated'),f"team ran out of budget: {metadata.get('truncation_reason')}"


@pytest.mark.asyncio
async def test_run_ceiling_still_stops_a_runaway_team(monkeypatch):
    """The ceiling is a backstop, not a per-agent allowance: a greedy team still gets cut off."""
    monkeypatch.setenv('AGENT_RUN_BUDGET','1')
    abstain=json.dumps({'answer':'','citations':[],'abstain':True,'reason':'Budget exhausted before the team finished.'})
    scripted(monkeypatch,[json.dumps({'action':'call','target':'spec0','input':'x'}),abstain,abstain])
    events=[]
    async def emit(event):events.append(event)
    result=await compile_workflow(team_flow(1),message='facts',platform_resolver=platform_with([source(1)],[]),emit=emit).graph.ainvoke({'values':{}})
    metadata=json.loads(result['values']['agent']['grounding'])
    assert metadata['truncated'] is True
    assert 'Run-wide' in metadata['truncation_reason'] and 'AGENT_RUN_BUDGET' in metadata['truncation_reason']
    warning=next(e for e in events if e.get('kind')=='agent_budget')
    assert warning['run_budget']==1 and warning['actions_used']==1


@pytest.mark.asyncio
async def test_per_agent_steps_still_bind_with_plenty_of_run_budget(monkeypatch):
    """A single agent cannot consume the run: its own max_steps stops it first."""
    monkeypatch.setenv('AGENT_RUN_BUDGET','200')
    workflow=team_flow(1);workflow.nodes[1].config['max_steps']=1
    scripted(monkeypatch,[json.dumps({'action':'call','target':'spec0','input':'x'}),
                          json.dumps({'action':'call','target':'kb0','input':'q'}),
                          grounded('Finding [S1]',['S1']),
                          grounded('Combined [S1]',['S1'])])
    result=await compile_workflow(workflow,message='facts',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    metadata=json.loads(result['values']['agent']['grounding'])
    assert metadata['truncated'] is True
    assert 'step budget' in metadata['truncation_reason'] and 'Run-wide' not in metadata['truncation_reason']


@pytest.mark.asyncio
async def test_invocation_ids_stay_unique_and_monotonic(monkeypatch):
    """budget['counter'] is the approval idempotency key. Raising the ceiling must not disturb it."""
    replies=[]
    for i in range(3):
        replies.append(json.dumps({'action':'call','target':f'spec{i}','input':'x'}))
        replies.append(json.dumps({'action':'call','target':f'kb{i}','input':'q'}))
        replies.append(grounded(f'Finding {i} [S{i+1}]',[f'S{i+1}']))
    replies.append(grounded('Combined [S1][S2][S3]',['S1','S2','S3']))
    scripted(monkeypatch,replies)
    ids=[]
    async def emit(event):
        if event.get('invocation_id'):ids.append(event['invocation_id'])
    await compile_workflow(team_flow(),message='facts',platform_resolver=platform_with([source(1)],[]),emit=emit).graph.ainvoke({'values':{}})
    unique=list(dict.fromkeys(ids))
    assert len(unique)==6, f'expected six invocations, got {unique}'
    counters=[int(i.split(':')[1]) for i in unique]
    assert counters==sorted(counters) and len(set(counters))==len(counters)
    assert counters[0]==1 and counters[-1]==6


@pytest.mark.asyncio
async def test_resume_from_a_frame_written_before_the_ceiling_existed(monkeypatch):
    """Frames persisted by older runs carry no 'ceiling'; the counter must still continue."""
    from backend.app.agent_runtime import execute_agent,DEFAULT_RUN_BUDGET
    from backend.app.registry import Context
    from backend.tests.test_platform_graph import platform_flow
    scripted(monkeypatch,[grounded('Fact 1 [S1]',['S1'])])
    events=[]
    async def emit(event):events.append(event)
    run={'evidence':[{**source(1),'citation':'S1'}],'citation_counter':1,'agent_frames':{}}
    legacy={'remaining':6,'counter':4}
    output=await execute_agent('agent',json.dumps({'question':'facts','passages':[{**source(1),'citation':'S1'}]}),
                               platform_flow(),Context('',lambda _:'',run=run),emit,budget=legacy)
    assert output['text']=='Fact 1 [S1]'
    assert legacy['counter']==4 and legacy['remaining']==6
    assert DEFAULT_RUN_BUDGET==40


def test_run_budget_setting_is_validated(monkeypatch):
    from backend.app.agent_runtime import run_budget,DEFAULT_RUN_BUDGET
    monkeypatch.delenv('AGENT_RUN_BUDGET',raising=False)
    assert run_budget()==DEFAULT_RUN_BUDGET
    monkeypatch.setenv('AGENT_RUN_BUDGET','12');assert run_budget()==12
    for bad in ('0','-1','201','abc','1.5'):
        monkeypatch.setenv('AGENT_RUN_BUDGET',bad)
        with pytest.raises(ValueError) as exc:run_budget()
        assert 'AGENT_RUN_BUDGET' in str(exc.value)
