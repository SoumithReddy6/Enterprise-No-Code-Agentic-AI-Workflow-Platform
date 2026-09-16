"""Execute every workflow shape the platform supports, for real, and report what each one actually did.

Self-contained: in-process knowledge services indexing evals/corpus with real Ollama embeddings,
a temporary Relay database, the real worker, real Docker for the Python tool and one read-only
public HTTP GET. Nothing touches the signed-in workspace. It is a diagnostic report: each row says
what the shape did and which runtime guarantee it exercises (see docs/production-gap-analysis.md).

    .venv/bin/python -m scripts.check_workflows [--model llama3.1:latest] [--offline]
"""
import argparse
import asyncio
import base64
import inspect
import json
import shutil
import sys
import time
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.app.tool_service import ToolService
from backend.app.main import create_app
from backend.app.kb.management import Management
from backend.app.kb.search import Search
from backend.app.kb.ingestion import Ingestion
from backend.app.kb.embedding import Embeddings
from backend.app.kb_gateway import KnowledgeServices

TENANT='local'

class Local:
    def __init__(self,domain):self.domain=domain
    async def call(self,action,tenant='local',payload=None):
        result=self.domain.call(action,tenant,payload or {})
        return await result if inspect.isawaitable(result) else result
    def call_sync(self,action,tenant='local',payload=None):return self.domain.call(action,tenant,payload or {})

def wf(name,nodes,edges):return {'version':1,'name':name,'nodes':nodes,'edges':edges}
def flow(*pairs):return [{'id':f'{a}-{b}','source':a,'target':b} for a,b in pairs]
def tool_edge(tool,target):return {'id':f'{tool}~{target}','source':tool,'target':target,'kind':'tool','targetHandle':'tools'}
def spec_edge(parent,child):return {'id':f'{parent}>{child}','source':parent,'target':child,'kind':'agent','sourceHandle':'agents'}
CHAT={'id':'input','type':'chat_input'}
def out(binding='agent.text'):return {'id':'out','type':'response','inputs':{'text':binding}}

async def index_corpus(services,embedding_model):
    kb=await services.management.call('create',TENANT,{'name':'Handbook','config':{'backend':'faiss','embedding_model':embedding_model,'chunking':'paragraph','chunk_size':800,'chunk_overlap':120,'search_defaults':{'mode':'hybrid'}}})
    for file in sorted((ROOT/'evals/corpus').glob('*.md')):
        await services.management.call('upload',TENANT,{'kb_id':kb['id'],'filename':file.name,'content_b64':base64.b64encode(file.read_bytes()).decode(),'idempotency_key':file.name})
    ingestion=Ingestion(services.management,services.index,services.embeddings)
    while (job:=await services.management.call('claim','system',{})):await ingestion.execute(job)
    detail=await services.management.call('get',TENANT,{'kb_id':kb['id']})
    if detail['status']!='ready':raise SystemExit('Corpus indexing failed: '+json.dumps(detail.get('jobs'))[:1500])
    return kb['id']

async def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model',default='llama3.1:latest');parser.add_argument('--embedding-model',default='embeddinggemma:latest')
    parser.add_argument('--offline',action='store_true',help='Skip the public HTTP GET scenario')
    args=parser.parse_args()
    LLM={'provider':'ollama','model':args.model,'temperature':0,'max_tokens':600}
    def agent(id,role='reasoner',system='You are a helpful assistant.',**extra):return {'id':id,'type':'agent','config':{**LLM,'role':role,'system':system,**extra}}
    results=[]
    with TemporaryDirectory(prefix='relay-workflows-') as d:
        key=Fernet.generate_key()
        management=Management(f'sqlite:///{d}/management.db',Path(d)/'files',key);search=Search(f'sqlite:///{d}/search.db',Path(d)/'indexes',key)
        services=KnowledgeServices(Local(management),Local(search),Embeddings())
        started=time.perf_counter();KB=await index_corpus(services,args.embedding_model)
        print(f'Indexed evals/corpus with {args.embedding_model} in {time.perf_counter()-started:.1f}s; model {args.model}.',flush=True)
        def retrieve(id,**cfg):return {'id':id,'type':'retrieve','config':{'knowledge_base_id':KB,'mode':'hybrid','top_k':4,**cfg}}
        store=Store(f'sqlite:///{d}/relay.db',key);store.allow_model('ollama',args.model,'',TENANT)
        worker=Worker(store);worker.kbs=services
        github=ToolService(store).save_connection({'name':'GitHub API','provider':'http','endpoint':'https://api.github.com'},TENANT)['id']

        async def execute(name,workflow,message,check,note=''):
            t=time.perf_counter()
            try:
                run=store.create_run(workflow,message,TENANT)
                await worker.execute(store.claim_next(worker.owner))
                record=store.run(run['id'],TENANT)
                trace=[f"{e['node_id']}:{e['status']}{'~' if e.get('transient') else ''}" for e in record['events'] if e.get('node_id') and e['status']!='running']
                ok=record['status']=='success' and bool(check(record))
                results.append({'scenario':name,'status':record['status'],'ok':ok,'seconds':round(time.perf_counter()-t,1),'note':note,'output':record.get('output','')[:400],'error':record.get('error','')[:300],'trace':trace})
            except Exception as exc:
                results.append({'scenario':name,'status':'exception','ok':False,'seconds':round(time.perf_counter()-t,1),'note':note,'output':'','error':(str(exc) or traceback.format_exc())[-300:],'trace':[]})
            r=results[-1];print(f"\n{'✓' if r['ok'] else '✗'} {name} [{r['status']}, {r['seconds']}s] {note}\n   trace: {' → '.join(r['trace'])}\n   output: {r['output'][:240]!r}"+(f"\n   error: {r['error']}" if r['error'] else ''),flush=True)

        await execute('1 plain_chat',wf('Chat',[CHAT,{**agent('agent'),'inputs':{'input':'input.message'}},out()],flow(('input','agent'),('agent','out'))),'In two sentences, what is a durable job queue?',lambda r:len(r['output'])>40)
        await execute('2 grounded_qa',wf('Grounded',[CHAT,{**retrieve('retrieve'),'inputs':{'query':'input.message'}},{**agent('agent'),'inputs':{'input':'retrieve.context'}},out()],flow(('input','retrieve'),('retrieve','agent'),('agent','out'))),'What is the meal per diem for overnight travel?',lambda r:'65' in r['output'] and '[S' in r['output'],'expects $65 with an [S#] citation')
        await execute('3 query_node',wf('Query',[CHAT,{'id':'query','type':'query','config':{**LLM,'knowledge_base_id':KB,'mode':'hybrid','top_k':4},'inputs':{'query':'input.message'}},out('query.text')],flow(('input','query'),('query','out'))),'What is the meal per diem for overnight travel?',lambda r:'65' in r['output'])
        if shutil.which('docker'):
            await execute('4 agent_python_tool',wf('Calc',[CHAT,{**agent('agent',system='You have a calculator tool.'),'inputs':{'input':'input.message'}},{'id':'calc','type':'tool_python','config':{'code':"print(eval(input_text,{'__builtins__':{}},{}))",'timeout_seconds':10,'description':'Calculator. Input must be a single Python arithmetic expression such as 240*0.15+17; it prints the numeric result.'}},out()],flow(('input','agent'),('agent','out'))+[tool_edge('calc','agent')]),'Compute 15% of 240, then add 17. Use the calculator tool and report the final number.',lambda r:'53' in r['output'],'tool described to the model; a tool error is an observation, not a run failure')
        else:print('\n- 4 agent_python_tool skipped: Docker not available')
        await execute('5 agent_retrieve_tool',wf('AgentSearch',[CHAT,{**agent('agent',system='You can search the employee handbook with the attached retrieve tool.'),'inputs':{'input':'input.message'}},retrieve('search'),out()],flow(('input','agent'),('agent','out'))+[tool_edge('search','agent')]),'Search the handbook: how many paid vacation days do full-time employees accrue per year?',lambda r:'22' in r['output'],'retrieval via tool is rendered as passages; citations validated')
        await execute('6 router_specialists',wf('Router',[CHAT,{**agent('router',role='router'),'inputs':{'input':'input.message'}},agent('summarizer',role='summarizer'),agent('extractor',role='extraction'),out('router.text')],flow(('input','router'),('router','out'))+[spec_edge('router','summarizer'),spec_edge('router','extractor')]),'Summarize this in one sentence: Meridian Freight Cooperative reimburses a $65 per diem, requires receipts over $25, pays 67 cents per mile, and expects reports within 30 days.',lambda r:any(e.get('node_id')=='summarizer' for e in r['events']),'expects delegation to the summarizer')
        await execute('7 condition_branch',wf('Branch',[CHAT,{'id':'check','type':'condition','config':{'contains':'refund'},'inputs':{'value':'input.message'}},{'id':'yes','type':'response','inputs':{'text':'input.message'}},{'id':'no','type':'response','inputs':{'text':'input.message'}}],[{'id':'a','source':'input','target':'check'},{'id':'b','source':'check','target':'yes','sourceHandle':'true'},{'id':'c','source':'check','target':'no','sourceHandle':'false'}]),'I want a refund for order 42',lambda r:any(e.get('node_id')=='yes' for e in r['events']) and not any(e.get('node_id')=='no' for e in r['events']),'true branch only')
        if not args.offline:
            await execute('8 http_tool_flow',wf('HTTP',[CHAT,{'id':'http','type':'tool_http','config':{'connection_id':github,'method':'GET','path':'/zen'},'inputs':{'input':'input.message'}},out('http.text')],flow(('input','http'),('http','out'))),'ignored',lambda r:len(r['output'])>3,'read-only GET api.github.com/zen')
        def is_json(r):
            try:return isinstance(json.loads(r['output'].strip().strip('`').removeprefix('json')),dict)
            except Exception:return False
        await execute('9 extraction_json',wf('Extract',[CHAT,{**retrieve('retrieve'),'inputs':{'query':'input.message'}},{**agent('agent',role='extraction',system='Return only a JSON object with keys per_diem, mileage_rate, receipt_threshold. No prose.'),'inputs':{'input':'retrieve.context'}},out()],flow(('input','retrieve'),('retrieve','agent'),('agent','out'))),'Extract the meal per diem, the mileage rate and the receipt threshold from the expense policy.',is_json,'extraction role: runtime parses and validates the JSON')
        mem=wf('Memory',[CHAT,{**agent('agent',role='memory',memory_key='check-workflows'),'inputs':{'input':'input.message'}},out()],flow(('input','agent'),('agent','out')))
        await execute('10a memory_store',mem,'Remember this: my favourite database is DuckDB.',lambda r:True)
        await execute('10b memory_recall',mem,'What is my favourite database?',lambda r:'duckdb' in r['output'].lower(),'expects DuckDB recalled')
        await execute('11 chained_agents',wf('Chain',[CHAT,{**retrieve('retrieve'),'inputs':{'query':'input.message'}},{**agent('summ',role='summarizer'),'inputs':{'input':'retrieve.context'}},{**agent('critic',role='critic'),'inputs':{'input':'summ.text'}},out('critic.text')],flow(('input','retrieve'),('retrieve','summ'),('summ','critic'),('critic','out'))),'Summarize the on-call response expectations.',lambda r:len(r['output'])>80 and '[unsupported reference]' not in r['output'] and json.loads(next(e['outputs']['sources'] for e in r['events'] if e.get('node_id')=='out' and e['status']=='success'))!=[],'Response validates [S#] against run-wide evidence')
        await execute('12 manual_trigger',wf('Manual',[{'id':'input','type':'manual_input'},{**agent('agent'),'inputs':{'input':'input.message'}},out()],flow(('input','agent'),('agent','out'))),'Reply with the single word OK.',lambda r:'ok' in r['output'].lower())
        with TestClient(create_app(f'sqlite:///{d}/api.db',key,auth_enabled=False,embedded_worker=False,knowledge_services=services)) as client:
            bad=wf('BadKB',[CHAT,{**retrieve('retrieve',knowledge_base_id='does-not-exist'),'inputs':{'query':'input.message'}},{**agent('agent',provider='demo'),'inputs':{'input':'retrieve.context'}},out()],flow(('input','retrieve'),('retrieve','agent'),('agent','out')))
            r=client.post('/api/runs',json={'workflow':bad,'message':'x'});results.append({'scenario':'13 invalid_kb_rejected','status':str(r.status_code),'ok':r.status_code==422,'seconds':0,'note':'validation gate','output':'','error':'' if r.status_code==422 else r.text[:200],'trace':[]})
            c=ToolService(client.app.state.store).save_connection({'name':'GitHub API','provider':'http','endpoint':'https://api.github.com'},TENANT)['id']
            write=wf('Write',[CHAT,{'id':'http','type':'tool_http','config':{'connection_id':c,'method':'POST','path':'/repos/x/y/issues'},'inputs':{'input':'input.message'}},out('http.text')],flow(('input','http'),('http','out')))
            r=client.post('/api/runs',json={'workflow':write,'message':'x'});results.append({'scenario':'14 write_without_consent_rejected','status':str(r.status_code),'ok':r.status_code==422,'seconds':0,'note':'consent gate','output':'','error':'' if r.status_code==422 else r.text[:200],'trace':[]})
        for r in results[-2:]:print(f"\n{'✓' if r['ok'] else '✗'} {r['scenario']} [{r['status']}] {r['note']}",flush=True)
        store.engine.dispose();management.engine.dispose();search.engine.dispose()
    print('\n=== SUMMARY');print(f"{'scenario':36} {'status':10} {'ok':3} {'s':>6}  note")
    for r in results:print(f"{r['scenario']:36} {r['status']:10} {'✓' if r['ok'] else '✗':3} {r['seconds']:>6}  {r['note'][:80]}")
    out_path=ROOT/'evals/results/workflows-latest.json';out_path.write_text(json.dumps({'model':args.model,'results':results},indent=1,ensure_ascii=False))
    print(f'\nWrote {out_path}')

if __name__=='__main__':asyncio.run(main())
