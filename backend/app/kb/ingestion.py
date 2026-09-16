"""Durable ingestion worker. All authoritative writes go through service RPCs."""
import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import signal
import sys
import tempfile
from .rpc import Client
from .embedding import Embeddings
from ..vector_service import VectorService

log=logging.getLogger('relay.knowledge.ingestion')

class Ingestion:
    def __init__(self,management=None,search=None,embeddings=None):
        self.management=management or Client('management');self.search=search or Client('search');self.embeddings=embeddings or Embeddings()
    async def extract(self,filename,content):
        with tempfile.TemporaryDirectory(prefix='relay-kb-extract-') as directory:
            source=Path(directory)/('document'+Path(filename).suffix.lower());output=Path(directory)/'extracted.json'
            source.write_bytes(content)
            env=dict(os.environ);env['PYTHONPATH']=str(Path(__file__).resolve().parents[3])
            process=await asyncio.create_subprocess_exec(sys.executable,'-m','backend.app.vector_extract',str(source),str(output),env=env,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
            try:await asyncio.wait_for(process.wait(),300)
            finally:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await process.wait()
            if not output.exists() or output.stat().st_size>16*1024*1024:raise ValueError('Extraction failed or exceeded the 16 MB text budget')
            result=json.loads(output.read_text())
            if result.get('error'):raise ValueError(result['error'])
            return result['pages']
    async def execute(self,job):
        tenant=job['tenant_id'];fence={'job_id':job['id'],'attempt_id':job['attempt_id']}
        async def progress(stage,**extra):
            await self.management.call('progress',tenant,{**fence,'stage':stage,**extra})
        async def process():
            config=job['config'];chunks=[];vectors=[]
            digest=config.get('embedding_digest','')
            if config.get('embedding_model'):
                actual=await self.embeddings.fingerprint(config['embedding_model'])
                if digest and actual!=digest:raise ValueError('Embedding model changed. Rebuild using its current version.')
                digest=actual
            for number,document in enumerate(job['documents']):
                await progress('extracting',completed=number,total=len(job['documents']))
                artifact=await self.management.call('artifact',tenant,{**fence,'document_id':document['id']})
                pages=await self.extract(document['filename'],base64.b64decode(artifact['content_b64'],validate=True))
                await progress('chunking',completed=number,total=len(job['documents']))
                split=VectorService.split(None,pages,config)
                for chunk in split:
                    chunk['document_id']=document['id'];chunk['ordinal']=len(chunks);chunks.append(chunk)
                if len(chunks)>50000 or sum(len(c['text']) for c in chunks)>16*1024*1024:raise ValueError('Knowledge base exceeds its chunk/text budget')
            if config.get('embedding_model'):
                for offset in range(0,len(chunks),32):
                    await progress('embedding',completed=offset,total=len(chunks))
                    batch=await self.embeddings.embed(config['embedding_model'],[c['text'] for c in chunks[offset:offset+32]],digest)
                    if len(chunks)*len(batch[0])>8_000_000:raise ValueError('Knowledge base exceeds vector memory budget; use larger chunks or smaller embeddings')
                    vectors.extend(batch)
            await progress('indexing',completed=0,total=len(chunks))
            payload={'kb_id':job['kb_id'],'version':job['version'],'segment_id':job['attempt_id'],'config':{**config,'embedding_digest':digest},'connection':job.get('connection') or {}, 'documents':job['documents'],'chunks':chunks,'vectors':vectors}
            state=await self.management.call('build_status',tenant,{'kb_id':job['kb_id'],'segment_id':job['attempt_id']})
            if not state.get('allowed'):raise ValueError('Indexing attempt was cancelled or superseded')
            result=await self.search.call('build',tenant,payload)
            await progress('verifying',completed=len(chunks),total=len(chunks))
            await self.management.call('publish',tenant,{**fence,'segment_id':result['segment_id'],'chunk_count':len(chunks),'embedding_digest':digest,'dimensions':len(vectors[0]) if vectors else 0})
        task=asyncio.create_task(process())
        try:
            while not task.done():
                done,_=await asyncio.wait({task},timeout=5)
                if not done:
                    result=await self.management.call('renew',tenant,fence)
                    if not result.get('ok'):raise ValueError('Indexing lease expired')
            await task
        except asyncio.CancelledError:
            task.cancel();raise
        except Exception as exc:
            task.cancel()
            error=str(exc) if isinstance(exc,ValueError) else 'Document processing failed unexpectedly'
            try:await self.management.call('fail',tenant,{**fence,'error':error})
            except Exception:log.warning('Could not record indexing failure; lease recovery will retry job %s',job['id'])
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    async def cleanup(self):
        pending=await self.management.call('cleanup_list','system',{})
        for item in pending:
            tenant=item['tenant_id'];result={'segment_id':item['segment_id'],'ok':False}
            try:
                await self.search.call('cleanup',tenant,{'kb_id':item['kb_id'],'segment_id':item['segment_id'],'retain_provenance':item.get('retain_provenance',False)});result['ok']=True
            except Exception as exc:result['error']=str(exc)[:500] if isinstance(exc,ValueError) else 'Index cleanup unavailable'
            await self.management.call('cleanup_result',tenant,result)
    async def serve(self):
        while True:
            try:
                await self.cleanup()
                job=await self.management.call('claim','system',{})
                if job:await self.execute(job)
                else:await asyncio.sleep(1)
            except asyncio.CancelledError:raise
            except Exception:log.warning('Knowledge services unavailable; retrying in two seconds');await asyncio.sleep(2)

async def main():
    from dotenv import load_dotenv
    load_dotenv();logging.basicConfig(level=logging.INFO)
    print('Relay knowledge ingestion worker started.',flush=True)
    await Ingestion().serve()
if __name__=='__main__':
    try:asyncio.run(main())
    except KeyboardInterrupt:pass
