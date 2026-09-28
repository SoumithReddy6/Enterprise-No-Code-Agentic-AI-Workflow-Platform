"""S07 boundary regressions, adopted from the 2026-09-27 fault-injection audit.

Each test reproduced a defect before its fix: a settled agent write sent twice after
resume, a re-executed read running free, a lost lease swallowed as an agent observation,
approval-resume tokens counted twice, a paid response lost to an accounting outage, and an
approval frame restoring an obsolete budget ceiling. The original probe file is kept as
evidence under evals/results/s07-complete-probe-2026-09-27/.
"""
import asyncio
import json
import pytest
from cryptography.fernet import Fernet
from backend.app.storage import Store,LeaseLost
from backend.app.worker import Worker
from backend.app.compiler import compile_workflow
from backend.tests.test_agent_tools import calc_flow,scripted
from backend.tests.test_node_error_policy import flow


@pytest.fixture
def lab(tmp_path,monkeypatch):
    store=Store(f'sqlite:///{tmp_path}/probe.db',Fernet.generate_key())
    worker=Worker(store)
    monkeypatch.setattr('backend.app.tool_service.public_addresses',lambda *_:['93.184.216.34'])
    yield store,worker
    store.engine.dispose()


@pytest.mark.asyncio
async def test_agent_settled_write_keeps_identity_after_crash(lab,monkeypatch):
    store,worker=lab;deliveries=[]
    connection=worker.tools.save_connection({'name':'sink','provider':'http','endpoint':'https://example.com'})
    workflow=calc_flow();workflow.nodes[2].type='tool_http'
    workflow.nodes[2].config={'connection_id':connection['id'],'method':'POST','enable_writes':True,'approval':False}
    scripted(monkeypatch,['{"action":"call","target":"calc","input":"same reply"}',
                          '{"action":"call","target":"calc","input":"same reply"}',
                          '{"action":"final","text":"done"}'])
    async def send(_type,_settings,text,*_):deliveries.append(text);return 'sent'
    monkeypatch.setattr(worker.tools,'execute',send)
    original=store.worker_event
    def crash(id,owner,event):
        if event.get('node_id')=='calc' and event['status']=='success':raise asyncio.CancelledError()
        return original(id,owner,event)
    monkeypatch.setattr(store,'worker_event',crash)
    row=store.create_run(workflow.model_dump(mode='json'),'send')
    await worker.execute(store.claim_next(worker.owner))
    assert deliveries==['same reply']
    assert store.completed_action(row['id'],'agent:1:calc')=='sent'
    monkeypatch.setattr(store,'worker_event',original)
    store.resume_run(row['id']);await worker.execute(store.claim_next(worker.owner))
    assert deliveries==['same reply'], f'duplicate deliveries: {deliveries}'


@pytest.mark.asyncio
async def test_reexecuted_read_is_not_free_on_resume(lab,monkeypatch):
    store,worker=lab;calls=[]
    monkeypatch.setenv('AGENT_RUN_BUDGET','1')
    async def read(*_):calls.append(1);return 'read result'
    monkeypatch.setattr(worker.tools,'execute',read)
    original=store.worker_event
    def crash(id,owner,event):
        if event.get('node_id')=='work' and event['status']=='success':raise asyncio.CancelledError()
        return original(id,owner,event)
    monkeypatch.setattr(store,'worker_event',crash)
    row=store.create_run(flow().model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))
    assert len(calls)==1 and store.run(row['id'])['accounting']['action_budget']['counter']==1
    monkeypatch.setattr(store,'worker_event',original)
    store.resume_run(row['id']);await worker.execute(store.claim_next(worker.owner))
    assert len(calls)==1, f'{len(calls)} real executions under action budget 1'


@pytest.mark.asyncio
async def test_agent_does_not_swallow_reservation_lease_loss(monkeypatch):
    model_calls=[]
    scripted(monkeypatch,['{"action":"call","target":"calc","input":"x"}',
                          '{"action":"final","text":"continued"}'],model_calls)
    async def lost(_):raise LeaseLost('lost lease')
    async def tool(*_):pytest.fail('must not call tool')
    with pytest.raises(LeaseLost):
        await compile_workflow(calc_flow(),persist_accounting=lost,platform_resolver=tool).graph.ainvoke({'values':{}})
    assert len(model_calls)==1


@pytest.mark.asyncio
async def test_approval_resume_usage_not_counted_twice(lab,monkeypatch):
    from backend.app import registry,providers
    from backend.app.approvals import decide
    store,worker=lab
    store.allow_model('ollama','m','','local')
    conn=worker.tools.save_connection({'name':'sink','provider':'http','endpoint':'https://example.com'})
    workflow=calc_flow();workflow.nodes[1].config={'provider':'ollama','model':'m'}
    workflow.nodes[2].type='tool_http';workflow.nodes[2].config={'connection_id':conn['id'],'method':'POST','enable_writes':True,'approval':True}
    replies=iter(['{"action":"call","target":"calc","input":"x"}','{"action":"final","text":"done"}'])
    async def model(*args,usage=None,**kwargs):
        providers.record_usage(usage,10,0);return next(replies)
    async def send(*_):return 'sent'
    monkeypatch.setattr(providers,'ollama_chat',model)
    monkeypatch.setattr(worker.tools,'execute_prepared',send)
    row=store.create_run(workflow.model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))
    approval=store.run(row['id'])['approvals'][0]
    decide(store,row['id'],'local',approval['id'],approval['digest'],'approve')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(row['id']);assert saved['status']=='success',saved.get('error')
    total=sum(e.get('usage',{}).get('prompt_tokens',0) for e in saved['events'])
    assert total==20,f'{total} journal tokens for 20 provider tokens'


@pytest.mark.asyncio
async def test_token_persistence_failure_preserves_paid_response(lab,monkeypatch):
    from backend.app import providers
    store,worker=lab;calls=[]
    store.allow_model('ollama','m','','local')
    workflow=flow();workflow.nodes[1].type='llm'
    workflow.nodes[1].inputs={'prompt':'input.message'}
    workflow.nodes[1].config={'provider':'ollama','model':'m'}
    async def model(*args,usage=None,**kwargs):
        calls.append(1);providers.record_usage(usage,10,0);return 'paid answer'
    monkeypatch.setattr(providers,'ollama_chat',model)
    original=store.record_accounting;writes=[]
    def fail_once(*args):
        if calls:
            writes.append(1)
            if len(writes)==1:raise RuntimeError('injected accounting database outage')
        return original(*args)
    monkeypatch.setattr(store,'record_accounting',fail_once)
    row=store.create_run(workflow.model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(row['id'])
    assert len(calls)==1
    assert saved['status']=='success',f"paid response lost: status={saved['status']}, error={saved.get('error')}"


@pytest.mark.asyncio
async def test_approval_frame_does_not_restore_old_budget_ceiling(lab,monkeypatch):
    from backend.app.approvals import decide
    store,worker=lab
    monkeypatch.setenv('AGENT_RUN_BUDGET','3')
    conn=worker.tools.save_connection({'name':'sink','provider':'http','endpoint':'https://example.com'})
    workflow=calc_flow();workflow.nodes[2].type='tool_http'
    workflow.nodes[2].config={'connection_id':conn['id'],'method':'POST','enable_writes':True,'approval':True}
    scripted(monkeypatch,['{"action":"call","target":"calc","input":"first"}',
                          '{"action":"call","target":"calc","input":"second"}'])
    async def send(*_):return 'sent'
    monkeypatch.setattr(worker.tools,'execute_prepared',send)
    row=store.create_run(workflow.model_dump(mode='json'),'go')
    await worker.execute(store.claim_next(worker.owner))
    pending=store.run(row['id'])['approvals'][0]
    monkeypatch.setenv('AGENT_RUN_BUDGET','1')
    decide(store,row['id'],'local',pending['id'],pending['digest'],'approve')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(row['id'])
    assert saved['accounting']['action_budget']['counter']==1, 'approval frame overrode the current run ceiling'


@pytest.mark.asyncio
async def test_the_same_specialist_called_twice_gets_distinct_identities(monkeypatch):
    """Positional identity must not collide when one specialist is delegated to twice:
    each call sits under the delegation that made it, not just the specialist's own step."""
    from backend.tests.test_agent_team_budget import team_flow,grounded
    from backend.tests.test_agent_grounding import source,platform_with
    scripted(monkeypatch,[json.dumps({'action':'call','target':'spec0','input':'a'}),
                          json.dumps({'action':'call','target':'kb0','input':'q'}),
                          grounded('F [S1]',['S1']),
                          json.dumps({'action':'call','target':'spec0','input':'b'}),
                          json.dumps({'action':'call','target':'kb0','input':'q'}),
                          grounded('G [S2]',['S2']),
                          grounded('Done [S1][S2]',['S1','S2'])])
    ids=[]
    async def emit(event):
        if event.get('invocation_id'):ids.append(event['invocation_id'])
    await compile_workflow(team_flow(1),message='f',platform_resolver=platform_with([source(1)],[]),
                           emit=emit).graph.ainvoke({'values':{}})
    retrievals=[i for i in dict.fromkeys(ids) if i.endswith(':kb0')]
    assert retrievals==['agent:1:spec0>1:kb0','agent:2:spec0>1:kb0'],retrievals
