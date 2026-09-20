import json
import pytest
from backend.app.compiler import compile_workflow
from backend.app.agent_runtime import execute_agent
from backend.app.registry import Context
from backend.tests.test_agent_tools import calc_flow,scripted
from backend.tests.test_agent_grounding import source,platform_with
from backend.tests.test_platform_graph import platform_flow


def retrieval_flow():
    workflow=calc_flow();workflow.nodes[1].config['max_steps']=1
    workflow.nodes[2].type='retrieve';workflow.nodes[2].config={'knowledge_base_id':'kb1','mode':'keyword'}
    return workflow

@pytest.mark.asyncio
async def test_budget_with_evidence_returns_grounded_partial_and_warning(monkeypatch):
    seen=[];scripted(monkeypatch,['{"action":"call","target":"calc","input":"facts"}',json.dumps({'answer':'Fact 1 [S1]','citations':['S1'],'abstain':False,'reason':''})],seen)
    events=[]
    async def emit(event):events.append(event)
    result=await compile_workflow(retrieval_flow(),message='facts',platform_resolver=platform_with([source(1)],[]),emit=emit).graph.ainvoke({'values':{}})
    output=result['values']['agent'];metadata=json.loads(output['grounding'])
    assert metadata['truncated'] is True and metadata['truncation_reason']
    assert result['values']['out']['text']=='Fact 1 [S1]'
    assert json.loads(result['values']['out']['sources'])[0]['cited']
    assert len(seen)==2
    assert any(e.get('truncated') and e.get('kind')=='agent_budget' for e in events)

@pytest.mark.asyncio
async def test_budget_without_evidence_abstains_without_extra_model_call(monkeypatch):
    seen=[];scripted(monkeypatch,['{"action":"call","target":"calc","input":"facts"}'],seen)
    result=await compile_workflow(retrieval_flow(),message='facts',platform_resolver=platform_with([],[])).graph.ainvoke({'values':{}})
    metadata=json.loads(result['values']['agent']['grounding'])
    assert metadata['abstain'] and metadata['truncated']
    assert result['values']['out']['sources']=='[]' and len(seen)==1

@pytest.mark.asyncio
async def test_empty_nested_exhaustion_is_visible_and_parent_keeps_evidence(monkeypatch):
    scripted(monkeypatch,['{"action":"call","target":"helper","input":"Review"}',json.dumps({'answer':'Fact 1 [S1]','citations':['S1'],'abstain':False,'reason':''})])
    events=[]
    async def emit(event):events.append(event)
    passage={**source(1),'citation':'S1'}
    output=await execute_agent('agent',json.dumps({'question':'facts','passages':[passage]}),platform_flow(),Context('',lambda _:''),emit,budget={'remaining':1,'counter':0})
    assert json.loads(output['grounding'])['truncated']
    assert output['text']=='Fact 1 [S1]'
    assert any(e.get('node_id')=='helper' and e['status']=='failed' and 'budget' in e['error'].lower() for e in events)

@pytest.mark.asyncio
async def test_worker_persists_truncated_flag_on_run(tmp_path,monkeypatch):
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from cryptography.fernet import Fernet
    workflow=calc_flow();workflow.nodes[1].config['max_steps']=1
    scripted(monkeypatch,['{"action":"call","target":"calc","input":"facts"}'])
    # Failed non-writing tool consumes the budget and produces safe abstention.
    async def broken(*args):raise ValueError('unavailable')
    store=Store(f'sqlite:///{tmp_path}/partial.db',Fernet.generate_key());worker=Worker(store)
    monkeypatch.setattr(worker.tools,'execute',broken)
    run=store.create_run(workflow.model_dump(),'facts')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id'])
    assert saved['status']=='success'
    assert saved['truncated'] is True and saved['truncation_reason']
