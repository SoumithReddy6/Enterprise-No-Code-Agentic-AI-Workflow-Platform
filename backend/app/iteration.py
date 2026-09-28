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


def within_budget(results):
    return len(json.dumps(results,ensure_ascii=False,default=str).encode())<=MAX_RESULT_BYTES


def record_progress(ctx,index,entry):
    """Persist this item's result and publish run-wide spend to the caller's sink.

    Both happen per item rather than per loop, so an interruption mid-collection resumes
    with its completed items skipped and its budget already consumed.
    """
    remember(ctx.run,ctx.node_id,index,entry)
    sink=(ctx.run or {}).get('accounting_sink')
    if sink is not None:sink.update(accounting_snapshot(ctx.run))


async def for_each_node(inputs,config,ctx):
    from .tool_service import UncertainWriteError
    from .approvals import ApprovalPause
    from .observability import journal
    items=inputs['items']
    if not isinstance(items,list):raise ValueError('for_each expects a list; connect an array output to items.')
    body=next((n for n in (ctx.workflow.nodes if ctx.workflow else []) if n.id==config.body), None)
    if body is None:raise ValueError('for_each has no attached body to run.')
    emit=ctx.emit or (lambda event: None)

    limit=min(config.max_items,MAX_ITEMS_CEILING)
    truncated=len(items)>limit
    selected=items[:limit]
    done=completed_indices(ctx.run,ctx.node_id)
    results=[];failed=0;dropped=False

    for index,value in enumerate(selected):
        if index in done:
            results.append(done[index])
            if done[index].get('status')=='failed':failed+=1
            continue
        invocation=f'{ctx.node_id}:{index}:{body.id}'
        identity=ExecutionIdentity(body.id,item_owner(ctx,index),invocation,ctx.node_id,index)
        text=item_input(value)
        common={**identity.event_fields(),'transient':True}
        await emit({**common,'status':'running','inputs':{'input':text[:2000]}})
        try:
            async def invoke():
                return await invoke_attached(body,text,ctx,identity,emit,
                                             action_budget=(ctx.run or {}).get('action_budget'))
            outcome=await execute_with_policy(
                invoke,identity,body.retry,ctx.run,emit,generic_error='Item execution failed.',
                action_budget=(ctx.run or {}).get('action_budget'),charge_first_attempt=charges_action(body.type))
            if outcome.status=='failed':
                entry={'index':index,'status':'failed','value':'','error':outcome.error}
                failed+=1
                event={**common,'status':'failed','error':outcome.error}
                if outcome.usage:event['usage']=outcome.usage
                if body.retry.attempts:event['attempt']=outcome.attempts
                await emit(event)
                journal(event='loop.item_failed',node_id=ctx.node_id,reason_code='item_error')
                if config.on_item_error=='stop':
                    record_progress(ctx,index,entry)
                    await emit({'kind':'loop_item','status':'info','node_id':ctx.node_id,'item_index':index,'item_result':entry,'transient':True})
                    results.append(entry)
                    break
            else:
                output=outcome.outputs
                entry={'index':index,'status':'success','value':output.get('text',''),'error':''}
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
        record_progress(ctx,index,entry)
        await emit({'kind':'loop_item','status':'info','node_id':ctx.node_id,'item_index':index,'item_result':entry,'transient':True})
        results.append(entry)
        if not within_budget(results):
            # Keep every outcome; shed only the payloads, so the caller still sees what ran.
            results=[{**r,'value':''} for r in results];dropped=True

    await emit({'kind':'loop_summary','node_id':ctx.node_id,'status':'warning' if (truncated or dropped) else 'info',
                'transient':True,'total':len(items),'processed':len(results),'failed':failed,
                'truncated':truncated,'values_dropped':dropped})
    return {'results':results,'failed':str(failed)}
