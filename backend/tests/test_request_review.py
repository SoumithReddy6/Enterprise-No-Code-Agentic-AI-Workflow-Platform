"""Typed review through the real worker/store, before any delivery."""
import copy
import json

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Workflow
from backend.app.registry import REGISTRY, NodeDefinition
from backend.app.request_contracts import ExtractionCheckConfig
from backend.app.storage import Store, JobRecord
from backend.app.worker import Worker


def flow(connection, rule='source_field', value_schema=None):
    rules = [{'field':'/amount','kind':'source_field','source_pointer':'/amount'}] if rule == 'source_field' else [
        {'field':'/amount','kind':'quoted'}, {'field':'/amount','kind':'enum','allowed_values':[0,1200]}]
    return Workflow.model_validate({'name':'Checked purchase','contract':2,'nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'cache','type':'greeting_cache','inputs':{'message':'input.message'}},
        {'id':'router','type':'request_router','inputs':{'message':'input.message'},'config':{'purpose':'Handle purchase requests','provider':'ollama','model':'fixture'}},
        {'id':'extract','type':'llm','inputs':{'prompt':'input.message'},'config':{'provider':'ollama','model':'fixture'}},
        {'id':'verify','type':'verify_extraction','inputs':{'original':'input.message','value':'extract.text'},'config':{
            'expected_schema':value_schema or {'type':'object','properties':{'amount':{'type':'number','minimum':0}},'required':['amount']},
            'required_fields':['/amount'],'field_rules':rules}},
        {'id':'send','type':'tool_http','inputs':{'input':'verify.text'},'config':{'connection_id':connection,'method':'POST','path':'/deliver','body':'{input}','enable_writes':True,'approval':False}},
        {'id':'out','type':'response','inputs':{'text':'send.text'}},
        {'id':'greeting','type':'response','inputs':{'text':'cache.reply'}},
        {'id':'declined','type':'response','inputs':{'text':'router.reply'}},
        {'id':'clarified','type':'response','inputs':{'text':'verify.reply'}},
    ],'edges':[
        {'id':'a','source':'input','target':'cache'},
        {'id':'b','source':'cache','target':'router','sourceHandle':'continue'},
        {'id':'c','source':'cache','target':'greeting','sourceHandle':'reply'},
        {'id':'d','source':'router','target':'extract','sourceHandle':'continue'},
        {'id':'e','source':'router','target':'declined','sourceHandle':'reply'},
        {'id':'f','source':'extract','target':'verify'},
        {'id':'g','source':'verify','target':'send','sourceHandle':'continue'},
        {'id':'h','source':'verify','target':'clarified','sourceHandle':'reply'},
        {'id':'i','source':'send','target':'out'}]})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    # Reproduce missing enforcement on the old checkout with an intentionally unsafe
    # placeholder. The implementation must ignore forged verifier-handler decisions.
    if 'verify_extraction' not in REGISTRY:
        async def unsafe(inputs,config,ctx):
            return {'text':inputs['value'],'branch':'continue','reply':'','report':'{}'}
        monkeypatch.setitem(REGISTRY,'verify_extraction',NodeDefinition('verify_extraction','Verifier','Control','fixture',
            {'original':'string','value':'string'},{'text':'string','branch':'string','reply':'string','report':'string'},
            ExtractionCheckConfig,unsafe,branch_routes=('continue','reply'),optional_inputs={'reference':'string'}))
    store = Store(f'sqlite:///{tmp_path}/review.db',Fernet.generate_key());store.allow_model('ollama','fixture')
    worker = Worker(store,common_reply_cache=None)
    connection = worker.tools.save_connection({'name':'Receiver','provider':'http','endpoint':'https://receiver.example'})['id']
    calls = [];model_calls = [];responses = []
    async def chat(model,system,prompt,usage=None,**kwargs):
        model_calls.append(prompt);usage.update(prompt_tokens=5,completion_tokens=2)
        return responses.pop(0)
    monkeypatch.setattr('backend.app.providers.ollama_chat',chat)
    monkeypatch.setattr('backend.app.tool_service.public_addresses',lambda *a:['93.184.216.34'])
    worker.tools.transport = httpx.MockTransport(lambda request:(calls.append(request) or httpx.Response(200,text='sent')))
    def script(value,*following):
        responses.extend([json.dumps({'route':'proceed','reason':'Synthetic admission','missing_fields':[],'reply':''}),value,*following])
    yield store,worker,connection,calls,model_calls,script
    store.engine.dispose()


async def execute(setup, original='Hello', value='{"amount":0}', rule='enum'):
    store,worker,connection,_,_,script = setup;script(value)
    run = store.create_run(flow(connection,rule).model_dump(mode='json'),original)
    await worker.execute(store.claim_next(worker.owner))
    return store.run(run['id'])


@pytest.mark.asyncio
async def test_wrong_router_proceed_pauses_before_any_send_and_releases_lease(setup):
    store,worker,_,calls,model_calls,_ = setup
    saved = await execute(setup)
    assert saved['status'] == 'awaiting_approval' and calls == []
    approval = saved['approvals'][0]
    assert approval['kind'] == 'verification' and approval['payload']['value'] == '{"amount":0}'
    assert approval['payload']['original'] == 'Hello'
    assert store.claim_next('other') is None
    with Session(store.engine) as s:
        job=s.get(JobRecord,saved['id']);assert job.owner == '' and job.lease_until == 0
    assert len(model_calls) == 2 and saved['accounting']['tokens_spent'] == 14


@pytest.mark.asyncio
async def test_matching_confirmation_resumes_without_model_and_delivers_once(setup):
    from backend.app.approvals import decide
    store,worker,_,calls,model_calls,_ = setup;saved = await execute(setup);approval=saved['approvals'][0]
    decide(store,saved['id'],'local',approval['id'],approval['digest'],'approve')
    decide(store,saved['id'],'local',approval['id'],approval['digest'],'approve')
    await worker.execute(store.claim_next(worker.owner))
    final=store.run(saved['id']);assert final['status']=='success' and len(calls)==1 and len(model_calls)==2
    assert json.loads(calls[0].content)=={'amount':0}
    receipt=final['checkpoints']['verify']['_verification']
    assert receipt['review_id']==approval['id'] and receipt['report']['verdict']=='needs_review'
    assert final['checkpoints']['verify']['_provenance']['ports']['text']=='guessed'
    decide(store,saved['id'],'local',approval['id'],approval['digest'],'approve')
    assert store.claim_next('other') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('action',['reject','expire','cancel','wrong_digest','foreign'])
async def test_unconfirmed_interpretations_never_send(setup, action):
    from backend.app.approvals import decide,ApprovalRecord,expire_pending
    store,worker,_,calls,_,_=setup;saved=await execute(setup);approval=saved['approvals'][0]
    if action=='reject':decide(store,saved['id'],'local',approval['id'],approval['digest'],'reject')
    elif action=='cancel':store.request_cancel(saved['id'])
    elif action=='expire':
        with Session(store.engine) as s:s.get(ApprovalRecord,approval['id']).expires_at=0;s.commit()
        expire_pending(store)
        with pytest.raises(ValueError):decide(store,saved['id'],'local',approval['id'],approval['digest'],'approve')
    elif action=='wrong_digest':
        with pytest.raises(ValueError):decide(store,saved['id'],'local',approval['id'],'0'*64,'approve')
    else:
        with pytest.raises(KeyError):decide(store,saved['id'],'other',approval['id'],approval['digest'],'approve')
    assert calls==[] and store.claim_next('other') is None
    if action in ('reject','expire','cancel'):
        assert store.run(saved['id'])['status']=='cancelled'
        with pytest.raises(ValueError):store.resume_run(saved['id'])


@pytest.mark.asyncio
async def test_exact_structured_fact_passes_without_review(setup):
    store,_,_,calls,_,_=setup
    saved=await execute(setup,original='{"amount":0}',value='{"amount":0}',rule='source_field')
    assert saved['status']=='success' and len(calls)==1 and not saved.get('approvals')
    receipt=saved['checkpoints']['verify']['_verification']
    assert receipt['report']['verdict']=='passed' and receipt['run_id']==saved['id']


@pytest.mark.asyncio
async def test_missing_fact_clarifies_instead_of_review_or_send(setup):
    saved=await execute(setup,original='Hello',value='{}',rule='source_field')
    assert saved['status']=='success' and not saved.get('approvals') and setup[3]==[]
    assert 'send' not in saved['checkpoints'] and saved['checkpoints']['verify']['branch']=='reply'


@pytest.mark.asyncio
async def test_verifier_handler_cannot_mint_its_own_continue_or_receipt(setup, monkeypatch):
    from dataclasses import replace
    async def forged(inputs,config,ctx):
        return {'text':inputs['value'],'branch':'continue','reply':'','report':'{}','_verification':{'v':1}}
    monkeypatch.setitem(REGISTRY,'verify_extraction',replace(REGISTRY['verify_extraction'],handler=forged))
    saved=await execute(setup)
    assert saved['status']=='awaiting_approval' and setup[3]==[]


@pytest.mark.asyncio
async def test_changed_extraction_after_review_cannot_reuse_confirmation(setup):
    from backend.app.approvals import decide
    from backend.app.storage import RunRecord
    store,worker,_,calls,_,_=setup;saved=await execute(setup);approval=saved['approvals'][0]
    decide(store,saved['id'],'local',approval['id'],approval['digest'],'approve')
    with Session(store.engine) as s:
        row=s.get(RunRecord,saved['id']);data=copy.deepcopy(row.data);data['checkpoints']['extract']['text']='{"amount":1200}';row.data=data;s.commit()
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(saved['id'])['status']=='failed' and calls==[]


def checking_flow(connection):
    raw=flow(connection).model_dump(mode='json')
    raw['nodes'] += [
        {'id':'cross','type':'calculate','inputs':{'value':'verify.text'},'config':{'mode':'check','expression':'amount > 1000'}},
        {'id':'choose','type':'condition','inputs':{'value':'cross.text'},'config':{'contains':'true'}},
        {'id':'humanFalse','type':'response','inputs':{'text':'cross.text'}}]
    next(e for e in raw['edges'] if e['source']=='verify' and e['sourceHandle']=='continue')['target']='cross'
    raw['edges'] += [{'id':'j','source':'cross','target':'choose'},
        {'id':'k','source':'choose','target':'send','sourceHandle':'true'},
        {'id':'l','source':'choose','target':'humanFalse','sourceHandle':'false'}]
    next(n for n in raw['nodes'] if n['id']=='send')['inputs']['input']='cross.text'
    return Workflow.model_validate(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize('choice',[True,False])
async def test_failed_check_requires_explicit_choice_and_uses_that_branch(setup,choice):
    from backend.app.approvals import decide
    store,worker,connection,calls,model_calls,script=setup;script('{"amount":0}')
    run=store.create_run(checking_flow(connection).model_dump(mode='json'),'{"amount":0}')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='awaiting_approval' and calls==[]
    approval=saved['approvals'][0];assert approval['kind']=='decision'
    for bad in (None,0,'true'):
        with pytest.raises(ValueError,match='explicit'):decide(store,run['id'],'local',approval['id'],approval['digest'],'approve',bad)
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve',choice)
    decide(store,run['id'],'local',approval['id'],approval['digest'],'approve',choice)
    with pytest.raises(ValueError,match='different choice'):decide(store,run['id'],'local',approval['id'],approval['digest'],'approve',not choice)
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='success',saved.get('error')
    assert len(calls)==int(choice) and len(model_calls)==2
    assert saved['checkpoints']['choose']['branch']==('true' if choice else 'false')
    assert saved['checkpoints']['cross']['_decision_review']['choice'] is choice
    assert saved['checkpoints']['cross']['_provenance']['ports']['text']=='guessed'
    assert any(e.get('decision',{}).get('manual') is True for e in saved['events'])
    check_event=next(e for e in saved['events'] if e.get('node_id')=='cross' and e.get('decision'))
    assert check_event['decision']['passed'] is False  # The human choice does not make the original check pass.
    from backend.app.operator_metrics import metrics
    assert metrics(store)['human_reviews']['by_kind']['decision']['completed']==1


@pytest.mark.asyncio
@pytest.mark.parametrize('policy',['continue','route'])
async def test_unsafe_refusal_cannot_recover_or_send_on_an_error_path(setup,monkeypatch,policy):
    from backend.app.request_safety import UnsafeExecutionError
    store,worker,connection,calls,_,script=setup;script('{"amount":0}')
    raw=flow(connection).model_dump(mode='json')
    send=next(n for n in raw['nodes'] if n['id']=='send');send['on_error']=policy
    if policy=='continue':next(n for n in raw['nodes'] if n['id']=='out')['inputs']={'text':'verify.text'}
    else:
        raw['nodes'].append({'id':'errorOut','type':'response','inputs':{'text':'verify.text'}})
        raw['edges'].append({'id':'error','source':'send','target':'errorOut','sourceHandle':'error'})
    original=__import__('backend.app.request_safety',fromlist=['guard']).guard
    async def denied(ctx,node,inputs,identity=None):
        if node.id=='send':raise UnsafeExecutionError('Missing eligible verification')
        return await original(ctx,node,inputs,identity)
    monkeypatch.setattr('backend.app.request_safety.guard',denied)
    run=store.create_run(raw,'{"amount":0}');await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='failed' and calls==[]
    assert not any(e.get('recovered') for e in saved['events'])
    assert saved['accounting']['action_budget']['counter']==0


@pytest.mark.asyncio
async def test_reject_is_idempotent_and_payload_damage_is_refused(setup):
    from backend.app.approvals import decide,ApprovalRecord
    store,_,_,calls,_,_=setup;saved=await execute(setup);approval=saved['approvals'][0]
    with Session(store.engine) as s:
        row=s.get(ApprovalRecord,approval['id']);row.payload={**row.payload,'original':'different'};s.commit()
    with pytest.raises(ValueError,match='digest'):decide(store,saved['id'],'local',approval['id'],approval['digest'],'approve')
    with Session(store.engine) as s:s.get(ApprovalRecord,approval['id']).payload=approval['payload'];s.commit()
    assert decide(store,saved['id'],'local',approval['id'],approval['digest'],'reject')['status']=='rejected'
    assert decide(store,saved['id'],'local',approval['id'],approval['digest'],'reject')['status']=='rejected'
    assert calls==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('tamper',['receipt','review','policy','run','value','origin','expiry','foreign_review'])
async def test_cached_verification_never_restores_damaged_authority(setup,monkeypatch,tamper):
    from backend.app.approvals import decide,ApprovalRecord
    from backend.app.storage import RunRecord
    import asyncio
    store,worker,_,calls,_,_=setup;saved=await execute(setup);approval=saved['approvals'][0]
    decide(store,saved['id'],'local',approval['id'],approval['digest'],'approve')
    original=store.worker_event
    def after_verify(id,owner,event):
        result=original(id,owner,event)
        if event.get('node_id')=='verify' and event.get('status')=='success':raise RuntimeError('Injected interruption after verification commit')
        return result
    monkeypatch.setattr(store,'worker_event',after_verify)
    await worker.execute(store.claim_next(worker.owner));assert calls==[]
    monkeypatch.setattr(store,'worker_event',original)
    with Session(store.engine) as s:
        row=s.get(RunRecord,saved['id']);data=copy.deepcopy(row.data);receipt=data['checkpoints']['verify']['_verification']
        if tamper=='receipt':receipt['report_digest']='0'*64
        elif tamper=='review':s.get(ApprovalRecord,approval['id']).result='not confirmed'
        elif tamper=='policy':next(n for n in data['workflow']['nodes'] if n['id']=='verify')['config']['cross_checks']=['amount > 0']
        elif tamper=='run':receipt['run_id']='anotherRun'
        elif tamper=='value':data['checkpoints']['verify']['text']='{"amount":1200}'
        elif tamper=='origin':data['checkpoints']['input']['message']='Different'
        elif tamper=='expiry':s.get(ApprovalRecord,approval['id']).expires_at=0
        else:s.get(ApprovalRecord,approval['id']).tenant_id='other'
        row.data=data;s.commit()
    store.resume_run(saved['id'])
    await worker.execute(store.claim_next(worker.owner));saved=store.run(saved['id'])
    assert saved['status']=='failed' and calls==[],saved.get('error')


@pytest.mark.asyncio
async def test_attached_agent_write_cannot_change_the_verified_value(setup):
    store,worker,connection,calls,model_calls,script=setup
    raw=flow(connection).model_dump(mode='json')
    raw['nodes'] += [{'id':'agent','type':'agent','inputs':{'input':'verify.text'},
        'config':{'provider':'ollama','model':'fixture','system':'Use the attached sender.'}}]
    next(e for e in raw['edges'] if e['source']=='verify' and e['sourceHandle']=='continue')['target']='agent'
    next(e for e in raw['edges'] if e['source']=='send')['source']='agent'
    next(n for n in raw['nodes'] if n['id']=='out')['inputs']={'text':'agent.text'}
    raw['edges'].append({'id':'attached','source':'send','target':'agent','kind':'tool'})
    next(n for n in raw['nodes'] if n['id']=='send')['inputs']={}
    # A real model response requests a different payload, including a plausible source number.
    script('{"amount":0}','{"action":"call","target":"send","input":"{\\"amount\\":1200}"}')
    run=store.create_run(raw,'{"amount":0}');await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='failed' and calls==[]
    assert 'eligible' in saved['error'] and saved['accounting']['action_budget']['counter']==0


@pytest.mark.asyncio
async def test_specialist_cannot_write_a_changed_task_or_observe_the_refusal(setup):
    store,worker,connection,calls,model_calls,script=setup
    raw=flow(connection).model_dump(mode='json')
    raw['nodes'] += [{'id':'agent','type':'agent','inputs':{'input':'verify.text'},'config':{'provider':'ollama','model':'fixture'}},
        {'id':'specialist','type':'agent','config':{'provider':'ollama','model':'fixture'}}]
    next(e for e in raw['edges'] if e['source']=='verify' and e['sourceHandle']=='continue')['target']='agent'
    next(e for e in raw['edges'] if e['source']=='send')['source']='agent'
    next(n for n in raw['nodes'] if n['id']=='out')['inputs']={'text':'agent.text'}
    raw['edges'] += [{'id':'attached','source':'send','target':'specialist','kind':'tool'},
        {'id':'delegation','source':'agent','target':'specialist','kind':'agent'}]
    next(n for n in raw['nodes'] if n['id']=='send')['inputs']={}
    script('{"amount":0}',json.dumps({'action':'call','target':'specialist','input':'{"amount":0}'}),
        json.dumps({'action':'call','target':'send','input':'{"amount":1200}'}))
    run=store.create_run(raw,'{"amount":0}');await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='failed' and calls==[],saved.get('error')
    assert len(model_calls)==4 and saved['accounting']['action_budget']['counter']==1


@pytest.mark.asyncio
async def test_looped_agent_refusal_is_never_an_item_error_that_continues(setup):
    store,worker,connection,calls,model_calls,script=setup
    raw=flow(connection).model_dump(mode='json');value='[{"amount":0},{"amount":1200}]'
    verify=next(n for n in raw['nodes'] if n['id']=='verify')
    verify['config']={'expected_schema':{'type':'array'},'required_fields':['/0/amount','/1/amount'],
        'field_rules':[{'field':f'/{i}/amount','kind':'source_field','source_pointer':f'/{i}/amount'} for i in range(2)]}
    next(n for n in raw['nodes'] if n['id']=='send')['inputs']={}
    raw['nodes'] += [{'id':'each','type':'for_each','inputs':{'items':'verify.text'},'config':{'body':'agent','on_item_error':'continue'}},
        {'id':'agent','type':'agent','config':{'provider':'ollama','model':'fixture'}}]
    next(e for e in raw['edges'] if e['source']=='verify' and e['sourceHandle']=='continue')['target']='each'
    next(e for e in raw['edges'] if e['source']=='send')['source']='each'
    raw['edges'] += [{'id':'body','source':'each','target':'agent','kind':'loop'},
        {'id':'attached','source':'send','target':'agent','kind':'tool'}]
    next(n for n in raw['nodes'] if n['id']=='out')['inputs']={'text':'each.failed'}
    script(value,json.dumps({'action':'call','target':'send','input':'{"amount":1200}'}))
    run=store.create_run(raw,value);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='failed' and calls==[],saved.get('error')
    assert len(model_calls)==3 and not saved.get('loop_progress')
    assert saved['accounting']['action_budget']['counter']==0


@pytest.mark.asyncio
async def test_review_consumption_and_restore_require_the_current_owner(setup):
    from backend.app.approvals import decide,consume_review,checked_review
    from backend.app.storage import LeaseLost
    store,worker,_,calls,_,_=setup;saved=await execute(setup);review=saved['approvals'][0]
    decide(store,saved['id'],'local',review['id'],review['digest'],'approve')
    claim=store.claim_next(worker.owner)
    for call,args in [(consume_review,('verify:review:verification','verification',review['payload'])),
                      (checked_review,(review['id'],'verification',review['payload']))]:
        with pytest.raises(LeaseLost):call(store,saved['id'],'stale-owner',*args)
    assert calls==[] and store.run(saved['id'])['approvals'][0]['status']=='approved'
    await worker.execute(claim)
    assert len(calls)==1


@pytest.mark.asyncio
async def test_verification_review_is_rate_limited_without_authorizing_the_run(setup,monkeypatch):
    from backend.app.approvals import decide
    from fastapi import HTTPException
    from types import SimpleNamespace
    monkeypatch.setattr('backend.app.approvals.time',SimpleNamespace(time=lambda:1800000000.0))
    saved=await execute(setup);review=saved['approvals'][0]
    for _ in range(30):
        with pytest.raises(ValueError):decide(setup[0],saved['id'],'local',review['id'],'0'*64,'approve')
    with pytest.raises(HTTPException) as exc:decide(setup[0],saved['id'],'local',review['id'],review['digest'],'approve')
    assert exc.value.status_code==429 and setup[3]==[]
    assert setup[0].run(saved['id'])['status']=='awaiting_approval'


@pytest.mark.asyncio
async def test_verified_loop_writes_each_exact_item_and_cannot_use_a_sibling(setup):
    store,worker,connection,calls,_,script=setup
    value='[{"amount":0},{"amount":1200}]';script(value)
    raw=flow(connection).model_dump(mode='json')
    verify=next(n for n in raw['nodes'] if n['id']=='verify')
    verify['config']={'expected_schema':{'type':'array'},'required_fields':['/0/amount','/1/amount'],
        'field_rules':[{'field':f'/{i}/amount','kind':'source_field','source_pointer':f'/{i}/amount'} for i in range(2)]}
    next(n for n in raw['nodes'] if n['id']=='send')['inputs']={}
    raw['nodes'].append({'id':'each','type':'for_each','inputs':{'items':'verify.text'},'config':{'body':'send'}})
    next(e for e in raw['edges'] if e['source']=='verify' and e['sourceHandle']=='continue')['target']='each'
    next(e for e in raw['edges'] if e['source']=='send')['source']='each'
    raw['edges'].append({'id':'body','source':'each','target':'send','kind':'loop','sourceHandle':'body','targetHandle':'input'})
    next(n for n in raw['nodes'] if n['id']=='out')['inputs']={'text':'each.failed'}
    run=store.create_run(raw,value);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='success',saved.get('error')
    assert [json.loads(c.content) for c in calls]==[{'amount':0},{'amount':1200}]
    assert saved['accounting']['action_budget']['counter']==2


def automatic_condition_flow(connection):
    raw=flow(connection).model_dump(mode='json')
    raw['nodes'] += [{'id':'choose','type':'condition','inputs':{'value':'verify.text'},
        'config':{'operator':'gt','field':'amount','compare_to':'1000'}},
        {'id':'falseOut','type':'response','inputs':{'text':'verify.text'}}]
    next(e for e in raw['edges'] if e['source']=='verify' and e['sourceHandle']=='continue')['target']='choose'
    raw['edges'] += [{'id':'yes','source':'choose','target':'send','sourceHandle':'true'},
        {'id':'no','source':'choose','target':'falseOut','sourceHandle':'false'}]
    return raw


@pytest.mark.asyncio
async def test_cached_automatic_condition_cannot_change_the_verified_branch(setup,monkeypatch):
    from backend.app.storage import RunRecord
    store,worker,connection,calls,_,script=setup;script('{"amount":0}')
    raw=automatic_condition_flow(connection);run=store.create_run(raw,'{"amount":0}')
    old=store.worker_event
    def after_choose(id,owner,event):
        result=old(id,owner,event)
        if event.get('node_id')=='choose' and event.get('status')=='success':raise RuntimeError('Interrupt after condition commit')
        return result
    monkeypatch.setattr(store,'worker_event',after_choose);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['checkpoints']['choose']['branch']=='false' and calls==[]
    monkeypatch.setattr(store,'worker_event',old)
    with Session(store.engine) as s:
        row=s.get(RunRecord,run['id']);data=copy.deepcopy(row.data);data['checkpoints']['choose']['branch']='true';row.data=data;s.commit()
    store.resume_run(run['id']);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='failed' and calls==[],saved.get('error')
    assert saved['accounting']['action_budget']['counter']==0


@pytest.mark.asyncio
async def test_fresh_condition_handler_cannot_override_the_configured_comparison(setup,monkeypatch):
    from dataclasses import replace
    store,worker,connection,calls,_,script=setup;script('{"amount":0}')
    async def forged(*args):return {'branch':'true'}
    monkeypatch.setitem(REGISTRY,'condition',replace(REGISTRY['condition'],handler=forged))
    run=store.create_run(automatic_condition_flow(connection),'{"amount":0}')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run['id'])['status']=='failed' and calls==[]


@pytest.mark.asyncio
async def test_expired_manual_control_cannot_authorize_an_independently_bound_write(setup,monkeypatch):
    from backend.app.approvals import decide,ApprovalRecord
    store,worker,connection,calls,_,script=setup;script('{"amount":0}')
    raw=checking_flow(connection).model_dump(mode='json')
    next(n for n in raw['nodes'] if n['id']=='send')['inputs']={'input':'verify.text'}
    run=store.create_run(raw,'{"amount":0}');await worker.execute(store.claim_next(worker.owner))
    review=store.run(run['id'])['approvals'][0];decide(store,run['id'],'local',review['id'],review['digest'],'approve',True)
    old=store.worker_event
    def expire_after_choose(id,owner,event):
        result=old(id,owner,event)
        if event.get('node_id')=='choose' and event.get('status')=='success':
            with Session(store.engine) as s:s.get(ApprovalRecord,review['id']).expires_at=0;s.commit()
        return result
    monkeypatch.setattr(store,'worker_event',expire_after_choose)
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='failed' and calls==[],saved.get('error')
    assert saved['accounting']['action_budget']['counter']==0


@pytest.mark.parametrize('schema',[None,False,'array',[]])
def test_invalid_schema_on_a_verified_loop_binding_is_an_error_not_a_crash(schema):
    from backend.app.compiler import validate_workflow
    raw=flow('fixture').model_dump(mode='json')
    next(n for n in raw['nodes'] if n['id']=='verify')['config']['expected_schema']=schema
    next(n for n in raw['nodes'] if n['id']=='send')['inputs']={}
    raw['nodes'].append({'id':'each','type':'for_each','inputs':{'items':'verify.text'},'config':{'body':'send'}})
    next(e for e in raw['edges'] if e['source']=='verify' and e['sourceHandle']=='continue')['target']='each'
    next(e for e in raw['edges'] if e['source']=='send')['source']='each'
    raw['edges'].append({'id':'body','source':'each','target':'send','kind':'loop'})
    next(n for n in raw['nodes'] if n['id']=='out')['inputs']={'text':'each.failed'}
    assert validate_workflow(Workflow.model_validate(raw))


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['prompt','calculate'])
async def test_cached_derivation_cannot_change_checked_values_and_coalesce_reads_only_its_branch(setup,monkeypatch,kind):
    from backend.app.storage import RunRecord
    store,worker,connection,calls,_,script=setup
    value='{"amount":0,"unused":"not a number"}';script(value)
    raw=flow(connection).model_dump(mode='json');verify=next(n for n in raw['nodes'] if n['id']=='verify')
    verify['config']['field_rules'].append({'field':'/unused','kind':'source_field','source_pointer':'/unused'})
    raw['nodes'].append({'id':'derived','type':kind,'inputs':{'message' if kind=='prompt' else 'value':'verify.text'},
        'config':{'template':'{message}'} if kind=='prompt' else {'expression':'coalesce(amount, unused)'}})
    next(e for e in raw['edges'] if e['source']=='verify' and e['sourceHandle']=='continue')['target']='derived'
    raw['edges'].append({'id':'derivation','source':'derived','target':'send'})
    next(n for n in raw['nodes'] if n['id']=='send')['inputs']={'input':'derived.text'}
    old=store.worker_event
    def after_derived(id,owner,event):
        result=old(id,owner,event)
        if event.get('node_id')=='derived' and event.get('status')=='success':raise RuntimeError('Interrupt after derivation commit')
        return result
    monkeypatch.setattr(store,'worker_event',after_derived)
    run=store.create_run(raw,value);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['checkpoints']['derived']['text']==(value if kind=='prompt' else '{"result": 0}')
    assert calls==[];monkeypatch.setattr(store,'worker_event',old)
    with Session(store.engine) as s:
        row=s.get(RunRecord,run['id']);data=copy.deepcopy(row.data)
        data['checkpoints']['derived']['text']='{"amount":1200}' if kind=='prompt' else '{"result":1200}'
        row.data=data;s.commit()
    store.resume_run(run['id']);await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id'])
    assert saved['status']=='failed' and calls==[]
    assert 'Derived output changed after its checked inputs.' in saved['error']
    assert not any(e.get('node_id')=='send' for e in saved['events'])
