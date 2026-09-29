"""Run one attached callable once per element of an array.

The body executes inside this handler rather than as LangGraph nodes: the platform owns
its own leasing, fencing and checkpointing, so per-item state must travel through that
path. Progress is recorded as it happens, so a resumed run skips completed indices
instead of repeating paid work or external writes.
"""
import json

from .execution_policy import ExecutionIdentity,execute_with_policy,invoke_attached,charges_action,accounting_snapshot

MAX_ITEMS_CEILING=1000
MAX_RESULT_BYTES=1_000_000
# Observability events carry previews; the durable item result holds the full value.
PREVIEW_CHARS=2000


def item_owner(ctx,index):
    """Per-item checkpoint owner: '<loop node>:<index>'."""
    return f'{ctx.node_id}:{index}'


def progress_key(node_id):
    return node_id


def completed_indices(run,node_id):
    """Indices already finished in an earlier attempt of this run."""
    stored=(run or {}).get('loop_progress',{}).get(progress_key(node_id),{})
    return {int(index):result for index,result in stored.items()} if isinstance(stored,dict) else {}


def remember(run,node_id,index,result):
    if run is None:return
    run.setdefault('loop_progress',{}).setdefault(progress_key(node_id),{})[str(index)]=result


def item_input(value):
    """Body callables take text. Objects are rendered as compact JSON, never str()."""
    if isinstance(value,str):return value
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),default=str)


def serialized_bytes(value):
    """UTF-8 bytes of the JSON the results output is stored and returned as."""
    return len(json.dumps(value,ensure_ascii=False,default=str).encode('utf-8'))


class ResultBudget:
    """Admit item entries in order while the serialized results list fits MAX_RESULT_BYTES.

    The earliest values are kept. The first value that does not fit, and every value after
    it, is replaced by '' and marked value_dropped: a value is never cut part way, and the
    retained values always form a prefix. Outcome metadata is always kept. The size counts
    the whole list as serialized - brackets, separators and entry structure, not just the
    value text - so the bound is on what is actually stored.

    Restored entries pass through the same admission, so a replay reaches the same
    decisions: fresh entries are admitted before they are persisted, and an entry that
    was dropped is stored already marked.
    """
    def __init__(self):
        self.size=2  # '[]'
        self.count=0
        self.dropping=False

    def admit(self,entry):
        separator=2 if self.count else 0  # json.dumps separates list items with ', '
        if entry.get('value_dropped'):self.dropping=True
        elif entry.get('value'):
            if self.dropping or self.size+separator+serialized_bytes(entry)>MAX_RESULT_BYTES:
                entry={**entry,'value':'','value_dropped':True};self.dropping=True
        self.size+=separator+serialized_bytes(entry);self.count+=1
        return entry


def body_truncation(output):
    """An agent body that stopped early says so in its grounding metadata; '' otherwise."""
    try:metadata=json.loads(output.get('grounding') or '{}')
    except (TypeError,ValueError):return ''
    if not isinstance(metadata,dict) or not metadata.get('truncated'):return ''
    return str(metadata.get('truncation_reason') or 'Agent work was incomplete.')


def completeness(node_id,total,accepted,results,failed,halt=None):
    """Whether the loop delivered every item it was given, and if not, why.

    This is part of the node's output rather than only an event, so it is checkpointed
    with the results and a resumed run reports the same incompleteness at run level.
    halt names why iteration ended early: ('stop', index) after a failed item under
    on_item_error stop, or ('budget', index, reason) when the run-wide action budget
    refused an item before it made any call.
    """
    reasons=[];unrun=accepted-len(results)
    if total>accepted:
        reasons.append(f'{node_id} accepted {accepted} of {total} items (max_items {accepted}); {total-accepted} did not run.')
    stopped=bool(halt and halt[0]=='stop' and unrun>0)
    budget=bool(halt and halt[0]=='budget')
    if stopped:
        reasons.append(f'{node_id} stopped after item {halt[1]} failed; {unrun} accepted items did not run.')
    if budget:
        reasons.append(f'{node_id} stopped at item {halt[1]}: {halt[2]} {unrun} accepted items did not run.')
    partial=[r for r in results if r.get('truncation_reason')]
    if partial:
        reasons.append(f'{node_id}: {len(partial)} item(s) returned incomplete agent work ({partial[0]["truncation_reason"]}).')
    dropped=sum(bool(r.get('value_dropped')) for r in results)
    if dropped:
        reasons.append(f'{node_id} kept item values up to {MAX_RESULT_BYTES} bytes; {dropped} later value(s) were dropped and only their outcomes kept.')
    return {'total':total,'accepted':accepted,'processed':len(results),'failed':failed,
            'limited':total>accepted,'stopped':stopped,'budget_exhausted':budget,'incomplete_items':len(partial),
            'values_dropped':bool(dropped),'dropped_value_count':dropped,
            'truncated':bool(reasons),'truncation_reason':' '.join(reasons),
            'truncation_source':'confirmed' if reasons else ''}


LEGACY_UNVERIFIED='Loop completeness could not be fully verified because this run was checkpointed by an older Relay version'


def reconstructed_summary(node_id,items,config,outputs,body_type):
    """Rebuild the summary of a loop checkpointed before the summary output existed.

    Missing metadata is not evidence of completeness. What is provable is reconstructed;
    anything that is not makes the loop count as incomplete, with truncation_source
    'legacy_checkpoint_unverified' so operators can separate it from confirmed cases.

    * limit and stop: provable from the restored input list, configuration and results.
    * dropped values: a size drop blanks every value present at that moment, including
      the first success, and nothing refills it, so a non-empty first success proves no
      drop happened. With no successes there were no payloads to lose.
    * agent item truncation: never recorded, but only bodies that report grounding can
      produce it.
    """
    from .registry import REGISTRY
    results=outputs.get('results') or []
    failed=sum(r.get('status')=='failed' for r in results)
    unverified=[]
    # A legacy stop is visible as a final failed item with accepted items left over.
    halt=('stop',results[-1].get('index')) if config.on_item_error=='stop' and results and results[-1].get('status')=='failed' else None
    if isinstance(items,list):
        accepted=len(items[:min(config.max_items,MAX_ITEMS_CEILING)])
        summary=completeness(node_id,len(items),accepted,results,failed,halt)
    else:
        summary=completeness(node_id,len(results),len(results),results,failed,halt)
        unverified+=['limited','stopped']
    first=next((r for r in results if r.get('status')=='success'),None)
    if first is not None and first.get('value')=='':unverified.append('values_dropped')
    definition=REGISTRY.get(body_type)
    if definition is None or 'grounding' in definition.outputs:unverified.append('agent_item_truncation')
    summary={**summary,'reconstructed':True,'unverified':unverified}
    if unverified:
        note=f"{LEGACY_UNVERIFIED} ({node_id}; unverified: {', '.join(unverified)})."
        summary.update(truncated=True,truncation_reason=' '.join(filter(None,[summary['truncation_reason'],note])),
                       truncation_source=summary['truncation_source'] or 'legacy_checkpoint_unverified')
    return summary


def record_progress(ctx,index,entry):
    """Persist this item's result and publish run-wide spend to the caller's sink.

    Both happen per item rather than per loop, so an interruption mid-collection resumes
    with its completed items skipped and its budget already consumed.
    """
    remember(ctx.run,ctx.node_id,index,entry)
    sink=(ctx.run or {}).get('accounting_sink')
    if sink is not None:sink.update(accounting_snapshot(ctx.run))


async def for_each_node(inputs,config,ctx):
    items=inputs['items']
    if not isinstance(items,list):raise ValueError('for_each expects a list; connect an array output to items.')
    body=next((n for n in (ctx.workflow.nodes if ctx.workflow else []) if n.id==config.body), None)
    if body is None:raise ValueError('for_each has no attached body to run.')
    emit=ctx.emit or (lambda event: None)

    limit=min(config.max_items,MAX_ITEMS_CEILING)
    selected=items[:limit]
    done=completed_indices(ctx.run,ctx.node_id)
    results=[];failed=0;halt=None;budget=ResultBudget()

    for index,value in enumerate(selected):
        if index in done:
            # Restored from an earlier attempt: it passes through the same admission and
            # policy checks as a fresh item, so replay reaches the same decisions.
            entry=budget.admit(done[index])
        else:
            entry,exhausted=await _run_item(ctx,body,emit,index,value)
            if entry is None:
                # The action budget refused the item before any call: nothing ran, so
                # there is no item result to record, and every later item would be
                # refused the same way.
                halt=('budget',index,exhausted);break
            # Admit before persisting, so progress never holds a value the output drops.
            entry=budget.admit(entry)
            record_progress(ctx,index,entry)
            await emit({'kind':'loop_item','status':'info','node_id':ctx.node_id,'item_index':index,'item_result':entry,'transient':True})
            if exhausted:
                # An earlier attempt ran and failed; the budget then refused its retry.
                results.append(entry);failed+=1
                halt=('budget',index+1,exhausted);break
        results.append(entry)
        if entry.get('status')=='failed':failed+=1
        if entry.get('status')=='failed' and config.on_item_error=='stop':
            halt=('stop',index);break

    summary=completeness(ctx.node_id,len(items),len(selected),results,failed,halt)
    await emit({'kind':'loop_summary','node_id':ctx.node_id,'status':'warning' if summary['truncated'] else 'info',
                'transient':True,**summary})
    return {'results':results,'failed':str(failed),'summary':json.dumps(summary)}


async def _run_item(ctx,body,emit,index,value):
    """Execute one fresh item. Returns (entry, exhaustion reason or '').

    entry is None when the run-wide action budget refused the item before it made any
    call. The caller admits, persists and publishes the entry.
    """
    from .tool_service import UncertainWriteError
    from .approvals import ApprovalPause
    from .observability import journal
    invocation=f'{ctx.node_id}:{index}:{body.id}'
    identity=ExecutionIdentity(body.id,item_owner(ctx,index),invocation,ctx.node_id,index)
    text=item_input(value)
    common={**identity.event_fields(),'transient':True}
    await emit({**common,'status':'running','inputs':{'input':text[:PREVIEW_CHARS]}})
    try:
        async def invoke():
            return await invoke_attached(body,text,ctx,identity,emit,
                                         action_budget=(ctx.run or {}).get('action_budget'))
        outcome=await execute_with_policy(
            invoke,identity,body.retry,ctx.run,emit,generic_error='Item execution failed.',
            action_budget=(ctx.run or {}).get('action_budget'),charge_first_attempt=charges_action(body.type))
        if outcome.budget_exhausted and not outcome.attempts:
            await emit({**common,'status':'not_run','reason':outcome.error})
            journal(event='loop.budget_exhausted',node_id=ctx.node_id,reason_code='action_budget')
            return None,outcome.error
        if outcome.status=='failed':
            entry={'index':index,'status':'failed','value':'','error':outcome.error}
            event={**common,'status':'failed','error':outcome.error}
            if outcome.usage:event['usage']=outcome.usage
            if body.retry.attempts:event['attempt']=outcome.attempts
            await emit(event)
            journal(event='loop.item_failed',node_id=ctx.node_id,reason_code='item_error')
        else:
            output=outcome.outputs
            entry={'index':index,'status':'success','value':output.get('text',''),'error':''}
            reason=body_truncation(output)
            if reason:entry['truncation_reason']=reason
            event={**common,'status':'success','outputs':output}
            if outcome.usage:event['usage']=outcome.usage
            await emit(event)
    except ApprovalPause:
        # A pause is not an item failure: swallowing it would disable the approval
        # gate for every write inside a loop. Usage already spent is still reported.
        from .execution_policy import usage_for
        paused={**common,'status':'paused'};usage=usage_for(ctx.run,identity)
        if usage:paused['usage']=usage
        await emit(paused)
        raise
    except UncertainWriteError:
        # The external outcome is unknown; iterating past it could duplicate a write.
        raise
    return entry,(outcome.error if outcome.budget_exhausted else '')
