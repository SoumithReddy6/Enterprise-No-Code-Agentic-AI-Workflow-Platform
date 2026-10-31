"""R01 regression: a cached Response created by an earlier loop resume must recheck
its hidden knowledge dependencies. Run against argv[1], never rewrite old evidence.
The default assertion demands refusal, so the affected commit exits nonzero.
"""
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0,sys.argv[1])

import httpx
from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from backend.app.kb.management import Management
from backend.app.kb.search import Search
from backend.app.kb_gateway import KnowledgeServices
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.tests.test_kb_services_integration import Local,populated
from backend.tests.test_response_source_boundary import loop_workflow,other_document,passage_model


async def main():
    with TemporaryDirectory(prefix='relay-r01-impl-review-') as directory, MonkeyPatch.context() as mp:
        root=Path(directory);key=Fernet.generate_key()
        management=Management(f'sqlite:///{root}/management.db',root/'files',key)
        search=Search(f'sqlite:///{root}/search.db',root/'indexes',key)
        services=KnowledgeServices(Local(management),Local(search))
        store=Store(f'sqlite:///{root}/relay.db',key)
        try:
            kb,doc,ingester=await populated(services)
            await other_document(services,kb)(ingester)
            worker=Worker(store);worker.kbs=services
            store.allow_model('ollama','fixture','','local')
            mp.setenv('RELAY_RUN_TOKEN_LIMIT','1000')
            mp.setattr('backend.app.tool_service.public_addresses',lambda *_:['93.184.216.34'])
            worker.tools.transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
                'issues':[{'key':'P-1','fields':{'summary':'project codename',
                    'status':{'name':'Open'},'assignee':None,'updated':''}}]}))
            calls=[];timeline=[];phase={'attempt':1};original_verify=services.verify
            passage_model(mp,calls)

            async def verify(sources,tenant):
                timeline.append({'event':'verify','attempt':phase['attempt'],
                    'documents':[s['document_id'] for s in sources]})
                return await original_verify(sources,tenant)

            mp.setattr(services,'verify',verify)
            original_event=store.worker_event

            def event(run_id,owner,payload):
                saved=original_event(run_id,owner,payload)
                node=payload.get('node_id');success=payload.get('status')=='success' and not payload.get('transient')
                if success:
                    timeline.append({'event':'success','attempt':phase['attempt'],'node':node,
                        'cached':payload.get('cached',False)})
                    if phase['attempt']==1 and node=='each':
                        raise asyncio.CancelledError('stop after loop checkpoint')
                    if phase['attempt']==2 and node=='out':
                        raise asyncio.CancelledError('stop after Response checkpoint')
                    if phase['attempt']==3 and node=='render' and payload.get('cached'):
                        services.management.call_sync('remove_document','local',
                            {'kb_id':kb['id'],'document_id':doc['id']})
                        timeline.append({'event':'removed','attempt':3})
                return saved

            mp.setattr(store,'worker_event',event)
            workflow=loop_workflow(store,kb)
            row=store.create_run(workflow.model_dump(mode='json'),'project codename')
            await worker.execute(store.claim_next(worker.owner))
            first=store.run(row['id'])
            assert first['status']=='failed' and 'each' in first['checkpoints'] and 'out' not in first['checkpoints']
            phase['attempt']=2
            store.resume_run(row['id'])
            await worker.execute(store.claim_next(worker.owner))
            second=store.run(row['id'])
            assert second['status']=='failed' and 'out' in second['checkpoints']
            assert json.loads(second['checkpoints']['out']['sources'])==[]
            assert 'each:0' in second['vector_dependencies'] and calls==[doc['id']]
            phase['attempt']=3
            store.resume_run(row['id'])
            await worker.execute(store.claim_next(worker.owner))
            final=store.run(row['id'])
            deleted=next(i for i,e in enumerate(timeline) if e['event']=='removed')
            after=timeline[deleted+1:]
            result={'application_root':str(Path(sys.modules['backend.app.worker'].__file__).parent),
                'status':final['status'],'error':final.get('error'),'output':final.get('output'),
                'provider_calls':len(calls),'cached_response_sources':json.loads(second['checkpoints']['out']['sources']),
                'checks_after_removal':sum(e['event']=='verify' for e in after),
                'cached_response_republished':any(e['event']=='success' and e.get('node')=='out' and e.get('cached') for e in after),
                'timeline':timeline}
            print(json.dumps(result,indent=2),flush=True)
            assert calls==[doc['id']]
            assert final['status']=='failed' and not final.get('output'), 'Removed-source cached answer must not be published'
            assert result['checks_after_removal']>0 and not result['cached_response_republished']
        finally:
            store.engine.dispose();management.engine.dispose();search.engine.dispose()


if __name__=='__main__':asyncio.run(main())
