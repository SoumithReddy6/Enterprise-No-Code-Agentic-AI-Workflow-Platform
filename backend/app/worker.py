"""Separate worker with renewable leases and durable per-node results."""
import asyncio
import os
from pathlib import Path
from .storage import Store, local_key, new_id
from .models import Workflow
from .compiler import compile_workflow
from .knowledge import Knowledge
from .knowledge_nodes import knowledge_errors
from .tool_service import ToolService,CONFIGS,is_write
from .vector_service import VectorService
from .agent_memory import MemoryService
from .platform_validation import platform_errors,resolve_vector,kb_errors
from .kb_gateway import KnowledgeServices

RUN_TIMEOUT_SECONDS=120

class Worker:
    def __init__(self,store,concurrency=4):
        self.store=store;self.owner=new_id();self.concurrency=concurrency;self.knowledge=Knowledge(store);self.tools=ToolService(store);self.vectors=VectorService(store);self.memory=MemoryService(store);self.kbs=KnowledgeServices()

    async def execute(self,run):
        id=run['id'];owner=run.get('_claim_owner',self.owner)
        async def emit(event):
            if not self.store.worker_event(id,owner,event):raise asyncio.CancelledError()
        async def graph_run():
            workflow=Workflow.model_validate(run['workflow'])
            tenant_id=self.store.run_tenant(id,owner)
            self.store.check_resume_writes(run)
            errors=self.store.model_errors(workflow,tenant_id)+knowledge_errors(self.knowledge,workflow,tenant_id,run.get('checkpoints'))+platform_errors(self.store,workflow,tenant_id,run.get('checkpoints'),run.get('vector_dependencies'))+await kb_errors(self.kbs,workflow,tenant_id,run.get('checkpoints'),run.get('vector_dependencies'))
            if errors:raise ValueError('; '.join(errors))
            resolver=lambda credential_id:self.store.resolve_run_credential(id,owner,credential_id)
            def knowledge_resolver(action,*args):
                tenant=self.store.run_tenant(id,owner)
                try:
                    if action=='retrieve':return self.knowledge.retrieve(*args,tenant)
                    if action=='verify_sources':return self.knowledge.verify_sources(*args,tenant)
                except KeyError:raise ValueError('Knowledge source is unavailable in this workspace.') from None
                raise ValueError('Unsupported knowledge operation.')
            async def platform_resolver(action,*args):
                tenant=self.store.run_tenant(id,owner)
                try:
                    if action=='tool':
                        node_type,settings,input_text,checkpoint_owner=args
                        config=CONFIGS[node_type].model_validate(settings)
                        self.tools.check(config,tenant)
                        if is_write(node_type,config):self.store.mark_write(id,owner,checkpoint_owner)
                        return await self.tools.execute(node_type,settings,input_text,tenant)
                    if action=='resolve_vector':return resolve_vector(self.vectors,*args,tenant)
                    if action=='retrieve':return await self.vectors.retrieve(*args,tenant)
                    if action=='kb_retrieve':return await self.kbs.retrieve(*args,tenant)
                    if action=='verify_vector_sources':
                        sources=args[0]
                        return await self.kbs.verify(sources,tenant) if sources and 'knowledge_base_id' in sources[0] else self.vectors.verify_sources(sources,tenant)
                    if action=='record_vector_sources':
                        checkpoint_owner,sources=args
                        canonical=await self.kbs.verify(sources,tenant) if sources and 'knowledge_base_id' in sources[0] else self.vectors.verify_sources(sources,tenant)
                        return self.store.record_vector_dependencies(id,owner,checkpoint_owner,canonical)
                    if action=='memory_read':return self.memory.read(*args,tenant)
                    if action=='memory_write':return self.memory.write(*args,tenant)
                except KeyError:raise ValueError('Platform resource is unavailable in this workspace.') from None
                raise ValueError('Unsupported platform operation.')
            async def validate_cached(node,outputs):
                issues=knowledge_errors(self.knowledge,workflow,self.store.run_tenant(id,owner),{node.id:outputs})+platform_errors(self.store,workflow,self.store.run_tenant(id,owner),{node.id:outputs},run.get('vector_dependencies'))+await kb_errors(self.kbs,workflow,self.store.run_tenant(id,owner),{node.id:outputs},run.get('vector_dependencies'))
                if issues:raise ValueError('; '.join(issues))
            graph=compile_workflow(workflow,resolver,emit,run['message'],completed=run.get('checkpoints',{}),authorize_model=lambda config:self.store.authorize_run_model(id,owner,config),knowledge_resolver=knowledge_resolver,validate_cached=validate_cached,platform_resolver=platform_resolver).graph
            async with asyncio.timeout(120):
                result=await graph.ainvoke({'values':{}},{'recursion_limit':150})
            return '\n\n'.join(result['values'][n.id]['text'] for n in workflow.nodes if n.type=='response' and n.id in result['values'])
        async def bounded_run():
            async with asyncio.timeout(RUN_TIMEOUT_SECONDS):return await graph_run()
        task=asyncio.create_task(bounded_run())
        try:
            while not task.done():
                if self.store.cancel_requested(id,owner) or not self.store.heartbeat(id,owner):
                    task.cancel();break
                await asyncio.wait({task},timeout=.2)
            output=await task
            self.store.finish_run(id,owner,'success',output=output)
        except asyncio.CancelledError:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            if self.store.cancel_requested(id,owner):self.store.finish_run(id,owner,'cancelled')
            else:self.store.release(id,owner)
        except Exception as exc:
            error='Run timed out after 120 seconds.' if isinstance(exc,TimeoutError) else str(exc) if isinstance(exc,ValueError) else 'Run failed unexpectedly.'
            self.store.finish_run(id,owner,'failed',error=error)

    async def serve(self):
        active=set()
        indexing=asyncio.create_task(self.knowledge.serve())
        vector_indexing=asyncio.create_task(self.vectors.serve())
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
            indexing.cancel();vector_indexing.cancel()
            await asyncio.gather(indexing,vector_indexing,return_exceptions=True)
            for task in active:task.cancel()
            await asyncio.gather(*active,return_exceptions=True)

async def main():
    from dotenv import load_dotenv
    load_dotenv()
    path=Path(os.environ.get('DATA_DIR','.data'));path.mkdir(parents=True,exist_ok=True,mode=0o700)
    store=Store(os.environ.get('DATABASE_URL',f'sqlite:///{path}/workflows.db'),local_key(path))
    print('Relay durable worker started (4 slots).',flush=True)
    try:await Worker(store).serve()
    finally:store.engine.dispose()

if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
