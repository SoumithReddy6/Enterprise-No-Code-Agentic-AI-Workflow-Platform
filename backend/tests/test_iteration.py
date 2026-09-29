"""A4: run one attached callable per element, with per-item isolation and resume."""
import asyncio
import json
import pytest
from backend.app.models import Workflow
from backend.app.compiler import compile_workflow, validate_workflow
from backend.app.tool_service import TransientToolError, UncertainWriteError


def jira_connection(store):
    """The worker validates tool connections before execution, so a real record is needed."""
    from backend.app.tool_service import ToolService
    return ToolService(store).save_connection(
        {'name':'Jira','provider':'jira','endpoint':'https://jira.example.com',
         'username':'bot','secret':'token'},'local')['id']


def loop_flow(max_items=100, on_item_error='continue', connection_id='c1', body_retry=None):
    """jira search -> for each issue -> python tool -> response."""
    return Workflow.model_validate({'version':1,'name':'Batch','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'tickets','type':'tool_jira','inputs':{'input':'input.message'},
         'config':{'connection_id':connection_id,'operation':'search','jql':'assignee=me'}},
        {'id':'each','type':'for_each','inputs':{'items':'tickets.items'},
         'config':{'body':'work','max_items':max_items,'on_item_error':on_item_error}},
        {'id':'work','type':'tool_python','config':{'code':'print(input_text)','description':'Handles one item.'},
         **({'retry':body_retry} if body_retry is not None else {})},
        {'id':'out','type':'response','inputs':{'text':'input.message'}}],
        'edges':[{'id':'a','source':'input','target':'tickets'},
                 {'id':'b','source':'tickets','target':'each'},
                 {'id':'c','source':'each','target':'out'},
                 {'id':'d','source':'each','target':'work','kind':'loop','sourceHandle':'body','targetHandle':'input'}]})


def platform(items, behaviour=None):
    """Jira returns `items`; the body applies `behaviour(index, text)` or echoes."""
    calls=[]
    async def resolver(action,*args):
        if action!='tool':raise AssertionError(action)
        node_type,settings,text=args[0],args[1],args[2]
        if node_type=='tool_jira':
            return {'text':json.dumps({'issues':[]}),'items':items,'truncated':False,'warnings':[]}
        calls.append(text)
        if behaviour:return behaviour(len(calls)-1,text)
        return 'done:'+text
    return resolver,calls


async def run(workflow,resolver,events=None,run_state=None):
    sink=events if events is not None else []
    async def emit(event):sink.append(dict(event))
    compiled=compile_workflow(workflow,message='go',platform_resolver=resolver,emit=emit)
    return await compiled.graph.ainvoke({'values':{}})


def issues(n):
    return [{'key':f'PROJ-{i}','summary':f'Issue {i}','status':'Open','assignee':'Ana',
             'updated':'2026-09-01','url':f'https://j/browse/PROJ-{i}'} for i in range(n)]


# --------------------------------------------------------------------------- correctness

@pytest.mark.asyncio
async def test_runs_the_body_once_per_item_in_order():
    resolver,calls=platform(issues(5))
    result=await run(loop_flow(),resolver)
    results=result['values']['each']['results']
    assert len(calls)==5
    assert [r['index'] for r in results]==[0,1,2,3,4]
    assert all(r['status']=='success' for r in results)
    assert json.loads(calls[2])['key']=='PROJ-2'


@pytest.mark.asyncio
async def test_empty_list_runs_nothing_and_is_not_an_error():
    resolver,calls=platform([])
    result=await run(loop_flow(),resolver)
    assert calls==[] and result['values']['each']['results']==[]
    assert result['values']['each']['failed']=='0'


@pytest.mark.asyncio
async def test_one_failing_item_does_not_discard_the_others():
    def flaky(index,text):
        if index==2:raise TransientToolError('provider unreachable')
        return 'done'
    resolver,calls=platform(issues(6),flaky)
    result=await run(loop_flow(),resolver)
    results=result['values']['each']['results']
    assert len(calls)==6, 'every item must still be attempted'
    assert [r['status'] for r in results]==['success','success','failed','success','success','success']
    assert results[2]['error']=='provider unreachable'
    assert result['values']['each']['failed']=='1'


@pytest.mark.asyncio
async def test_transient_loop_body_uses_the_same_retry_policy_as_a_graph_node(monkeypatch):
    from backend.app import execution_policy
    attempts=0;events=[]
    def flaky(_index,_text):
        nonlocal attempts
        attempts+=1
        if attempts==1:raise TransientToolError('provider unavailable')
        return 'done'
    async def no_sleep(_):pass
    monkeypatch.setattr(execution_policy.asyncio,'sleep',no_sleep)
    resolver,_=platform(issues(1),flaky)
    result=await run(loop_flow(body_retry={'attempts':2,'base_delay':0}),resolver,events)
    assert result['values']['each']['results'][0]['status']=='success'
    assert attempts==2
    retries=[event for event in events if event.get('status')=='retrying']
    assert len(retries)==1 and retries[0]['invocation_id']=='each:0:work'
    outcomes=[event for event in events if event.get('invocation_id')=='each:0:work'
              and event.get('status') in ('success','failed','paused')]
    assert len(outcomes)==1, 'the caller owns exactly one outcome event'


@pytest.mark.asyncio
async def test_loop_constructs_one_typed_identity_for_dispatch_and_events():
    from backend.app.execution_policy import ExecutionIdentity

    dispatched=[];events=[]
    async def resolver(action,*args):
        assert action=='tool'
        node_type,settings,text=args[:3]
        if node_type=='tool_jira':
            return {'text':'{}','items':issues(1),'truncated':False,'warnings':[]}
        dispatched.append(args[3])
        return 'done'

    await run(loop_flow(),resolver,events)
    assert dispatched==[ExecutionIdentity('work','each:0','each:0:work','each',0)]
    outcome=next(event for event in events
                 if event.get('invocation_id')=='each:0:work' and event.get('status')=='success')
    assert {key:outcome[key] for key in ('node_id','invocation_id','parent_node_id','item_index')} \
           == dispatched[0].event_fields()


@pytest.mark.asyncio
async def test_stop_halts_at_the_first_failure_and_keeps_earlier_results():
    def flaky(index,text):
        if index==2:raise TransientToolError('gone')
        return 'done'
    resolver,calls=platform(issues(6),flaky)
    result=await run(loop_flow(on_item_error='stop'),resolver)
    results=result['values']['each']['results']
    assert len(calls)==3 and len(results)==3
    assert [r['status'] for r in results]==['success','success','failed']


@pytest.mark.asyncio
async def test_max_items_truncates_and_reports_it():
    resolver,calls=platform(issues(10))
    events=[]
    result=await run(loop_flow(max_items=4),resolver,events)
    assert len(calls)==4 and len(result['values']['each']['results'])==4
    summary=next(e for e in events if e.get('kind')=='loop_summary')
    assert summary['truncated'] is True and summary['total']==10 and summary['processed']==4


@pytest.mark.asyncio
async def test_uncertain_write_stops_the_loop_rather_than_iterating_past_it():
    def risky(index,text):
        if index==1:raise UncertainWriteError('write outcome unknown')
        return 'done'
    resolver,calls=platform(issues(5),risky)
    with pytest.raises(ValueError) as exc:await run(loop_flow(),resolver)
    assert 'unknown' in str(exc.value)
    assert len(calls)==2, 'iteration must not continue past an uncertain external write'


# --------------------------------------------------------------------------- durability

@pytest.mark.asyncio
async def test_resume_skips_completed_items(tmp_path,monkeypatch):
    """Killing the run at item 18 of 30 must resume there, not repeat the first 17."""
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from backend.app import tool_service
    from cryptography.fernet import Fernet
    monkeypatch.setattr(tool_service,'public_addresses',lambda host,port:['93.184.216.34'])
    store=Store(f'sqlite:///{tmp_path}/loop.db',Fernet.generate_key())
    worker=Worker(store)
    # Jira reads take the prepared path; the loop body takes the plain execute path.
    jira_body=json.dumps({'issues':[{'key':f'PROJ-{i}','fields':{
        'summary':f'Issue {i}','status':{'name':'Open'},
        'assignee':{'displayName':'Ana'},'updated':'2026-09-01T00:00:00Z'}} for i in range(30)]})
    async def prepared(_prepared,tenant_id='local'):return jira_body
    calls={'n':0,'fail_at':18}
    async def body(node_type,settings,text,tenant_id='local'):
        calls['n']+=1
        # A killed worker surfaces as cancellation, which the loop must not swallow.
        if calls['n']==calls['fail_at']:raise asyncio.CancelledError()
        return 'done'
    monkeypatch.setattr(worker.tools,'execute_prepared',prepared)
    monkeypatch.setattr(worker.tools,'execute',body)
    workflow=loop_flow(connection_id=jira_connection(store))
    row=store.create_run(workflow.model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))

    first=store.run(row['id'])
    assert first['status']!='success'
    progress=first.get('loop_progress',{}).get('each',{})
    assert len(progress)==17, f'expected 17 durable item results, saw {len(progress)}'
    assert calls['n']==18

    calls['fail_at']=0  # never fail again
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    second=store.run(row['id'])
    assert second['status']=='success'
    assert len(second['checkpoints']['each']['results'])==30
    assert calls['n']==18+13, 'items 0-16 must not be re-executed on resume'


@pytest.mark.asyncio
async def test_item_invocations_are_unique_and_ordered():
    resolver,_=platform(issues(4))
    events=[]
    await run(loop_flow(),resolver,events)
    ids=[e['invocation_id'] for e in events if e.get('invocation_id')]
    unique=list(dict.fromkeys(ids))
    assert unique==[f'each:{i}:work' for i in range(4)]


# --------------------------------------------------------------------------- validation

def test_items_must_come_from_a_list_not_text():
    workflow=loop_flow()
    workflow.nodes[2].inputs={'items':'input.message'}
    errors=validate_workflow(workflow)
    assert any('items must come from a list' in e and 'string' in e for e in errors)


def test_for_each_needs_exactly_one_body():
    workflow=loop_flow()
    workflow.edges=[e for e in workflow.edges if e.kind!='loop']
    assert any('exactly one attached body' in e for e in validate_workflow(workflow))


def test_nested_loops_are_rejected():
    workflow=loop_flow()
    workflow.nodes[3]=type(workflow.nodes[3]).model_validate(
        {'id':'work','type':'for_each','config':{'body':'work','max_items':2}})
    assert any('Nested loops are not supported' in e or 'loop body must be' in e
               for e in validate_workflow(workflow))


def test_valid_loop_workflow_passes():
    assert validate_workflow(loop_flow())==[]


def test_attached_write_body_cannot_enable_retries():
    workflow=loop_flow()
    body=next(node for node in workflow.nodes if node.id=='work')
    body.type='tool_http';body.config={'connection_id':'c1','method':'POST','enable_writes':True}
    from backend.app.models import RetryPolicy
    body.retry=RetryPolicy(attempts=1)
    assert any('writes externally cannot be retried' in error for error in validate_workflow(workflow))


def test_max_items_bounds_are_enforced():
    import pydantic
    from backend.app.registry import REGISTRY
    model=REGISTRY['for_each'].config_model
    for bad in ({'max_items':0},{'max_items':1001}):
        with pytest.raises(pydantic.ValidationError):model.model_validate({'body':'work',**bad})
