"""A3 regression guard: default behaviour must stay byte-identical while error policy lands."""
import asyncio
import json
import pytest
from backend.app.models import Workflow
from backend.app.compiler import compile_workflow, validate_workflow


def flow(**node_overrides):
    """Trigger -> tool -> response. The tool is the node whose failure policy is under test."""
    tool={'id':'work','type':'tool_python','inputs':{'input':'input.message'},
          'config':{'code':'print(input_text)','description':'Echoes its input.'},**node_overrides}
    return Workflow.model_validate({'version':1,'name':'Policy','nodes':[
        {'id':'input','type':'chat_input'},
        tool,
        {'id':'out','type':'response','inputs':{'text':'work.text'}}],
        'edges':[{'id':'a','source':'input','target':'work'},
                 {'id':'b','source':'work','target':'out'}]})


def platform(fail_times=0, error=ValueError('tool unavailable')):
    """Fails the first `fail_times` calls, then succeeds. Records every attempt."""
    state={'calls':0}
    async def resolver(action,*args):
        if action!='tool':raise AssertionError(action)
        state['calls']+=1
        if state['calls']<=fail_times:raise error
        return 'ok'
    return resolver,state


async def run(workflow,resolver,events):
    async def emit(event):events.append(dict(event))
    return await compile_workflow(workflow,message='hello',platform_resolver=resolver,emit=emit).graph.ainvoke({'values':{}})


# --------------------------------------------------------------------------- regression

@pytest.mark.asyncio
async def test_default_failure_still_ends_the_run_with_the_same_event():
    resolver,_=platform(fail_times=1)
    events=[]
    with pytest.raises(ValueError) as exc:await run(flow(),resolver,events)
    assert 'tool unavailable' in str(exc.value)
    failed=[e for e in events if e.get('status')=='failed']
    assert len(failed)==1
    assert failed[0]['node_id']=='work' and failed[0]['error']=='tool unavailable'
    assert 'attempt' not in failed[0], 'an unconfigured node must not gain retry metadata'


@pytest.mark.asyncio
async def test_default_success_path_is_unchanged():
    resolver,state=platform(fail_times=0)
    events=[]
    result=await run(flow(),resolver,events)
    assert result['values']['out']['text']=='ok'
    assert state['calls']==1
    assert [e['status'] for e in events if e.get('node_id')=='work']==['running','success']


def test_workflow_without_policy_fields_still_validates():
    assert validate_workflow(flow())==[]


def test_policy_fields_default_to_current_behaviour():
    node=flow().nodes[1]
    assert node.on_error=='fail'
    assert node.retry.attempts==0 and node.retry.base_delay==0.5


def test_saved_documents_without_policy_fields_round_trip():
    document=flow().model_dump(mode='json')
    assert document['nodes'][1]['on_error']=='fail'
    assert Workflow.model_validate(document).nodes[1].retry.attempts==0


# --------------------------------------------------------------------------- retry

from backend.app.tool_service import TransientToolError, UncertainWriteError


def retrying(attempts, base_delay=0.0):
    return {'retry':{'attempts':attempts,'base_delay':base_delay}}


@pytest.mark.asyncio
async def test_transient_failure_retries_and_succeeds():
    resolver,state=platform(fail_times=2,error=TransientToolError('provider unreachable'))
    events=[]
    result=await run(flow(**retrying(3)),resolver,events)
    assert result['values']['out']['text']=='ok'
    assert state['calls']==3
    retries=[e for e in events if e.get('status')=='retrying']
    assert [e['attempt'] for e in retries]==[1,2]
    assert all(e['transient'] and e['delay_seconds']>=0 for e in retries)


@pytest.mark.asyncio
async def test_transient_failure_exhausts_attempts_then_fails():
    resolver,state=platform(fail_times=9,error=TransientToolError('provider unreachable'))
    events=[]
    with pytest.raises(ValueError):await run(flow(**retrying(2)),resolver,events)
    assert state['calls']==3, 'one initial attempt plus two retries'
    failed=[e for e in events if e.get('status')=='failed']
    assert len(failed)==1 and failed[0]['attempt']==3


@pytest.mark.asyncio
async def test_permanent_failure_is_not_retried():
    resolver,state=platform(fail_times=9,error=ValueError('code has a syntax error'))
    events=[]
    with pytest.raises(ValueError) as exc:await run(flow(**retrying(5)),resolver,events)
    assert 'syntax error' in str(exc.value)
    assert state['calls']==1, 'a permanent failure must not consume retry attempts'
    assert not [e for e in events if e.get('status')=='retrying']


@pytest.mark.asyncio
async def test_uncertain_write_is_never_retried():
    resolver,state=platform(fail_times=9,error=UncertainWriteError('write outcome unknown'))
    events=[]
    with pytest.raises(ValueError):await run(flow(**retrying(5)),resolver,events)
    assert state['calls']==1
    assert not [e for e in events if e.get('status')=='retrying']


@pytest.mark.asyncio
async def test_backoff_grows_and_stays_capped(monkeypatch):
    from backend.app import compiler
    seen=[]
    async def no_sleep(delay):seen.append(delay)
    monkeypatch.setattr(compiler.asyncio,'sleep',no_sleep)
    resolver,_=platform(fail_times=9,error=TransientToolError('busy'))
    with pytest.raises(ValueError):await run(flow(**retrying(5,base_delay=4.0)),resolver,[])
    assert len(seen)==5
    assert all(0<=d<=compiler.MAX_NODE_RETRY_DELAY for d in seen)
    assert max(seen)>0


# --------------------------------------------------------------------------- modes

def route_flow(**node_overrides):
    tool={'id':'work','type':'tool_python','inputs':{'input':'input.message'},
          'config':{'code':'print(input_text)','description':'Echoes.'},
          'on_error':'route',**node_overrides}
    return Workflow.model_validate({'version':1,'name':'Route','nodes':[
        {'id':'input','type':'chat_input'},
        tool,
        {'id':'ok','type':'response','inputs':{'text':'work.text'}},
        {'id':'fallback','type':'response','inputs':{'text':'input.message'}}],
        'edges':[{'id':'a','source':'input','target':'work'},
                 {'id':'b','source':'work','target':'ok'},
                 {'id':'c','source':'work','target':'fallback','sourceHandle':'error'}]})


@pytest.mark.asyncio
async def test_route_sends_a_failure_down_the_error_branch():
    resolver,_=platform(fail_times=1,error=TransientToolError('unreachable'))
    events=[]
    result=await run(route_flow(),resolver,events)
    assert result['values']['fallback']['text']=='hello'
    assert 'ok' not in result['values']
    failed=next(e for e in events if e.get('status')=='failed')
    assert failed['recovered'] is True


@pytest.mark.asyncio
async def test_route_takes_the_normal_branch_when_the_node_succeeds():
    resolver,_=platform(fail_times=0)
    result=await run(route_flow(),resolver,[])
    assert result['values']['ok']['text']=='ok'
    assert 'fallback' not in result['values']


@pytest.mark.asyncio
async def test_continue_proceeds_when_nothing_reads_the_output():
    notify={'id':'work','type':'tool_python','inputs':{'input':'input.message'},
            'config':{'code':'print(input_text)','description':'Notifies.'},'on_error':'continue'}
    workflow=Workflow.model_validate({'version':1,'name':'Continue','nodes':[
        {'id':'input','type':'chat_input'},notify,
        {'id':'out','type':'response','inputs':{'text':'input.message'}}],
        'edges':[{'id':'a','source':'input','target':'work'},
                 {'id':'b','source':'work','target':'out'}]})
    resolver,_=platform(fail_times=1,error=TransientToolError('unreachable'))
    events=[]
    result=await run(workflow,resolver,events)
    assert result['values']['out']['text']=='hello'
    assert next(e for e in events if e.get('status')=='failed')['recovered'] is True


# --------------------------------------------------------------------------- validation

def test_write_node_cannot_be_retried():
    workflow=Workflow.model_validate({'version':1,'name':'Write','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'send','type':'tool_email','inputs':{'input':'input.message'},
         'config':{'connection_id':'c1','to':'a@example.com','subject':'Hi','enable_writes':True},
         'retry':{'attempts':3}},
        {'id':'out','type':'response','inputs':{'text':'send.text'}}],
        'edges':[{'id':'a','source':'input','target':'send'},{'id':'b','source':'send','target':'out'}]})
    errors=validate_workflow(workflow)
    assert any('cannot be retried' in e and 'duplicate' in e for e in errors)


def test_continue_is_rejected_when_something_reads_the_output():
    errors=validate_workflow(flow(on_error='continue'))
    assert any("'continue' is not allowed" in e and 'out' in e for e in errors)


def test_error_edge_requires_route():
    workflow=route_flow()
    workflow.nodes[1].on_error='fail'
    assert any("error edge requires on_error 'route'" in e for e in validate_workflow(workflow))


def test_route_requires_exactly_one_error_edge():
    workflow=route_flow()
    workflow.edges=[e for e in workflow.edges if e.sourceHandle!='error']
    assert any('needs exactly one normal edge and one error edge' in e for e in validate_workflow(workflow))


def test_valid_route_workflow_passes_validation():
    assert validate_workflow(route_flow())==[]


def test_retry_bounds_are_enforced_by_the_model():
    import pydantic
    for bad in ({'attempts':6},{'attempts':-1},{'base_delay':-1},{'base_delay':31}):
        with pytest.raises(pydantic.ValidationError):flow(retry=bad)


# --------------------------------------------------------------------------- durability

@pytest.mark.asyncio
async def test_recovered_node_is_not_checkpointed_and_reruns_on_resume(tmp_path,monkeypatch):
    """A recovered failure emits no outputs, so a resume re-executes it rather than
    replaying an empty result down the success branch."""
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from cryptography.fernet import Fernet
    store=Store(f'sqlite:///{tmp_path}/recover.db',Fernet.generate_key())
    worker=Worker(store)
    calls={'n':0}
    async def flaky(node_type,settings,input_text,tenant_id='local'):
        calls['n']+=1
        if calls['n']==1:raise TransientToolError('unreachable')
        return 'ok'
    monkeypatch.setattr(worker.tools,'execute',flaky)
    run_row=store.create_run(route_flow().model_dump(mode='json'),'hello')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run_row['id'])
    assert saved['status']=='success'
    assert 'work' not in saved['checkpoints'], 'a recovered node must not be checkpointed'
    assert saved['checkpoints']['fallback']['text']=='hello'
    assert any(e['status']=='failed' and e.get('recovered') for e in saved['events'])


@pytest.mark.asyncio
async def test_retry_attempts_are_visible_in_the_run_journal(tmp_path,monkeypatch):
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from cryptography.fernet import Fernet
    store=Store(f'sqlite:///{tmp_path}/retry.db',Fernet.generate_key())
    worker=Worker(store)
    calls={'n':0}
    async def flaky(node_type,settings,input_text,tenant_id='local'):
        calls['n']+=1
        if calls['n']<3:raise TransientToolError('busy')
        return 'ok'
    monkeypatch.setattr(worker.tools,'execute',flaky)
    run_row=store.create_run(flow(**retrying(3)).model_dump(mode='json'),'hello')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run_row['id'])
    assert saved['status']=='success' and calls['n']==3
    # Retries are part of the run's history: an operator must be able to see that the
    # node only succeeded on its third attempt, and how long it waited between them.
    retries=[e for e in saved['events'] if e.get('status')=='retrying']
    assert [e['attempt'] for e in retries]==[1,2]
    assert all(e['error']=='busy' and e['delay_seconds']>=0 for e in retries)
    # Transient events are still excluded from checkpoints: only the success is replayable.
    assert saved['checkpoints']['work']['text']=='ok'
