import json
from pathlib import Path
import pytest
from backend.app import answerability_guard as guard
from backend.app.compiler import compile_workflow
from backend.tests.test_kb_workflow import graph
from backend.tests.test_agent_grounding import source,platform_with

@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,decision,expected_calls',[(False,'abstain',1),(True,'abstain',0),(True,'allow',1)])
async def test_product_guard_controls_generation(monkeypatch,enabled,decision,expected_calls):
    flow=graph();flow.nodes[1].config['answerability_guard']=enabled
    calls=[];scores=[]
    async def check(*args):scores.append(args);return {'status':'scored','decision':decision,'abstention_source':'answerability_guard' if decision=='abstain' else None}
    async def model(*args):calls.append(1);return {'text':json.dumps({'answer':'Fact 1 [S1]','citations':['S1'],'abstain':False,'reason':''}),'provider':'demo'}
    monkeypatch.setattr(guard,'check',check);monkeypatch.setattr('backend.app.registry.llm_node',model)
    result=await compile_workflow(flow,message='What?',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    assert len(calls)==expected_calls and len(scores)==int(enabled)
    if enabled and decision=='abstain':
        output=result['values']['agent'];assert output['sources']=='[]'
        assert json.loads(output['grounding'])['abstention_source']=='answerability_guard'
        assert json.loads(result['values']['retrieve']['context'])['passages']==[]

@pytest.mark.asyncio
async def test_missing_weights_skip_and_warn_once(monkeypatch,tmp_path,caplog):
    monkeypatch.setenv('ANSWERABILITY_MODEL_DIR',str(tmp_path/'missing'))
    monkeypatch.setattr(guard,'_warned',set());monkeypatch.setattr(guard,'_cached',None)
    caplog.set_level('INFO',logger='relay.journal')
    flow=graph();flow.nodes[1].config['answerability_guard']=True
    calls=[]
    async def model(*args):calls.append(1);return {'text':json.dumps({'answer':'Fact 1 [S1]','citations':['S1'],'abstain':False,'reason':''}),'provider':'demo'}
    monkeypatch.setattr('backend.app.registry.llm_node',model)
    for _ in range(2):
        result=await compile_workflow(flow,message='PRIVATE QUESTION',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
        assert json.loads(result['values']['retrieve']['context'])['answerability']['reason']=='weights_missing'
    assert len(calls)==2
    events=[json.loads(r.message) for r in caplog.records if r.name=='relay.journal']
    assert sum(e['event']=='answerability.unavailable' for e in events)==1
    assert 'PRIVATE QUESTION' not in caplog.text and 'Fact 1' not in caplog.text

@pytest.mark.asyncio
async def test_checksum_mismatch_never_constructs_inference_session(monkeypatch,tmp_path):
    from backend.app.kb.answerability import AnswerabilityReader,MODEL,REVISION,FILES,SHA256
    import onnxruntime
    for name in FILES:(tmp_path/name).write_bytes(b'CORRUPT')
    (tmp_path/'manifest.json').write_text(json.dumps({'model':MODEL,'revision':REVISION,'files':FILES,'sha256':SHA256}))
    def forbidden(*a,**kw):raise AssertionError('Unverified model must never load')
    monkeypatch.setattr(onnxruntime,'InferenceSession',forbidden)
    with pytest.raises(ValueError,match='Checksum mismatch'):AnswerabilityReader(tmp_path)
    monkeypatch.setenv('ANSWERABILITY_MODEL_DIR',str(tmp_path));monkeypatch.setattr(guard,'_cached',None)
    result=await guard.check('Question',[source(1)])
    assert result['decision']=='skip' and result['reason']=='integrity_error'

@pytest.mark.asyncio
async def test_query_node_guard_abstains_without_model(monkeypatch):
    from backend.app.registry import QueryConfig,Context
    from backend.app.platform_nodes import query_node
    async def check(*args):return {'status':'scored','decision':'abstain'}
    async def forbidden(*args):raise AssertionError('Generation forbidden')
    monkeypatch.setattr(guard,'check',check);monkeypatch.setattr('backend.app.registry.llm_node',forbidden)
    output=await query_node({'query':'What?'},QueryConfig(knowledge_base_id='kb1',answerability_guard=True),Context(message='',resolve_credential=lambda _: '',platform=platform_with([source(1)],[])))
    assert json.loads(output['grounding'])['abstention_source']=='answerability_guard'


def test_guard_events_and_sql_metrics_do_not_double_count_cached_replay(tmp_path):
    from backend.app.storage import Store
    from backend.app.operator_metrics import metrics
    from cryptography.fernet import Fernet
    store=Store(f'sqlite:///{tmp_path}/guard.db',Fernet.generate_key())
    run=store.create_run(graph().model_dump(),'question');store.claim_next('worker')
    decision={'enabled':True,'status':'scored','decision':'abstain','abstention_source':'answerability_guard'}
    event={'kind':'answerability','answerability':decision,'transient':True,'node_id':'retrieve','status':'success','outputs':{'context':json.dumps({'answerability':decision}),'sources':'[]','query':'question'}}
    store.worker_event(run['id'],'worker',event)
    store.worker_event(run['id'],'worker',{**event,'cached':True})
    report=metrics(store)['answerability_guard']
    assert report['decisions']=={'abstain':1}
    assert store.run(run['id'])['events'][1]['abstention_source']=='answerability_guard'
    store.engine.dispose()

@pytest.mark.asyncio
async def test_agent_retrieval_tool_rejection_stops_followup_generation(monkeypatch):
    from backend.tests.test_agent_tools import calc_flow
    flow=calc_flow();flow.nodes[2].type='retrieve';flow.nodes[2].config={'knowledge_base_id':'kb1','answerability_guard':True}
    calls=[]
    async def model(*args):
        calls.append(1)
        assert len(calls)==1,'No answer-generation call after rejected retrieval'
        return {'text':'{"action":"call","target":"calc","input":"What?"}','provider':'demo'}
    async def check(*args):return {'status':'scored','decision':'abstain'}
    monkeypatch.setattr(guard,'check',check);monkeypatch.setattr('backend.app.registry.llm_node',model)
    result=await compile_workflow(flow,message='What?',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    assert len(calls)==1 and json.loads(result['values']['agent']['grounding'])['abstention_source']=='answerability_guard'

@pytest.mark.asyncio
async def test_reader_inference_error_skips_instead_of_false_abstention(monkeypatch,tmp_path):
    for name in ('manifest.json',*guard.FILES):(tmp_path/name).write_text('fixture')
    class Reader:
        def __init__(self,*args):pass
        def score(self,*args):return {'status':'error','answerable':False}
    monkeypatch.setenv('ANSWERABILITY_MODEL_DIR',str(tmp_path));monkeypatch.setattr(guard,'AnswerabilityReader',Reader);monkeypatch.setattr(guard,'_cached',None)
    decision=await guard.check('What?',[source(1)])
    assert decision['decision']=='skip' and decision['reason']=='reader_error'

@pytest.mark.asyncio
@pytest.mark.parametrize('metadata',[None,[], 'untrusted'])
async def test_malformed_optional_metadata_does_not_crash_agent(metadata):
    from backend.tests.test_agent_tools import calc_flow
    flow=calc_flow();flow.nodes=[n for n in flow.nodes if n.id!='calc'];flow.edges=[e for e in flow.edges if e.kind!='tool']
    result=await compile_workflow(flow,message=json.dumps({'passages':[],'answerability':metadata})).graph.ainvoke({'values':{}})
    assert json.loads(result['values']['agent']['grounding'])['abstain']

@pytest.mark.asyncio
@pytest.mark.parametrize('node_type',['llm','agent','query'])
async def test_legacy_prompt_llm_cannot_bypass_guard(monkeypatch,node_type):
    from backend.app.models import Workflow
    raw=graph().model_dump()
    raw['nodes'][1]['config']['answerability_guard']=True
    raw['nodes'][2].update(type=node_type,inputs={('prompt' if node_type=='llm' else 'query' if node_type=='query' else 'input'):'template.text'},config={'provider':'demo',**({'knowledge_base_id':'kb1'} if node_type=='query' else {})})
    raw['nodes'].append({'id':'template','type':'prompt','inputs':{'message':'retrieve.context'},'config':{'template':'Please answer: {message}'}})
    raw['edges']=[{'id':'a','source':'input','target':'retrieve'},{'id':'b','source':'retrieve','target':'template'},{'id':'c','source':'template','target':'agent'},{'id':'d','source':'agent','target':'out'}]
    async def check(*args):return {'decision':'abstain','status':'scored'}
    async def forbidden(*args):raise AssertionError('Legacy LLM must not generate')
    monkeypatch.setattr(guard,'check',check);monkeypatch.setattr('backend.app.registry.llm_node',forbidden)
    result=await compile_workflow(Workflow.model_validate(raw),message='What?',platform_resolver=platform_with([source(1)],[])).graph.ainvoke({'values':{}})
    assert result['values']['agent']['provider']=='none'

@pytest.mark.asyncio
async def test_query_records_decision_before_generation_failure(monkeypatch):
    from backend.app.registry import QueryConfig,Context
    from backend.app.platform_nodes import query_node
    events=[]
    async def emit(event):events.append(event)
    async def check(*args):return {'status':'scored','decision':'allow'}
    async def fail(*args):raise ValueError('Model unavailable')
    monkeypatch.setattr(guard,'check',check);monkeypatch.setattr('backend.app.registry.llm_node',fail)
    with pytest.raises(ValueError,match='Model unavailable'):
        await query_node({'query':'What?'},QueryConfig(knowledge_base_id='kb1',answerability_guard=True),Context(message='',resolve_credential=lambda _: '',platform=platform_with([source(1)],[]),emit=emit))
    assert len(events)==1 and events[0]['kind']=='answerability' and events[0]['answerability']['decision']=='allow'
