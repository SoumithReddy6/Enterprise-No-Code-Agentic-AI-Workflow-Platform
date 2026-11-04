"""Retrieval and grounding evaluation over the labelled synthetic corpus in evals/.

Runs the real knowledge services in-process with real Ollama embeddings in temporary
storage, measures every retrieval mode, and optionally measures grounded generation
through the workflow runtime. Never touches the signed-in workspace.

    .venv/bin/python -m scripts.eval_retrieval
    .venv/bin/python -m scripts.eval_retrieval --generate llama3.1:latest --generate-mode rrf
"""
import argparse
import asyncio
import base64
import inspect
import json
import statistics
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.app.kb.management import Management
from backend.app.kb.search import Search
from backend.app.kb.ingestion import Ingestion
from backend.app.kb.embedding import Embeddings
from backend.app.kb_gateway import KnowledgeServices
from backend.app.compiler import compile_workflow
from backend.app.models import Workflow
from backend.app.agent_runtime import NO_EVIDENCE

class Local:
    """In-process stand-in for the authenticated RPC client."""
    def __init__(self,domain):self.domain=domain
    async def call(self,action,tenant='local',payload=None):
        result=self.domain.call(action,tenant,payload or {})
        return await result if inspect.isawaitable(result) else result
    def call_sync(self,action,tenant='local',payload=None):return self.domain.call(action,tenant,payload or {})

def workflow(kb_id,mode,model,top_k,candidate_k=50,reranker='none',product_guard=False):
    return Workflow.model_validate({'version':1,'name':'Evaluation','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'retrieve','type':'retrieve','config':{'knowledge_base_id':kb_id,'mode':mode,'top_k':top_k,'candidate_k':candidate_k,'reranker':reranker,'answerability_guard':product_guard},'inputs':{'query':'input.message'}},
        {'id':'agent','type':'agent','config':{'provider':'ollama','model':model,'max_tokens':400,'temperature':0},'inputs':{'input':'retrieve.context'}},
        {'id':'out','type':'response','inputs':{'text':'agent.text'}}],
        'edges':[{'id':'a','source':'input','target':'retrieve'},{'id':'b','source':'retrieve','target':'agent'},{'id':'c','source':'agent','target':'out'}]})

def mean(values):return round(statistics.fmean(values),3) if values else None

def contract_compliance(rows):
    counts={state:sum(r.get('contract_compliance')==state for r in rows) for state in ('first_attempt','after_repair','failed','not_called','unavailable')}
    attempted=sum(counts[s] for s in ('first_attempt','after_repair','failed'))
    return {**{s:round(counts[s]/attempted,3) if attempted else None for s in ('first_attempt','after_repair','failed')},
            'attempted':attempted,'not_called':counts['not_called'],'unavailable':counts['unavailable'],'counts':counts}

def expected_evidence(question):
    evidence=question.get('evidence')
    if evidence is not None:return evidence
    document=question.get('document')
    return [{'document':document,'answers':question.get('answers',[])}] if document else []

def evidence_coverage(question,sources):
    evidence=expected_evidence(question)
    if not evidence:return None
    covered=0
    for group in evidence:
        if any(source['filename']==group['document'] and any(answer.lower() in source['text'].lower() for answer in group['answers']) for source in sources):covered+=1
    return covered/len(evidence)

def answer_matches(question,text):
    lowered=text.lower()
    groups=question.get('answer_groups')
    if groups is not None:return all(any(answer.lower() in lowered for answer in group) for group in groups)
    return any(answer.lower() in lowered for answer in question.get('answers',[]))

def is_abstention(text):
    paragraphs=[paragraph.strip().lower() for paragraph in text.split('\n\n') if paragraph.strip()]
    if not paragraphs:return False
    if text.startswith(NO_EVIDENCE[:30]):return True
    opening=paragraphs[0];closing=paragraphs[-1]
    return ('evidence is insufficient' in opening or
            'evidence does not contain' in opening or
            'passages do not contain' in opening or
            'evidence is insufficient' in closing or
            'insufficient to determine' in closing)

def corpus_files(args):
    root=Path(args.corpus);questions_file=Path(args.questions).resolve()
    manifest=root/'corpus-manifest.json'
    supported=('.md','.markdown','.txt','.pdf','.html','.htm','.csv','.json','.docx')
    if manifest.exists():
        names=json.loads(manifest.read_text())
        if not isinstance(names,list) or not names or any(not isinstance(n,str) for n in names) or len(set(names))!=len(names):raise ValueError('Invalid corpus manifest')
        files=[root/n for n in names]
        if any(f.resolve().parent!=root.resolve() or not f.is_file() or f.suffix.lower() not in supported or f.resolve() in (questions_file,manifest.resolve()) for f in files):raise ValueError('Invalid corpus manifest document')
    else:
        files=[f for f in root.iterdir() if f.is_file() and f.suffix.lower() in supported and f.resolve()!=questions_file]
    return sorted(files)

async def build(services,args):
    config={'backend':'faiss','embedding_model':args.embedding_model,'chunking':args.chunking,'chunk_size':args.chunk_size,'chunk_overlap':args.chunk_overlap,'search_defaults':{'mode':'rrf'}}
    kb=await services.management.call('create','local',{'name':'Evaluation corpus','config':config})
    # The labelled question file may sit beside the corpus; it must never be indexed as evidence.
    files=corpus_files(args)
    if not files:raise SystemExit(f'No supported documents in {args.corpus}')
    for file in files:
        await services.management.call('upload','local',{'kb_id':kb['id'],'filename':file.name,'content_b64':base64.b64encode(file.read_bytes()).decode(),'idempotency_key':file.name})
    ingestion=Ingestion(services.management,services.index,services.embeddings)
    started=time.perf_counter()
    while True:
        job=await services.management.call('claim','system',{})
        if not job:break
        await ingestion.execute(job)
    detail=await services.management.call('get','local',{'kb_id':kb['id']})
    if detail['status']!='ready':raise SystemExit('Indexing failed: '+json.dumps(detail.get('jobs'),indent=1)[:2000])
    print(f'Indexed {detail["document_count"]} documents into {detail["chunk_count"]} chunks with {args.embedding_model} in {time.perf_counter()-started:.1f}s.',flush=True)
    return kb['id']

async def evaluate_retrieval(services,kb_id,questions,args):
    report={}
    for mode in args.modes:
        rows=[];latencies=[]
        for q in questions:
            started=time.perf_counter()
            result=await services.retrieve(kb_id,q['question'],{'mode':mode,'top_k':args.top_k,'candidate_k':args.candidate_k,'reranker':args.reranker},'local')
            latencies.append(time.perf_counter()-started)
            sources=result['sources'];documents=[s['filename'] for s in sources]
            expected_documents=[group['document'] for group in expected_evidence(q)]
            ranks=[documents.index(document)+1 if document in documents else 0 for document in expected_documents]
            rank=min((rank for rank in ranks if rank),default=0)
            # Document-level hits can hide passage-level misses: the right file, the wrong paragraph.
            coverage=evidence_coverage(q,sources)
            rows.append({'question':q['question'],'kind':q['kind'],'split':q.get('split','calibration'),'rank':rank,'document_coverage':mean([value>0 for value in ranks]),'evidence_coverage':round(coverage,3) if coverage is not None else None,'answer_present':bool(expected_documents) and coverage==1,'top':documents[:args.top_k],
                         'passages':[{key:s.get(key) for key in ('id','filename','page','text','score','rerank_score')} for s in sources]})
        def metrics(subset):
            ranks=[r['rank'] for r in subset]
            return {'n':len(ranks),'recall@1':mean([r==1 for r in ranks]),f'recall@{args.top_k}':mean([r>0 for r in ranks]),'mrr':mean([1/r if r else 0 for r in ranks]),f'document_coverage@{args.top_k}':mean([r['document_coverage'] for r in subset]),f'evidence_coverage@{args.top_k}':mean([r['evidence_coverage'] for r in subset]),f'answer@{args.top_k}':mean([r['answer_present'] for r in subset])}
        answerable=[r for r in rows if r['kind']!='unanswerable']
        report[mode]={'all':metrics(answerable),'verbatim':metrics([r for r in rows if r['kind']=='verbatim']),'paraphrase':metrics([r for r in rows if r['kind']=='paraphrase']),'reasoning':metrics([r for r in rows if r['kind']=='reasoning']),'latency_ms':round(1000*statistics.fmean(latencies)) if latencies else None,'misses':[r for r in answerable if r['rank']!=1 or not r['answer_present']],'rows':rows}
    return report

async def evaluate_generation(services,kb_id,questions,args):
    reader=None;trace={}
    if getattr(args,'answerability_reader',''):
        from backend.app.kb.answerability import AnswerabilityReader
        reader=AnswerabilityReader(Path(args.answerability_reader))
    async def platform(action,*payload):
        if action=='kb_retrieve':
            result=await services.retrieve(payload[0],payload[1],payload[2],'local')
            if reader is not None:
                trace['pre_generation_passages']=[{key:s.get(key) for key in ('id','filename','page','text','score','rerank_score')} for s in result['sources']]
                decision=await asyncio.to_thread(reader.score,payload[1],result['sources'])
                trace['answerability']=decision
                if decision.get('status')!='scored' or type(decision.get('answerable')) is not bool:raise ValueError('Experimental answerability reader failed; generation was not called.')
                if not decision['answerable']:return {**result,'sources':[],'status':'no_matches'}
            return result
        if action=='record_vector_sources':return None
        if action=='verify_vector_sources':return await services.verify(payload[0],'local')
        raise ValueError(action)
    rows=[]
    for q in questions:
        trace={}
        started=time.perf_counter()
        grounding={};contract_status='unavailable';retrieved=[]
        async def capture(event):
            if event.get('node_id')=='retrieve' and event.get('status')=='success' and 'outputs' in event:
                retrieved.extend(json.loads(event['outputs']['sources']))
                envelope=json.loads(event['outputs']['context'])
                if 'answerability' in envelope:trace['product_answerability']=envelope['answerability']
        graph=compile_workflow(workflow(kb_id,args.generate_mode,args.generate,args.top_k,getattr(args,'candidate_k',50),getattr(args,'reranker','none'),getattr(args,'product_answerability_guard',False)),message=q['question'],platform_resolver=platform,emit=capture).graph
        try:
            result=await graph.ainvoke({'values':{}})
            text=result['values']['out']['text'];sources=json.loads(result['values']['agent']['sources']);error=''
            grounding=json.loads(result['values']['agent'].get('grounding','{}'))
            contract_status=grounding.get('compliance','unavailable')
        except Exception as exc:
            text='';sources=retrieved;error=str(exc)[:300]
            if 'grounded answer contract' in str(exc):contract_status='failed'
        cited=[s for s in sources if s['cited']]
        lowered=text.lower()
        abstained=grounding.get('abstain',is_abstention(text))
        row={'question':q['question'],'kind':q['kind'],'seconds':round(time.perf_counter()-started,1),'error':error,'answer':text,
             'retrieved':[s['filename'] for s in sources],'cited':[s['citation'] for s in cited],'unsupported':text.count('[unsupported reference]'),'abstained':abstained}
        row['contract_compliance']=contract_status
        row['passages']=[{key:s.get(key) for key in ('citation','filename','page','text','score','rerank_score')} for s in sources]
        row['grounding']=grounding
        row['split']=q.get('split','calibration')
        if reader is not None or getattr(args,'product_answerability_guard',False):row.update(trace)
        evidence=expected_evidence(q)
        if evidence:
            expected_documents={group['document'] for group in evidence}
            row['heuristic_pass']=answer_matches(q,text)
            row['expected_retrieved']=expected_documents.issubset(row['retrieved'])
            row['evidence_coverage']=round(evidence_coverage(q,sources),3)
            row['answer_retrieved']=row['evidence_coverage']==1
            row['citation_document_match']=bool(cited) and all(s['filename'] in expected_documents for s in cited)
        else:
            row['heuristic_pass']=abstained
        if getattr(args,'judge',''):
            from scripts.eval_claims import judge_claims
            row['claim_evaluation']=await judge_claims(text,sources,args.judge) if not error else {'status':'skipped_generation_error','claims':[],'supported_fraction':None}
        rows.append(row)
        flag='✓' if row['heuristic_pass'] else '✗'
        print(f'  {flag} [{q["kind"]}] {q["question"][:70]:<70} cited={row["cited"]} {row["seconds"]}s'+(f' ERROR {error}' if error else ''),flush=True)
    answerable=[r for r in rows if r['kind']!='unanswerable'];unanswerable=[r for r in rows if r['kind']=='unanswerable']
    summary={'model':args.generate,'mode':args.generate_mode,'n':len(rows),
             'answer_substring_match_rate':mean([r['heuristic_pass'] for r in answerable]),
             'answer_retrieved':mean([r['answer_retrieved'] for r in answerable]),
             'substring_match_when_answer_retrieved':mean([r['heuristic_pass'] for r in answerable if r['answer_retrieved']]),
             'false_abstention':mean([r['abstained'] for r in answerable]),
             'false_abstention_when_answer_retrieved':mean([r['abstained'] for r in answerable if r['answer_retrieved']]),
             'heuristic_abstention_on_unanswerable':mean([r['heuristic_pass'] for r in unanswerable]),
             'citation_rate':mean([bool(r['cited']) for r in answerable]),
             'citation_document_match':mean([r['citation_document_match'] for r in answerable if r['cited']]),
             'unsupported_references':sum(r['unsupported'] for r in rows),
             'errors':sum(bool(r['error']) for r in rows),
             'mean_seconds':mean([r['seconds'] for r in rows])}
    summary['contract_compliance_rate']=contract_compliance(rows)
    summary['uncited_answerable_rows']=sum(not r['cited'] for r in answerable)
    if reader is not None:summary['experimental_answerability']={'enabled':True,'threshold':0.,'pre_generation_rejections':sum(r.get('answerability',{}).get('answerable') is False and r.get('answerability',{}).get('status')=='scored' for r in rows),'production_enabled':False}
    if getattr(args,'product_answerability_guard',False):summary['product_answerability_guard']={'enabled':True,'decisions':{key:sum(r.get('product_answerability',{}).get('decision')==key for r in rows) for key in ('allow','abstain','skip')}}
    if getattr(args,'judge',''):
        judgments=[r['claim_evaluation'] for r in rows]
        summary['claim_judge']={'model':args.judge,'judged_answers':sum(j['status']=='judged' for j in judgments),'errors':sum(j['status']=='error' for j in judgments),'no_claims':sum(j['status']=='no_claims' for j in judgments),'skipped':sum(j['status']=='skipped_generation_error' for j in judgments),'mean_supported_fraction':mean([j['supported_fraction'] for j in judgments if j['supported_fraction'] is not None]),'limitation':'Model-estimated support; claim coverage and citation assignment are not independently verified. Human calibration required.'}
    return {'metric_version':2,'limitations':['Answer scoring checks expected substrings, not semantic correctness.','Citation scoring checks source documents, not claim entailment.','Abstention scoring uses the runtime contract where available; legacy outputs use a phrase-based heuristic. Neither proves that abstention is warranted.'],'summary':summary,'rows':rows}

def print_retrieval(report,top_k):
    print(f'\n| mode | doc R@1 | doc R@{top_k} | MRR | evidence@{top_k} | answer@{top_k} | verbatim answer@{top_k} | paraphrase answer@{top_k} | reasoning answer@{top_k} | ms/query |')
    print('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for mode,m in report.items():
        a=f'answer@{top_k}'
        e=f'evidence_coverage@{top_k}'
        print(f'| {mode} | {m["all"]["recall@1"]} | {m["all"][f"recall@{top_k}"]} | {m["all"]["mrr"]} | {m["all"][e]} | {m["all"][a]} | {m["verbatim"][a]} | {m["paraphrase"][a]} | {m["reasoning"][a]} | {m["latency_ms"]} |')

def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--corpus',default=str(ROOT/'evals/corpus'))
    parser.add_argument('--questions',default=str(ROOT/'evals/questions.json'))
    parser.add_argument('--embedding-model',default='embeddinggemma:latest')
    parser.add_argument('--chunking',default='paragraph',choices=['fixed','paragraph','section'])
    parser.add_argument('--chunk-size',type=int,default=800)
    parser.add_argument('--chunk-overlap',type=int,default=120)
    parser.add_argument('--modes',default='similarity,keyword,hybrid,rrf')
    parser.add_argument('--top-k',type=int,default=4)
    parser.add_argument('--candidate-k',type=int,default=50)
    parser.add_argument('--reranker',choices=['none','local_cross_encoder'],default='none')
    parser.add_argument('--generate',default='',help='Ollama chat model for the grounded-generation pass; omit for retrieval only')
    parser.add_argument('--judge',default='',help='Optional installed Ollama model for claim/evidence judgments; requires --generate')
    parser.add_argument('--generate-mode',default='rrf')
    parser.add_argument('--product-answerability-guard',action='store_true',help='Enable the actual Retrieve node guard; uses ANSWERABILITY_MODEL_DIR, no evaluation-side filtering')
    parser.add_argument('--answerability-reader',default='',help='Experimental local QA model directory; pre-generation evaluation only, never enables the product guard')
    parser.add_argument('--limit',type=int,default=0,help='Evaluate only the first N questions')
    parser.add_argument('--out',default=str(ROOT/'evals/results/latest.json'))
    args=parser.parse_args();args.modes=args.modes.split(',')
    if args.product_answerability_guard and (args.answerability_reader or not args.generate):parser.error('Product guard requires generation and cannot be combined with the offline reader flag')
    if args.judge and not args.generate:parser.error('--judge requires --generate')
    if args.answerability_reader and not args.generate:parser.error('--answerability-reader requires --generate')
    if args.judge.split(':',1)[0]=='llama3.1':parser.error('llama3.1 claim judge is retired after failed calibration; use scripts.eval_nli for offline classifier evaluation.')
    questions=json.loads(Path(args.questions).read_text())
    if args.limit:questions=questions[:args.limit]
    async def run():
        with TemporaryDirectory(prefix='relay-eval-') as directory:
            key=Fernet.generate_key()
            management=Management(f'sqlite:///{directory}/management.db',Path(directory)/'files',key)
            search=Search(f'sqlite:///{directory}/search.db',Path(directory)/'indexes',key)
            services=KnowledgeServices(Local(management),Local(search),Embeddings())
            try:
                kb_id=await build(services,args)
                output={'settings':{k:v for k,v in vars(args).items() if k not in ('out',)},'questions':len(questions),'retrieval':await evaluate_retrieval(services,kb_id,questions,args)}
                print_retrieval(output['retrieval'],args.top_k)
                if args.generate:
                    print(f'\nGrounded generation with {args.generate} over {args.generate_mode} retrieval:',flush=True)
                    output['generation']=await evaluate_generation(services,kb_id,questions,args)
                    print('\n'+json.dumps(output['generation']['summary'],indent=1))
                Path(args.out).parent.mkdir(parents=True,exist_ok=True);Path(args.out).write_text(json.dumps(output,indent=1,ensure_ascii=False))
                print(f'\nWrote {args.out}')
            finally:
                management.engine.dispose();search.engine.dispose()
    asyncio.run(run())

if __name__=='__main__':main()
