"""Runtime ownership of fact verification, typed review and exact derived eligibility.

Resolved inputs come from callers. This module never reads graph state. No model or
handler metadata can become authority: bindings are rebuilt by runtime code, and human
grants are checked against the fenced approval store each time they are consumed.
"""
from dataclasses import dataclass,replace
import json
import re

from . import exact_json,provenance
from .approvals import ReviewPause
from .extraction_checks import verify_extraction
from .verification_receipts import make_receipt,checked_receipt,receipt_matches,verification_policy_digest,digest

DECISION_KEY='_decision_review'
# Nodes that issue verification receipts. Both are runtime-owned handlers.
VERIFIERS=frozenset({'verify_extraction','request_reader'})


class UnsafeExecutionError(ValueError):
    """Refusal is control flow, never retryable/recoverable or an Agent observation."""


def render(value):
    return value if isinstance(value,str) else exact_json.dumps(value,compact=True)


@dataclass(frozen=True)
class Grant:
    record: dict
    original: str
    value: str
    policy: str
    node_id: str
    payload: dict


@dataclass(frozen=True)
class VerifiedBinding:
    value: object
    grants: tuple[Grant,...]
    decisions: tuple = ()


def resolved_proofs(run,bindings,inputs):
    known=(run or {}).get('eligible_outputs',{})
    return {name:known[ref] for name,ref in bindings.items() if ref in known
        and render(known[ref].value)==render(inputs.get(name))}


async def binding_valid(binding,value,ctx):
    if not isinstance(binding,VerifiedBinding) or render(binding.value)!=render(value) or not (binding.grants or binding.decisions):return False
    for grant in binding.grants:
        if not receipt_matches(grant.record,run_id=ctx.run['run_id'],node_id=grant.node_id,
            original=grant.original,value=grant.value,policy_digest=grant.policy):return False
        review=grant.record['review_id']
        if review:
            if ctx.platform is None:return False
            await ctx.platform('checked_review',review,'verification',grant.payload)
    for decision in binding.decisions:
        if ctx.platform is None:return False
        actual=await ctx.platform('checked_review',decision['id'],'decision',decision['payload'])
        if actual['choice']!=decision['choice']:return False
    return True


def _origins(ctx,node,inputs):
    origins=ctx.input_origins if isinstance(ctx.input_origins,provenance.EvidenceContext) else provenance.EvidenceContext(complete=False)
    origins=replace(origins,tenant_id=ctx.run.get('tenant_id','local'))
    if 'reference' in inputs:
        source,_,port=node.inputs.get('reference','').partition('.')
        reader=next((n for n in ctx.workflow.nodes if n.id==source),None)
        if reader:
            reference=provenance.reference_context(reader,port,inputs['reference'],tenant_id=origins.tenant_id)
            origins=replace(origins,reference_sources=reference.reference_sources)
    return origins


def _checked_inputs(node,inputs,outputs):
    """The (original, value) pair a verifier checks. The reader checks its own reading."""
    if node.type!='request_reader':return inputs
    return {'original':inputs['message'],'value':outputs['text'],**({'reference':inputs['reference']} if 'reference' in inputs else {})}


def _verification(ctx,node,inputs,config):
    if inputs['original']!=ctx.message:raise UnsafeExecutionError('Verification does not match this run original input.')
    origins=_origins(ctx,node,inputs)
    report=verify_extraction(inputs['original'],inputs['value'],config,origins,inputs.get('reference'))
    # Leave room for the receipt envelope and a maximal review id. Never issue a
    # partial pass merely because its audit representation was shortened.
    if len(report.model_dump_json().encode())>6000:
        from .request_contracts import VerificationReport
        fields=report.model_dump(mode='json')['fields']
        while len(json.dumps(fields,ensure_ascii=False).encode())>5000:fields=fields[:-1]
        fields=[f for f in fields if f['field']!='']+[{'field':'','status':'unverified','reason':'receipt_limit'}]
        report=VerificationReport(verdict='needs_review',complete=False,fields=fields[:64])
    # Policy includes graph bindings and the frozen workflow, not only field settings.
    policy=digest(json.dumps({'rules':verification_policy_digest(config,origins),
        'node_id':node.id,'bindings':node.inputs,'workflow':ctx.workflow.model_dump(mode='json')},
        sort_keys=True,ensure_ascii=False,separators=(',',':')))
    payload={'kind':'verification','node_id':node.id,'original':inputs['original'],'value':inputs['value'],
             'policy_digest':policy,'report':report.model_dump(mode='json')}
    return report,policy,payload


def _reader_verification(ctx,node,inputs,value,config,answer):
    report,policy,payload=_verification(ctx,node,_checked_inputs(node,inputs,{'text':value}),config.checks())
    # The model's reading type is part of what a person confirms.
    return report,policy,{**payload,'request_type':answer.type}


async def _review(ctx,kind,payload):
    if ctx.platform is None:raise ReviewPause(ctx.execution_identity,kind,payload)
    return await ctx.platform('review',kind,payload,ctx.execution_identity)


async def verify_extraction_node(inputs,config,ctx):
    node=next(n for n in ctx.workflow.nodes if n.id==ctx.node_id)
    report,policy,payload=_verification(ctx,node,inputs,config)
    review=None
    if report.verdict=='needs_review':review=(await _review(ctx,'verification',payload))['id']
    receipt=make_receipt(ctx.run['run_id'],node.id,inputs['original'],inputs['value'],policy,report,review)
    grant=Grant(receipt,inputs['original'],inputs['value'],policy,node.id,payload)
    ctx.run.setdefault('issued_verification',{})[ctx.execution_identity.invocation_id]=grant
    branch='reply' if report.verdict=='clarify' else 'continue'
    from .observability import journal
    journal(event='verification.result',node_id=node.id,reason_code=report.verdict,manual=review is not None)
    return {'text':inputs['value'],'branch':branch,'reply':
        'Missing or invalid facts. Submit a new request with the required information.' if branch=='reply' else '',
        'report':report.model_dump_json()}


async def request_reader_node(inputs,config,ctx):
    from .request_reader import prepared_reply,read_request,outcome,reading_text,PREPARED
    from .observability import journal
    node=next(n for n in ctx.workflow.nodes if n.id==ctx.node_id)
    if inputs['message']!=ctx.message:raise UnsafeExecutionError('The request reader does not match this run original input.')
    reply=prepared_reply(inputs['message'],config)
    if reply is not None:
        journal(event='request_reader.prepared',node_id=node.id,reason_code='prepared')
        return {'text':'','branch':'reply','reply':reply,'report':'','reading':PREPARED}
    # A review pause keeps the validated reading, so resuming never asks the model again.
    from .request_reader import validate_reading,reading_record
    from .approvals import ApprovalPause
    frame_key=node.id+':reader'
    frame=(ctx.run.get('agent_frames') or {}).pop(frame_key,None)
    answer=None
    if isinstance(frame,dict) and frame.get('checkpoint_owner')==node.id:
        try:
            answer=validate_reading(frame.get('reading'),config)
            if frame.get('record')!=reading_record({'reading':frame['reading']},inputs['message'],config):answer=None
        except ValueError:answer=None
    if answer is None:answer=await read_request(inputs['message'],config,ctx)
    value=exact_json.dumps(answer.values)
    report,policy,payload=_reader_verification(ctx,node,inputs,value,config,answer)
    branch,reply,person=outcome(answer,report,config)
    try:review=(await _review(ctx,'verification',payload))['id'] if person else None
    except ApprovalPause as exc:
        text=reading_text(answer)
        exc.frames[frame_key]={'checkpoint_owner':node.id,'reading':text,
            'record':reading_record({'reading':text},inputs['message'],config)}
        raise
    receipt=make_receipt(ctx.run['run_id'],node.id,inputs['message'],value,policy,report,review)
    ctx.run.setdefault('issued_verification',{})[ctx.execution_identity.invocation_id]=Grant(receipt,inputs['message'],value,policy,node.id,payload)
    journal(event='verification.result',node_id=node.id,reason_code=report.verdict,manual=review is not None)
    return {'text':value,'branch':branch,'reply':reply,'report':report.model_dump_json(),'reading':reading_text(answer)}


def _decision_payload(ctx,node,inputs):
    try:
        if node.type=='condition':
            from .conditions import ConditionConfig,evaluate
            proposed=evaluate(inputs['value'],ConditionConfig.model_validate(node.config))
        else:
            from .calculate import run
            proposed,_=run(node.config['expression'],inputs['value'],'check')
        check={'proposed_choice':proposed,'error':''}
    except ValueError as exc:check={'proposed_choice':None,'error':str(exc)[:300]}
    return {'kind':'decision','node_id':node.id,'original':ctx.message,'check':check,
        'inputs':{key:render(value) for key,value in inputs.items()},'policy':node.config,
        'workflow_digest':digest(json.dumps(ctx.workflow.model_dump(mode='json'),sort_keys=True,ensure_ascii=False,separators=(',',':')))}


def decision_record(decision):
    from .approvals import digest as payload_digest
    return {'id':decision['id'],'choice':decision['choice'],'payload_digest':payload_digest(decision['payload'])}


async def checked_control_path(ctx):
    for review in ctx.run.get('control_reviews',{}).values():
        if ctx.platform is None:raise UnsafeExecutionError('Manual execution path has no durable confirmation.')
        await ctx.platform('checked_review',review['id'],review['kind'],review['payload'])


SWITCH_REFUSAL='Route on checked values, or use a Condition, which supports review.'


async def check_execution_eligibility(identity,resolved_inputs,receipts,workflow_contract,*,node,ctx):
    if node.type=='switch' and workflow_contract!=2:raise UnsafeExecutionError('Switch requires a contract-2 workflow.')
    if workflow_contract!=2:return None
    from .compiler import is_write_node,unchecked_model_input
    proofs=receipts if isinstance(receipts,dict) else {}
    write=is_write_node(node,None)
    decision=node.type=='condition' or node.type=='calculate' and node.config.get('mode')=='check'
    if write or decision:await checked_control_path(ctx)
    consumed='input' if node.type.startswith('tool_') else 'value'
    valid=await binding_valid(proofs.get(consumed),resolved_inputs.get(consumed),ctx)
    if write:
        # A human choice can enable this path without being part of its payload's
        # data lineage. Both authorities must remain valid at the action boundary.
        # A dictionary with a well-formed checksum is not a runtime-issued binding.
        if not valid:raise UnsafeExecutionError('External write requires an exact eligible verification binding.')
        return None
    model_input=bool(ctx.workflow and any(unchecked_model_input(ref,{n.id:n for n in ctx.workflow.nodes}) for ref in node.inputs.values()))
    if node.type=='switch':
        # Checked or trusted values only; a Switch never creates a review of its own. The
        # lineage walk is validation's (switch_input_allowed): it stops at independent reads.
        await checked_control_path(ctx)
        from .platform_graph import split_graph
        _,deps,_=split_graph(ctx.workflow)
        nodes={n.id:n for n in ctx.workflow.nodes}
        from_model=any(unchecked_model_input(ref,nodes,deps,stop_at_sources=True) for ref in node.inputs.values())
        if valid or not from_model and ctx.input_label in provenance.TRUSTED:return None
        raise UnsafeExecutionError(SWITCH_REFUSAL)
    if not decision:return None
    needs_review=not valid and (model_input or ctx.input_label not in provenance.TRUSTED)
    if node.type=='calculate' and node.config.get('mode')=='check':
        from .calculate import run
        try:passed,_=run(node.config['expression'],resolved_inputs['value'],'check')
        except ValueError:passed=None
        needs_review|=passed is not True
    if not needs_review:return None
    payload=_decision_payload(ctx,node,resolved_inputs)
    confirmed=await _review(ctx,'decision',payload)
    ctx.run.setdefault('decision_reviews',{})[identity.invocation_id]={'id':confirmed['id'],'payload':payload,'choice':confirmed['choice']}
    return {'branch':'true' if confirmed['choice'] else 'false'} if node.type=='condition' else {
        'text':exact_json.dumps({'result':confirmed['choice']})}


async def guard(ctx,node,inputs,identity=None):
    identity=identity or ctx.execution_identity
    return await check_execution_eligibility(identity,inputs,ctx.input_proofs,
        (ctx.run or {}).get('contract',1),node=node,ctx=ctx)


async def finalize(ctx,node,inputs,outputs,config,*,cached=False):
    """Issue/restore authority independently of the handler's public result."""
    if ctx.run.get('contract')!=2:return outputs
    allowed={'_provenance','_truncation'}
    if node.type=='request_router':allowed.add('_routing')
    if node.type in VERIFIERS:allowed.add('_verification')
    if node.type=='request_reader':allowed.add('_reading')
    if node.type in ('condition','calculate'):allowed.add(DECISION_KEY)
    if any(k.startswith('_') and k not in allowed for k in outputs):raise UnsafeExecutionError('Unexpected reserved checkpoint metadata.')
    if cached and node.type in provenance.SOURCE_NODES and outputs.get('message')!=ctx.message:
        raise UnsafeExecutionError(f'{node.id} cannot be restored: its trigger no longer matches this run original input.')
    answer=None
    if node.type=='request_reader':
        from .request_reader import PREPARED,prepared_reply,validate_reading,outcome
        if inputs['message']!=ctx.message:raise UnsafeExecutionError('The request reader does not match this run original input.')
        if outputs.get('reading')==PREPARED:
            # A prepared reply asks no model and issues no receipt: it can only reply.
            reply=prepared_reply(inputs['message'],config)
            if reply is None or '_verification' in outputs or ctx.run.get('issued_verification',{}).get(ctx.execution_identity.invocation_id) or any(
                    outputs.get(k)!=v for k,v in {'text':'','branch':'reply','reply':reply,'report':''}.items()):
                raise UnsafeExecutionError('Prepared reply does not match the saved configuration.')
            return outputs
        try:answer=validate_reading(outputs.get('reading'),config)
        except ValueError:raise UnsafeExecutionError('Request reader outputs do not hold a valid reading.') from None
        if outputs.get('text')!=exact_json.dumps(answer.values):raise UnsafeExecutionError('Request reader values differ from its reading.')
    if node.type in VERIFIERS:
        checked=_checked_inputs(node,inputs,outputs)
        if cached:
            report,policy,payload=_reader_verification(ctx,node,inputs,outputs['text'],config,answer) if answer else _verification(ctx,node,inputs,config)
            record=checked_receipt(outputs.get('_verification'))
            if record['run_id']!=ctx.run['run_id'] or record['report']!=report.model_dump(mode='json') or record['node_id']!=node.id or record['policy_digest']!=policy or record['input_digest']!=digest(checked['original']) or record['value_digest']!=digest(checked['value']):
                raise UnsafeExecutionError('Verification checkpoint no longer matches input, value or policy.')
            grant=Grant(record,checked['original'],checked['value'],policy,node.id,payload)
        else:
            grant=ctx.run.get('issued_verification',{}).pop(ctx.execution_identity.invocation_id,None)
            if grant is None:raise UnsafeExecutionError('Verifier did not issue a runtime receipt.')
            outputs={**outputs,'_verification':grant.record}
        expected='reply' if grant.record['report']['verdict']=='clarify' else 'continue'
        if outputs.get('branch')!=expected or outputs.get('text')!=checked['value'] or outputs.get('report')!=report_json(grant.record['report']):
            raise UnsafeExecutionError('Verification outputs disagree with their checked result.')
        if answer is not None:
            from .request_reader import outcome
            from .request_contracts import VerificationReport
            branch,reply,person=outcome(answer,VerificationReport.model_validate(grant.record['report']),config)
            if (branch,reply)!=(outputs['branch'],outputs['reply']):raise UnsafeExecutionError('Request reader outputs disagree with its decision table.')
            # The model's type alone never continues a request a person must confirm.
            if person and not grant.record['review_id']:raise UnsafeExecutionError('This request requires a person to confirm it.')
        if expected=='continue':
            binding=VerifiedBinding(outputs['text'],(grant,))
            if not await binding_valid(binding,outputs['text'],ctx):raise UnsafeExecutionError('Verification confirmation is unavailable.')
            ctx.run.setdefault('eligible_outputs',{})[node.id+'.text']=binding
            if grant.record['review_id']:
                ctx.run.setdefault('control_reviews',{})[node.id]={'id':grant.record['review_id'],'kind':'verification','payload':grant.payload}
    # Restore/attach the human choice before deterministic derivation: a deliberately
    # chosen false is allowed to differ from the computed comparison.
    if cached and (node.type=='condition' or node.type=='calculate' and config.mode=='check'):
        await checked_control_path(ctx)
    decision=ctx.run.get('decision_reviews',{}).get(ctx.execution_identity.invocation_id)
    if decision is not None:outputs={**outputs,DECISION_KEY:decision_record(decision)}
    if cached and DECISION_KEY in outputs:
        from .approvals import digest as payload_digest
        saved=outputs[DECISION_KEY];payload=_decision_payload(ctx,node,inputs)
        if not isinstance(saved,dict) or set(saved)!={'id','payload_digest','choice'} or not isinstance(saved['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',saved['id']) or type(saved['choice']) is not bool or saved['payload_digest']!=payload_digest(payload) or ctx.platform is None:
            raise UnsafeExecutionError('Malformed decision review checkpoint.')
        actual=await ctx.platform('checked_review',saved['id'],'decision',payload)
        if actual['choice']!=saved['choice']:raise UnsafeExecutionError('Decision review choice changed.')
        decision={'id':actual['id'],'choice':actual['choice'],'payload':payload}
        expected={'branch':'true' if actual['choice'] else 'false'} if node.type=='condition' else {'text':exact_json.dumps({'result':actual['choice']})}
        if any(outputs.get(k)!=v for k,v in expected.items()):raise UnsafeExecutionError('Restored decision differs from confirmed choice.')
    elif cached and (node.type=='condition' or node.type=='calculate' and config.mode=='check'):
        # An unchecked old decision must not become an automatic branch on replay.
        checked=await guard(ctx,node,inputs)
        if checked is not None:raise UnsafeExecutionError('Restored decision lacks its recorded human confirmation.')
    if decision is not None:
        ctx.run.setdefault('control_reviews',{})[node.id]={'id':decision['id'],'kind':'decision','payload':decision['payload']}
    elif node.type=='condition':
        from .conditions import evaluate
        expected='true' if evaluate(inputs['value'],config) else 'false'
        if outputs.get('branch')!=expected:raise UnsafeExecutionError('Condition result does not match its checked input and policy.')
    if node.type=='switch':
        # A restored route is eligible again and equals the route its input and cases select.
        if cached:await guard(ctx,node,inputs)
        from .switch import choose
        if outputs.get('branch')!=choose(inputs['value'],config):raise UnsafeExecutionError('Switch route does not match its checked input and cases.')
    if node.type in ('prompt','calculate'):
        parents=[p for key,p in (ctx.input_proofs or {}).items() if await binding_valid(p,inputs.get(key),ctx)]
        if parents:
            # Manual check choices differ deliberately from the computed comparison.
            if DECISION_KEY not in outputs:
                if node.type=='prompt':expected=config.template.format_map(inputs)
                else:
                    from .calculate import run
                    value,_=run(config.expression,inputs['value'],config.mode);expected=exact_json.dumps({'result':value})
                if outputs.get('text')!=expected:raise UnsafeExecutionError('Derived output changed after its checked inputs.')
                roots=tuple(g for p in parents for g in p.grants)
                reviews=tuple(d for p in parents for d in p.decisions)
                ctx.run.setdefault('eligible_outputs',{})[node.id+'.text']=VerifiedBinding(expected,roots,reviews)
            else:
                roots=tuple(g for p in parents for g in p.grants)
                reviews=tuple(d for p in parents for d in p.decisions)+(decision,)
                ctx.run.setdefault('eligible_outputs',{})[node.id+'.text']=VerifiedBinding(outputs['text'],roots,reviews)
    return outputs


def report_json(value):
    from .request_contracts import VerificationReport
    return VerificationReport.model_validate(value).model_dump_json()


def item_proofs(ctx,index,text):
    if type(index) is not int or index<0:return {}
    parent=(ctx.input_proofs or {}).get('items')
    if not isinstance(parent,VerifiedBinding):return {}
    try:
        collection=exact_json.loads(parent.value,reject_duplicates=True) if isinstance(parent.value,str) else parent.value
        if not isinstance(collection,list) or index>=len(collection) or render(collection[index])!=text:return {}
    except (ValueError,TypeError):return {}
    return {'input':VerifiedBinding(text,parent.grants,parent.decisions)}
