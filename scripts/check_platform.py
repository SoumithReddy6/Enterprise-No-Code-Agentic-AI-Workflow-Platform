"""Local Ollama/vector/Docker smoke in a temporary database; no saved user data."""
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from cryptography.fernet import Fernet
from backend.app.storage import Store
from backend.app.models import Workflow
from backend.app.compiler import validate_workflow
from backend.app.worker import Worker


async def check(directory):
    os.environ['VECTOR_DATA_DIR']=str(Path(directory)/'vectors')
    store=Store('sqlite:///'+str(Path(directory)/'check.db'),Fernet.generate_key())
    worker=Worker(store)
    chat=os.environ.get('RELAY_SMOKE_CHAT_MODEL','qwen2.5:0.5b')
    embedding=os.environ.get('RELAY_SMOKE_EMBED_MODEL','embeddinggemma:latest')
    store.allow_model('ollama',chat)
    resources=[]
    for backend in ('faiss','chroma'):
        resource=worker.vectors.create({'name':'Local smoke','backend':backend,'embedding_model':embedding})
        resources.append(resource)
        worker.vectors.upload(resource['id'],'handbook.txt',b'The Relay test project has the codename BLUEBIRD. Its launch date is October 19. The owner is Maya.')
        assert await worker.vectors.process_once()
        files=worker.vectors.files(resource['id'])
        assert files[0]['status']=='ready',files
        for mode in ('similarity','keyword','hybrid','rrf'):
            sources=await worker.vectors.retrieve(resource['id'],'Relay project codename',{'mode':mode})
            assert sources and 'BLUEBIRD' in sources[0]['text'],sources
        print(f'{backend}: real Ollama embeddings, durable index, four retrieval modes passed.',flush=True)
    query=Workflow.model_validate({'version':1,'name':'Local query smoke','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'query','type':'query','inputs':{'query':'input.message'},'config':{'provider':'ollama','model':chat,'temperature':0,'max_tokens':128,'mode':'rrf'}},
        {'id':'db','type':'vector_faiss','config':{'resource_id':resources[0]['id']}},
        {'id':'out','type':'response','inputs':{'text':'query.text'}}],
        'edges':[{'id':'a','source':'input','target':'query'},{'id':'b','source':'query','target':'out'},
                 {'id':'store','source':'db','target':'query','kind':'store'}]})
    assert not validate_workflow(query),validate_workflow(query)
    async def run(workflow,message):
        created=store.create_run(workflow.model_dump(),message)
        await worker.execute(store.claim_next(worker.owner))
        result=store.run(created['id'])
        assert result['status']=='success',result.get('error')
        return result
    answer=await run(query,'What is the Relay test project codename? Cite the evidence.')
    assert 'BLUEBIRD' in answer['output'].upper(),answer['output']
    sources=json.loads(answer['checkpoints']['query']['sources'])
    assert sources and sources[0]['citation']=='S1'
    print('Query: durable workflow + local LLM answer + canonical source passed: '+answer['output'],flush=True)
    agent=Workflow.model_validate({'version':1,'name':'Local agent smoke','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'agent','type':'agent','inputs':{'input':'input.message'},'config':{'provider':'ollama','model':chat,'temperature':0,'max_tokens':128,'role':'summarizer'}},
        {'id':'out','type':'response','inputs':{'text':'agent.text'}}],
        'edges':[{'id':'a','source':'input','target':'agent'},{'id':'b','source':'agent','target':'out'}]})
    answer=await run(agent,'Summarize in one sentence: Relay supports local Ollama models and workflow nodes.')
    assert answer['output']
    print('Agent: real local model execution passed: '+answer['output'],flush=True)
    result=await worker.tools.execute('tool_python',{'code':'print(input_text.upper())'},'relay isolated python')
    assert 'RELAY ISOLATED PYTHON' in result,result
    print('Python: real isolated Docker execution passed.',flush=True)
    store.engine.dispose()


if __name__=='__main__':
    with TemporaryDirectory(prefix='relay-platform-') as directory:asyncio.run(check(directory))
