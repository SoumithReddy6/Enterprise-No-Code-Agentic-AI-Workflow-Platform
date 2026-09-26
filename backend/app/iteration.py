"""Run one attached callable once per element of an array.

The body executes inside this handler rather than as LangGraph nodes: the platform owns
its own leasing, fencing and checkpointing, so per-item state must travel through that
path. Progress is recorded as it happens, so a resumed run skips completed indices
instead of repeating paid work or external writes.
"""
import json
from dataclasses import replace

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


async def run_body(ctx,body,index,text,invocation):
    # Write intent is tracked per item: a completed item must not be held hostage
    # by the enclosing loop's checkpoint, which only appears when every item is done.
    """Invoke one attached callable, mirroring the agent's target-invocation contract."""
    from .registry import REGISTRY
    from .agent_runtime import tool_outputs, execute_agent
    from .tool_service import is_write
    definition=REGISTRY[body.type]
    config=definition.config_model.model_validate(body.config)
    if body.type=='agent':
        return await execute_agent(body.id,text,ctx.workflow,ctx,ctx.emit,0,None,item_owner(ctx,index))
    child=replace(ctx,node_id=body.id,node_type=body.type,checkpoint_owner=item_owner(ctx,index))
    if body.type.startswith('tool_'):
        if ctx.platform is None:raise ValueError('Tool execution unavailable.')
        raw=await ctx.platform('tool',body.type,config.model_dump(),text,child.checkpoint_owner,body.id,invocation)
        return await tool_outputs(ctx,body.type,config,raw,body.id)
    return await definition.handler({'query':text},config,child)


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
        text=item_input(value)
        await emit({'node_id':body.id,'invocation_id':invocation,'parent_node_id':ctx.node_id,
                    'transient':True,'status':'running','inputs':{'input':text[:2000]},'item_index':index})
        try:
            output=await run_body(ctx,body,index,text,invocation)
            entry={'index':index,'status':'success','value':output.get('text',''),'error':''}
            await emit({'node_id':body.id,'invocation_id':invocation,'parent_node_id':ctx.node_id,
                        'transient':True,'status':'success','outputs':output,'item_index':index})
        except ApprovalPause:
            # A pause is not an item failure: swallowing it would disable the approval
            # gate for every write inside a loop.
            raise
        except UncertainWriteError:
            # The external outcome is unknown; iterating past it could duplicate a write.
            raise
        except Exception as exc:
            error=str(exc) if isinstance(exc,ValueError) else 'Item execution failed.'
            entry={'index':index,'status':'failed','value':'','error':error}
            failed+=1
            await emit({'node_id':body.id,'invocation_id':invocation,'parent_node_id':ctx.node_id,
                        'transient':True,'status':'failed','error':error,'item_index':index})
            journal(event='loop.item_failed',node_id=ctx.node_id,reason_code='item_error')
            if config.on_item_error=='stop':
                remember(ctx.run,ctx.node_id,index,entry)
                await emit({'kind':'loop_item','status':'info','node_id':ctx.node_id,'item_index':index,'item_result':entry,'transient':True})
                results.append(entry)
                break
        remember(ctx.run,ctx.node_id,index,entry)
        await emit({'kind':'loop_item','status':'info','node_id':ctx.node_id,'item_index':index,'item_result':entry,'transient':True})
        results.append(entry)
        if not within_budget(results):
            # Keep every outcome; shed only the payloads, so the caller still sees what ran.
            results=[{**r,'value':''} for r in results];dropped=True

    await emit({'kind':'loop_summary','node_id':ctx.node_id,'status':'warning' if (truncated or dropped) else 'info',
                'transient':True,'total':len(items),'processed':len(results),'failed':failed,
                'truncated':truncated,'values_dropped':dropped})
    return {'results':results,'failed':str(failed)}
