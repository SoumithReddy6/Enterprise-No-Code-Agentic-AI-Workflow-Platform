import importlib
import pytest
from backend.app.models import Node,RetryPolicy
from backend.app.registry import Context
from backend.app.execution_policy import ExecutionIdentity,execute_with_policy


def safety():return importlib.import_module('backend.app.request_safety')


@pytest.mark.asyncio
async def test_shared_seam_refuses_before_action_debit_operation_or_retry():
    module=safety();events=[];calls=[]
    async def emit(e):events.append(e)
    async def deny():raise module.UnsafeExecutionError('No eligible receipt')
    async def operation():calls.append(1);return {'text':'sent'}
    budget={'remaining':5,'counter':0,'ceiling':5}
    with pytest.raises(module.UnsafeExecutionError):
        await execute_with_policy(operation,ExecutionIdentity('send','send','send'),RetryPolicy(attempts=2),{},emit,
            action_budget=budget,charge_first_attempt=True,eligibility=deny)
    assert calls==[] and events==[] and budget['counter']==0


@pytest.mark.asyncio
async def test_json_receipt_dictionary_is_not_runtime_authority():
    module=safety();node=Node(id='send',type='tool_http',config={'method':'POST','enable_writes':True})
    ctx=Context('Hello',lambda _: '',run={'contract':2,'run_id':'run1'})
    with pytest.raises(module.UnsafeExecutionError):
        await module.check_execution_eligibility(ExecutionIdentity('send','send','send'),{'input':'{"amount":0}'},
            {'input':{'_verification':{'v':1}}},2,node=node,ctx=ctx)


def test_review_and_denial_are_control_flow_in_every_caller():
    from backend.app.approvals import ReviewPause
    from backend.app.execution_policy import CONTROL_FLOW
    assert ReviewPause in CONTROL_FLOW and safety().UnsafeExecutionError in CONTROL_FLOW


def test_array_schema_supports_verified_loop_input_without_weakening_object_schema():
    from backend.app.extraction_checks import verify_extraction
    from backend.app.provenance import source_context
    from backend.app.request_contracts import ExtractionCheckConfig
    text='[{"amount":0}]'
    settings=ExtractionCheckConfig(expected_schema={'type':'array'},required_fields=['/0/amount'],
        field_rules=[{'field':'/0/amount','kind':'source_field','source_pointer':'/0/amount'}])
    assert verify_extraction(text,text,settings,source_context('input','message',text),None).verdict=='passed'


@pytest.mark.asyncio
async def test_direct_attached_dispatch_cannot_bypass_the_shared_guard():
    from backend.app.execution_policy import invoke_attached
    calls=[]
    async def platform(*args):calls.append(args);return 'sent'
    async def emit(event):pass
    node=Node(id='send',type='tool_http',config={'method':'POST','enable_writes':True})
    ctx=Context('Question',lambda _: '',platform=platform,run={'contract':2,'run_id':'run1'})
    with pytest.raises(safety().UnsafeExecutionError):
        await invoke_attached(node,'invented',ctx,ExecutionIdentity('send','each:0','each:0:send'),emit)
    assert calls==[]


@pytest.mark.parametrize('index,text',[(-1,'{"amount":1200}'),(True,'{"amount":1200}'),(0,'{"amount":1200}'),(2,'{"amount":1200}')])
def test_item_projection_cannot_claim_a_sibling_or_an_invalid_index(index,text):
    module=safety()
    ctx=Context('input',lambda _: '',input_proofs={'items':module.VerifiedBinding('[{"amount":0},{"amount":1200}]',())})
    assert module.item_proofs(ctx,index,text)=={}


def test_item_rendering_preserves_exact_json_numbers():
    from backend.app import exact_json
    from backend.app.iteration import item_input
    assert item_input(exact_json.loads('{"amount":1000.00000000000001}'))=='{"amount":1000.00000000000001}'
