"""Separate worker with renewable leases and durable per-node results."""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from .storage import Store, local_key, new_id
from .models import Workflow
from .compiler import compile_workflow
from .tool_service import ToolService,CONFIGS,is_write
from .agent_memory import MemoryService
from .platform_validation import platform_errors,kb_errors
from .kb_gateway import KnowledgeServices

RUN_TIMEOUT_SECONDS=120
log=logging.getLogger('relay.run')

def journal(**fields):
    """One JSON line per lifecycle event, always keyed by run_id; inputs and outputs stay in the run record."""
    log.info(json.dumps(fields,ensure_ascii=False,default=str))

class Worker:
    def __init__(self,store,concurrency=4):
        self.store=store;self.owner=new_id();self.concurrency=concurrency;self.tools=ToolService(store);self.memory=MemoryService(store);self.kbs=KnowledgeServices()

    async def execute(self,run):
        id=run['id'];owner=run.get('_claim_owner',self.owner);started=time.perf_counter()
        journal(event='run.start',run_id=id,tenant=run.get('tenant_id'),workflow=run['workflow'].get('name'),nodes=len(run['workflow'].get('nodes',[])),resumed=bool(run.get('checkpoints')))
        async def emit(event):
            if not self.store.worker_event(id,owner,event):raise asyncio.CancelledError()
            if event.get('node_id') and event['status']!='running':
                journal(event='node.'+event['status'],run_id=id,node_id=event['node_id'],transient=event.get('transient',False),cached=event.get('cached',False),duration_ms=event.get('duration_ms'),usage=event.get('usage'),error=event.get('error'))
        async def graph_run():
            workflow=Workflow.model_validate(run['workflow'])
            tenant_id=self.store.run_tenant(id,owner)
            self.store.check_resume_writes(run)
            errors=self.store.model_errors(workflow,tenant_id)+platform_errors(self.store,workflow,tenant_id)+await kb_errors(self.kbs,workflow,tenant_id,run.get('checkpoints'),run.get('vector_dependencies'))
            if errors:raise ValueError('; '.join(errors))
            resolver=lambda credential_id:self.store.resolve_run_credential(id,owner,credential_id)
            async def platform_resolver(action,*args):
                tenant=self.store.run_tenant(id,owner)
                try:
                    if action=='tool':
                        node_type,settings,input_text,checkpoint_owner=args
                        config=CONFIGS[node_type].model_validate(settings)
                        self.tools.check(config,tenant)
                        if is_write(node_type,config):self.store.mark_write(id,owner,checkpoint_owner)
                        return await self.tools.execute(node_type,settings,input_text,tenant)
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
                issues=await kb_errors(self.kbs,workflow,self.store.run_tenant(id,owner),{node.id:outputs},run.get('vector_dependencies'))
                if issues:raise ValueError('; '.join(issues))
            graph=compile_workflow(workflow,resolver,emit,run['message'],completed=run.get('checkpoints',{}),authorize_model=lambda config:self.store.authorize_run_model(id,owner,config),validate_cached=validate_cached,platform_resolver=platform_resolver).graph
            result=await graph.ainvoke({'values':{}},{'recursion_limit':150})
            return '\n\n'.join(result['values'][n.id]['text'] for n in workflow.nodes if n.type=='response' and n.id in result['values'])
        async def bounded_run():
            # The deadline covers knowledge-service preflight as well as graph execution.
            async with asyncio.timeout(RUN_TIMEOUT_SECONDS):return await graph_run()
        task=asyncio.create_task(bounded_run())
        try:
            while not task.done():
                if self.store.cancel_requested(id,owner) or not self.store.heartbeat(id,owner):
                    task.cancel();break
                await asyncio.wait({task},timeout=.2)
            output=await task
            self.store.finish_run(id,owner,'success',output=output)
            journal(event='run.finish',run_id=id,status='success',seconds=round(time.perf_counter()-started,3))
        except asyncio.CancelledError:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            cancelled=self.store.cancel_requested(id,owner)
            if cancelled:self.store.finish_run(id,owner,'cancelled')
            else:self.store.release(id,owner)
            journal(event='run.finish',run_id=id,status='cancelled' if cancelled else 'released',seconds=round(time.perf_counter()-started,3))
        except Exception as exc:
            error=f'Run timed out after {RUN_TIMEOUT_SECONDS} seconds.' if isinstance(exc,TimeoutError) else str(exc) if isinstance(exc,ValueError) else 'Run failed unexpectedly.'
            if not isinstance(exc,(ValueError,TimeoutError)):log.exception('run %s failed unexpectedly',id)
            self.store.finish_run(id,owner,'failed',error=error)
            journal(event='run.finish',run_id=id,status='failed',error=error,seconds=round(time.perf_counter()-started,3))

    async def serve(self):
        active=set()
        try:
            while True:
                active={t for t in active if not t.done()}
                while len(active)<self.concurrency:
                    claim_owner=new_id()
                    job=self.store.claim_next(claim_owner)
                    if not job:break
                    job['_claim_owner']=claim_owner
                    active.add(asyncio.create_task(self.execute(job)))
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
