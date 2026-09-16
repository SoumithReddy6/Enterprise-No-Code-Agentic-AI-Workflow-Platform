"""Real service-process smoke in isolated storage; never uses the signed-in workspace."""
import asyncio
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.worker import Worker
from backend.tests.test_kb_workflow import graph

ROOT=Path(__file__).resolve().parents[1]

def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]

def main(directory):
    management_port,search_port=free_port(),free_port()
    os.environ.update({'DATA_DIR':directory,'KB_DATA_DIR':directory+'/knowledge','KB_MANAGEMENT_URL':f'http://127.0.0.1:{management_port}','KB_SEARCH_URL':f'http://127.0.0.1:{search_port}','KB_SERVICE_KEY':Fernet.generate_key().decode(),'RELAY_EMBEDDED_WORKER':'false'})
    processes=[];logs=[]
    def start(module,port=None):
        log=open(Path(directory)/(module.rsplit('.',1)[-1]+'.log'),'w+');logs.append(log)
        args=[sys.executable,'-m',module] if port is None else [sys.executable,'-m','uvicorn',module+':create_app','--factory','--host','127.0.0.1','--port',str(port)]
        p=subprocess.Popen(args,cwd=ROOT,env=os.environ.copy(),stdout=log,stderr=log,start_new_session=True);processes.append(p)
        if port:
            for _ in range(150):
                if p.poll() is not None:raise AssertionError('Service failed to start')
                try:
                    if httpx.get(f'http://127.0.0.1:{port}/health',timeout=1,trust_env=False).status_code==200:return
                except httpx.HTTPError:pass
                time.sleep(.1)
            raise AssertionError('Service startup deadline exceeded')
    try:
        start('backend.app.kb.management_app',management_port);start('backend.app.kb.search_app',search_port);start('backend.app.kb.ingestion')
        with TestClient(create_app('sqlite:///'+directory+'/relay.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False)) as client:
            created=client.post('/api/knowledge-bases',json={'name':'Real local knowledge','config':{'embedding_model':'embeddinggemma:latest','search_defaults':{'mode':'rrf'}}})
            assert created.status_code==201,created.text
            kb=created.json();path='/api/knowledge-bases/'+kb['id']
            doc=client.post(path+'/documents?filename=handbook.txt',content=b'The Relay project codename is BLUEBIRD. The project owner is Maya. The launch date is October 19.',headers={'Idempotency-Key':'upload-smoke'})
            assert doc.status_code==202,doc.text
            def wait_ready(previous=None):
                for _ in range(240):
                    detail=client.get(path).json()
                    if detail['status'] in ('failed','degraded'):raise AssertionError(detail)
                    if detail['status']=='ready' and (previous is None or detail['active_version']!=previous):return detail
                    time.sleep(.5)
                raise AssertionError('Indexing did not complete')
            detail=wait_ready();version=detail['active_version']
            print('Independent management/search/ingestion processes: upload and pinned real Ollama embeddings passed.',flush=True)
            for mode in ('similarity','keyword','hybrid','rrf'):
                result=client.post(path+'/search',json={'query':'What is the Relay project codename?','options':{'mode':mode}})
                assert result.status_code==200 and result.json()['sources'],result.text
                assert 'BLUEBIRD' in result.json()['sources'][0]['text']
            print('Search API: all four techniques and canonical document sources passed.',flush=True)
            saved_sources=result.json()['sources']
            assert client.get(saved_sources[0]['url']).content.startswith(b'The Relay')
            workflow=graph();workflow.nodes[1].config.update(knowledge_base_id=kb['id'],mode='rrf')
            run=client.post('/api/runs',json={'workflow':workflow.model_dump(),'message':'What is the codename?'})
            assert run.status_code==201,run.text
            worker=Worker(client.app.state.store)
            asyncio.run(worker.execute(worker.store.claim_next(worker.owner)))
            record=client.get('/api/runs/'+run.json()['id']).json()
            assert record['status']=='success' and 'BLUEBIRD' in record['output'],record.get('error')
            print('Named Retrieve → Agent → Response passed through the workflow runtime.',flush=True)
            rebuilt=client.post(path+'/rebuild',json={'config':{**kb['config'],'chunk_size':500,'chunk_overlap':100}})
            assert rebuilt.status_code==200,rebuilt.text
            wait_ready(version)
            time.sleep(2)
            asyncio.run(client.app.state.knowledge_services.verify(saved_sources,'local'))
            print('Version rebuild, activation and retained source verification after cleanup passed.',flush=True)
            assert client.post(path+'/documents/'+doc.json()['id']+'/remove').status_code==200
            assert client.get(saved_sources[0]['url']).status_code==404
            try:asyncio.run(client.app.state.knowledge_services.verify(saved_sources,'local'))
            except (KeyError,ValueError):pass
            else:raise AssertionError('Removed source remained authorized')
            assert httpx.post(os.environ['KB_MANAGEMENT_URL']+'/rpc',json={'action':'list','tenant':'local','payload':{}},trust_env=False).status_code==401
            print('Immediate removal, download revocation and internal service authentication passed.',flush=True)
    except BaseException:
        for log in logs:
            log.flush();log.seek(0);print(log.read()[-5000:],file=sys.stderr)
        raise
    finally:
        for p in processes:
            if p.poll() is None:os.killpg(p.pid,signal.SIGTERM)
        for p in processes:
            try:p.wait(5)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
        for log in logs:log.close()

if __name__=='__main__':
    with TemporaryDirectory(prefix='relay-kb-services-') as directory:main(directory)
