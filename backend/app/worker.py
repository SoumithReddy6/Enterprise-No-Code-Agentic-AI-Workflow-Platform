"""Separate worker with renewable leases and durable per-node results."""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from .storage import Store, local_key, new_id
from .models import Workflow
from .compiler import compile_workflow,PersistedNodeCancellation
from .tool_service import ToolService,CONFIGS,is_write,UncertainWriteError
from .agent_memory import MemoryService
from .platform_validation import platform_errors,kb_errors
from .kb_gateway import KnowledgeServices
from . import approvals
from .execution_policy import ExecutionIdentity

RUN_TIMEOUT_SECONDS=120
from .observability import journal,request_id,run_id,tenant_id

def run_truncation(workflow,values):
    """Fold every node's incompleteness into the run's truncated flag, reason and source.

    Agents report it in grounding, loops in summary; both are outputs, so values restored
    from checkpoints on resume report it too. Any node may also report it under
    TRUNCATION_KEY in its graph value, as a node recovering from budget exhaustion does.
    A confirmed cause anywhere in the run outranks unverifiable legacy completeness.
    """
    from .execution_policy import TRUNCATION_KEY
    reasons=[];sources=set()
    loops={n.id for n in workflow.nodes if n.type=='for_each'}
    for node_id,outputs in values.items():
        field,default=('summary','A loop did not run every item.') if node_id in loops else ('grounding','Agent work was incomplete.')
        for metadata in (json.loads(outputs.get(field) or '{}'),outputs.get(TRUNCATION_KEY) or {}):
            if metadata.get('truncated'):
                reasons.append(metadata.get('truncation_reason') or default)
                sources.add(metadata.get('truncation_source') or 'confirmed')
    return {'truncated':bool(reasons),'truncation_reason':'; '.join(dict.fromkeys(reasons)),
            'truncation_source':'confirmed' if 'confirmed' in sources else next(iter(sources),'')}


class Worker:
    def __init__(self,store,concurrency=4):
        self.store=store;self.owner=new_id();self.concurrency=concurrency;self.tools=ToolService(store);self.memory=MemoryService(store);self.kbs=KnowledgeServices()

    async def execute(self,run):
        tokens=[(request_id,request_id.set(run.get('request_id'))),(run_id,run_id.set(run['id'])),(tenant_id,tenant_id.set(run.get('tenant_id')))]
        try:return await self._execute(run)
        finally:
            for var,token in reversed(tokens):var.reset(token)

    async def _execute(self,run):
        id=run['id'];owner=run.get('_claim_owner',self.owner);started=time.perf_counter()
        journal(event='run.start',run_id=id,tenant=run.get('tenant_id'),workflow=run['workflow'].get('name'),nodes=len(run['workflow'].get('nodes',[])),resumed=bool(run.get('checkpoints')))
        accounting={}
        async def persist_accounting(snapshot):
            accounting.update(snapshot)
            await asyncio.to_thread(self.store.reserve_accounting,id,owner,snapshot)
        async def action_settled(invocation):
            return await asyncio.to_thread(self.store.completed_action,id,invocation) is not None
        async def persist_spend(snapshot):
            accounting.update(snapshot)
            await asyncio.to_thread(self.store.record_accounting,id,owner,snapshot)
        async def emit(event):
            write=asyncio.create_task(asyncio.to_thread(self.store.worker_event,id,owner,event));cancelled=False
            while True:
                try:
                    persisted=await asyncio.shield(write);break
                except asyncio.CancelledError:
                    if write.cancelled():raise
                    cancelled=True
            if not persisted:raise asyncio.CancelledError()
            if accounting and event.get('node_id') and event['status'] in ('success','failed','info'):
                try:
                    await asyncio.to_thread(self.store.record_accounting,id,owner,dict(accounting))
                except Exception as exc:
                    # The node's result is already checkpointed by the event write above, so
                    # a resume reuses it; an accounting write failing here must not turn a
                    # completed node into a failed run.
                    journal(event='accounting.persist_failed',node_id=event.get('node_id'),
                            reason_code='post_event',error_type=type(exc).__name__)
            if event.get('node_id') and event['status']!='running':
                journal(event='node.'+event['status'],run_id=id,node_id=event['node_id'],transient=event.get('transient',False),cached=event.get('cached',False),duration_ms=event.get('duration_ms'),usage=event.get('usage'),error=event.get('error'))
            if cancelled:
                if event.get('node_id') and event['status'] in ('success','failed','cancelled') and not event.get('transient'):
                    raise PersistedNodeCancellation()
                raise asyncio.CancelledError()
        async def graph_run():
            workflow=Workflow.model_validate(run['workflow'])
            tenant_id=self.store.run_tenant(id,owner)
            self.store.check_resume_writes(run)
            from . import sandbox
            # Checked again here: Docker may have stopped after the run was submitted.
            errors=self.store.model_errors(workflow,tenant_id)+platform_errors(self.store,workflow,tenant_id)+await kb_errors(self.kbs,workflow,tenant_id,run.get('checkpoints'),run.get('vector_dependencies'))+await sandbox.preflight_errors(workflow)
            if errors:raise ValueError('; '.join(errors))
            resolver=lambda credential_id:self.store.resolve_run_credential(id,owner,credential_id)
            async def platform_resolver(action,*args):
                tenant=self.store.run_tenant(id,owner)
                try:
                    if action=='tool':
                        node_type,settings,input_text,identity=args
                        if not isinstance(identity,ExecutionIdentity):
                            raise ValueError('Tool execution requires a typed execution identity.')
                        checkpoint_owner=identity.checkpoint_owner
                        config=CONFIGS[node_type].model_validate(settings)
                        self.tools.check(config,tenant)
                        write=is_write(node_type,config)
                        if node_type=='tool_jira' and not write:
                            from .tool_service import jira_items
                            prepared=self.tools.prepare(node_type,settings,input_text,tenant)
                            text=await self.tools.execute_prepared(prepared,tenant)
                            return {'text':text,**jira_items(text,config.operation,prepared['connection']['endpoint'])}
                        invocation=identity.invocation_id
                        node_id=identity.node_id
                        required=write and (config.approval if config.approval is not None else run.get('approval_required',True))
                        if required:
                            saved=approvals.resolve(self.store,id,owner,invocation)
                            if saved is None:
                                prepared=self.tools.prepare(node_type,settings,input_text,tenant)
                                raise approvals.ApprovalPause(node_id,checkpoint_owner,invocation,prepared)
                            candidate=self.tools.prepare(node_type,settings,input_text,tenant)
                            if candidate['payload']!=saved['prepared']['payload'] or candidate['connection_id']!=saved['prepared']['connection_id']:
                                raise ValueError('The resumed action differs from the reviewed request. Start a new run.')
                            if saved['status']=='completed':return saved['result']
                            self.tools.validate_prepared(saved['prepared'],tenant)
                            approvals.begin(self.store,id,owner,saved['id'])
                            try:
                                result=await self.tools.execute_prepared(saved['prepared'],tenant)
                                approvals.complete(self.store,id,owner,saved['id'],result)
                                return result
                            except Exception:
                                raise UncertainWriteError('Approved write outcome is uncertain; reconciliation is required before another attempt.') from None
                        # Intent is recorded per action, not per node, so one completed
                        # write does not leave a sibling action looking unresolved.
                        marker=invocation if write else checkpoint_owner
                        if write:
                            done=self.store.completed_action(id,marker)
                            if done is not None:return done  # Already sent; never send again.
                            self.store.mark_write(id,owner,marker)
                        try:
                            result=await self.tools.execute(node_type,settings,input_text,tenant)
                        except Exception:
                            if write:
                                raise UncertainWriteError('External write outcome is uncertain; reconciliation is required before trying again. Check the remote system.') from None
                            raise
                        if write:self.store.settle_write(id,owner,marker,result)
                        return result
                    if action=='kb_retrieve':return await self.kbs.retrieve(*args,tenant)
                    if action=='verify_vector_sources':return await self.kbs.verify(args[0],tenant)
                    if action=='record_vector_sources':
                        checkpoint_owner,sources=args
                        return self.store.record_vector_dependencies(id,owner,checkpoint_owner,await self.kbs.verify(sources,tenant))
                    if action=='memory_read':return self.memory.read(*args,tenant)
                    if action=='memory_write':return self.memory.write(*args,tenant)
                except KeyError:raise ValueError('Platform resource is unavailable in this workspace.') from None
                raise ValueError('Unsupported platform operation.')
            async def validate_cached(node,outputs):
                results=outputs.get('results')
                if isinstance(results,dict) and results.get('unavailable'):raise ValueError(results['unavailable'])
                issues=await kb_errors(self.kbs,workflow,self.store.run_tenant(id,owner),{node.id:outputs},run.get('vector_dependencies'))
                if issues:raise ValueError('; '.join(issues))
            graph=compile_workflow(workflow,resolver,emit,run['message'],completed=run.get('checkpoints',{}),authorize_model=lambda config:self.store.authorize_run_model(id,owner,config),validate_cached=validate_cached,platform_resolver=platform_resolver,citation_counter=run.get('citation_counter',0),agent_frames=run.get('agent_frames',{}),loop_progress=run.get('loop_progress',{}),accounting=run.get('accounting',{}),accounting_sink=accounting,persist_accounting=persist_accounting,persist_spend=persist_spend,action_settled=action_settled).graph
            result=await graph.ainvoke({'values':{}},{'recursion_limit':150})
            return {'output':'\n\n'.join(result['values'][n.id]['text'] for n in workflow.nodes if n.type=='response' and n.id in result['values']),
                    **run_truncation(workflow,result['values'])}
        async def bounded_run():
            # The deadline covers knowledge-service preflight as well as graph execution.
            async with asyncio.timeout(RUN_TIMEOUT_SECONDS):
                try:return await graph_run()
                except PersistedNodeCancellation:raise asyncio.CancelledError() from None
        task=asyncio.create_task(bounded_run())
        try:
            while not task.done():
                if await asyncio.to_thread(self.store.cancel_requested,id,owner) or not await asyncio.to_thread(self.store.heartbeat,id,owner):
                    task.cancel();break
                await asyncio.wait({task},timeout=.2)
            output=await task
            self.store.finish_run(id,owner,'success',**output)
            journal(event='run.finish',run_id=id,status='success',seconds=round(time.perf_counter()-started,3))
        except approvals.ApprovalPause as exc:
            try:
                approvals.pause(self.store,id,owner,exc)
                journal(event='run.awaiting_approval',run_id=id)
            except Exception as pause_error:
                cancelled=self.store.cancel_requested(id,owner)
                self.store.finish_run(id,owner,'cancelled' if cancelled else 'failed',error='' if cancelled else 'Unable to persist approval request.')
                journal(event='approval.pause_failure',run_id=id,error_type=type(pause_error).__name__)
        except asyncio.CancelledError:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            cancelled=self.store.cancel_requested(id,owner)
            if cancelled:self.store.finish_run(id,owner,'cancelled')
            else:self.store.release(id,owner)
            journal(event='run.finish',run_id=id,status='cancelled' if cancelled else 'released',seconds=round(time.perf_counter()-started,3))
        except Exception as exc:
            error=f'Run timed out after {RUN_TIMEOUT_SECONDS} seconds.' if isinstance(exc,TimeoutError) else str(exc) if isinstance(exc,ValueError) else 'Run failed unexpectedly.'
            journal(event='run.failure',error_type=type(exc).__name__)
            self.store.finish_run(id,owner,'failed',error=error)
            journal(event='run.finish',run_id=id,status='failed',error=error,seconds=round(time.perf_counter()-started,3))

    async def serve(self):
        active=set();last_seen=0
        try:
            while True:
                for task in list(active):
                    if task.done():
                        active.remove(task)
                        try:task.result()
                        except asyncio.CancelledError:pass
                        except Exception as exc:journal(event='worker.execution_failure',error_type=type(exc).__name__)
                try:
                    if time.monotonic()-last_seen>=5:
                        await asyncio.to_thread(self.store.worker_seen,self.owner)
                        await asyncio.to_thread(approvals.expire_pending,self.store)
                        last_seen=time.monotonic()
                    while len(active)<self.concurrency:
                        claim_owner=new_id()
                        job=await asyncio.to_thread(self.store.claim_next,claim_owner)
                        if not job:break
                        job['_claim_owner']=claim_owner
                        active.add(asyncio.create_task(self.execute(job)))
                except Exception as exc:
                    journal(event='worker.poll_failure',worker_id=self.owner,error_type=type(exc).__name__)
                    await asyncio.sleep(1)
                await asyncio.sleep(.1)
        finally:
            for task in active:task.cancel()
            await asyncio.gather(*active,return_exceptions=True)

async def main():
    from dotenv import load_dotenv
    load_dotenv();logging.basicConfig(level=logging.INFO,format='%(message)s')  # JSON lines from relay.run; plain text elsewhere.
    path=Path(os.environ.get('DATA_DIR','.data'));path.mkdir(parents=True,exist_ok=True,mode=0o700)
    store=Store(os.environ.get('DATABASE_URL',f'sqlite:///{path}/workflows.db'),local_key(path))
    print('Relay durable worker started (4 slots).',flush=True)
    try:await Worker(store).serve()
    finally:store.engine.dispose()

if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
