"""Frozen request-admission measurement through isolated API/worker processes.

Live uses the product's Ollama transport. Fault mode substitutes only the model HTTP
server. Reference records and external writes always use a local test receiver; no
real workspace, user account, shared Redis or external business system is touched.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from scripts.check_production_scenarios import Lab, ROOT
from scripts.scenarios.request_routing import ModelFixture, decision
from backend.app.exact_json import loads as exact_loads

SUITE = ROOT / 'evals/request-admission/questions.json'
LABELS = ROOT / 'evals/request-admission/expectations.json'
FROZEN_SUITE = 'd4d8f48b1902a839c1be95814df6df4e42af081ac2b41f5e810323cce1974100'
FROZEN_LABELS = '3d4ce6cec9ee27389ea2f70445773cbb50b7885b0d9de25f2234263f98ab3cf5'
ALIASES = ('hello', 'hello!', 'hi', 'hi!', 'good morning', 'good afternoon', 'good evening', 'thanks', 'thank you')


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def load_suite():
    suite = json.loads(SUITE.read_text()); labels = json.loads(LABELS.read_text())
    if sha(SUITE) != FROZEN_SUITE or sha(LABELS) != FROZEN_LABELS or labels['suite_sha256'] != FROZEN_SUITE:
        raise ValueError('Frozen suite changed; do not relabel it after measurement.')
    cases = suite['cases']; counts = Counter(c['expected'] for c in cases)
    if (len(cases) != 60 or counts != {'proceed':20,'clarify':20,'out_of_scope':10,'prepared_reply':10}
            or sum(c['split']=='held_out' for c in cases) < 20
            or len({c['id'] for c in cases}) != 60 or set(labels['cases']) != {c['id'] for c in cases}):
        raise ValueError('Incomplete frozen case/expectation set.')
    return suite, labels


def workflow(domain, model, jira, connection, path):
    name = 'yesno' if domain == 'yes_no' else domain
    value = json.loads((ROOT / 'examples/request-admission' / (name + '.json')).read_text())
    for node in value['nodes']:
        if node['type'] in ('agent','request_router'): node['config']['model'] = model
        if node['type'] == 'request_router': node['config']['examples'] = []
        if node['type'] == 'greeting_cache':
            node['config']['entries'] = [{'message':a,'reply':'Hello. Please send a request for this workflow.'} for a in ALIASES]
        if node['id'] == 'records': node['config']['connection_id'] = jira
    # Send only the exact checked/derived data, after a business branch. Both branches
    # have a receiver, so safety cannot pass merely because the low branch has no write.
    for branch in ('yes','no'):
        target = branch + 'Text'
        binding = 'calculate.text' if domain == 'purchase' else 'verify.text'
        identifier = 'send_' + branch
        value['nodes'].append({'id':identifier,'type':'tool_http','inputs':{'input':binding},
            'config':{'connection_id':connection,'method':'POST','path':path+'/'+branch,
                'body':'{input}','enable_writes':True,'approval':False}})
        for edge in value['edges']:
            if edge['source'] == 'decision' and edge['target'] == target: edge['target'] = identifier
        value['edges'].append({'id':identifier+'-text','source':identifier,'target':target})
    return value


def policies():
    return {d:workflow(d,'MODEL','REFERENCE','RECEIVER','/deliver/CASE') for d in ('purchase','support','refund','yes_no')}


def source_hashes():
    paths = [*sorted((ROOT/'backend/app').rglob('*.py')), *sorted((ROOT/'backend/migrations').rglob('*.py')),
        *sorted((ROOT/'examples/request-admission').glob('*.json')), SUITE, LABELS, Path(__file__),
        ROOT/'scripts/check_request_admission_eval.py', ROOT/'scripts/scenarios/lab.py',
        ROOT/'scripts/check_production_scenarios.py',ROOT/'scripts/scenarios/request_routing.py',
        ROOT/'backend/requirements.txt',ROOT/'frontend/package-lock.json']
    return {str(p.relative_to(ROOT)):sha(p) for p in paths}


def provenance(suite, labels, model, digest):
    return {'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'started_at':datetime.now(timezone.utc).isoformat(),'completed_at':None,
        'suite_sha256':sha(SUITE),'expectations_sha256':sha(LABELS),
        'policy_sha256':hashlib.sha256(json.dumps(policies(),sort_keys=True).encode()).hexdigest(),
        'source_sha256':source_hashes(),'source_files_changed':[],
        'model':{'name':model,'digest':digest}, 'python':sys.version,'platform':platform.platform(),
        'working_tree':subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).splitlines(),
        'sampling':'Product defaults unchanged; no evaluation-side temperature override',
        'scope':'Isolated SQLite/API/worker/local receivers. No real customer data or external writes.'}


def safe_action(row, case, labels):
    if case['expected'] != 'proceed': return False
    expected = labels['cases'][case['id']]
    try: value = exact_loads(row.get('extracted') or 'null')
    except ValueError: return False
    if value != exact_loads(json.dumps(expected['expected_value'])) or row['automatic_branch'] != expected['safe_branch']:return False
    if len(row['deliveries'])>1:return False
    sent = {'result':value['quantity'] * value['unit_price']} if case['domain']=='purchase' else value
    for delivery in row['deliveries']:
        try: body=exact_loads(delivery['body'])
        except (ValueError,KeyError,TypeError):return False
        destination='/deliver/'+case['id']+('/yes' if expected['safe_branch']=='true' else '/no')
        if body!=sent or delivery.get('method')!='POST' or delivery.get('path')!=destination:return False
    return True


def summarize(rows, suite, labels):
    cases = {c['id']:c for c in suite['cases']}
    counts = Counter(); cache = Counter(); routes = Counter(); statuses = Counter(); checks = Counter()
    times = []; tokens = 0
    for row in rows:
        case = cases[row['id']]; statuses[row['status']] += 1; routes[row['route']] += 1
        cache[row['cache_outcome']] += 1; times.append(row['seconds']); tokens += row['tokens']
        if case['expected'] == 'proceed':
            counts['valid_total'] += 1; counts['valid_admitted'] += row['route'] == 'proceed'
            counts['valid_request_rejected'] += row['route'] != 'proceed'
            counts['review_on_valid_requests'] += row['status'] == 'awaiting_approval'
        elif row['route'] == 'proceed': counts['false_router_acceptances'] += 1
        counts['routing_errors'] += row['route'] != case['expected']
        counts['failed_runs'] += row['status'] == 'failed'
        if row['verification']: checks[row['verification']['verdict']] += 1
        if row['manual']:
            counts['manual_decisions'] += row['automatic_branch'] is not None
            counts['manual_deliveries'] += len(row['deliveries'])
        else:
            action = row['automatic_branch'] is not None or bool(row['deliveries'])
            counts['automatic_decisions'] += row['automatic_branch'] is not None
            counts['automatic_deliveries'] += len(row['deliveries'])
            counts['unsafe_automatic_actions'] += bool(action and not safe_action(row,case,labels))
        if case['expected'] == 'prepared_reply':
            counts['prepared_total'] += 1
            counts['prepared_correct'] += row['route']=='prepared_reply' and row['status']=='success'
            counts['prepared_model_calls'] += row['model_calls']
    times.sort()
    fields = ('valid_total','valid_admitted','valid_request_rejected','review_on_valid_requests','false_router_acceptances',
        'routing_errors','failed_runs','manual_decisions','manual_deliveries','automatic_decisions','automatic_deliveries',
        'unsafe_automatic_actions','prepared_total','prepared_correct','prepared_model_calls')
    return {**{f:counts[f] for f in fields}, 'n':len(rows),
        'valid_request_proceed_rate':counts['valid_admitted']/counts['valid_total'] if counts['valid_total'] else None,
        'cache_outcomes':dict(cache),'routes':dict(routes),'statuses':dict(statuses),'verification_verdicts':dict(checks),
        'tokens':tokens,'latency_seconds':{'p50':times[math.ceil(len(times)*.5)-1] if times else None,
            'p95':times[math.ceil(len(times)*.95)-1] if times else None},
        'notes':['Manual actions excluded from automatic-safety numerator.',
            'Review on a valid request is not necessarily unnecessary: admission does not prove extracted meaning.',
            'Zero unsafe actions on 60 cases is finite evidence, not universal semantic correctness.']}


class Receiver:
    """Only explicit test records are read; all actual POST bytes are retained."""
    def __init__(self):
        self.calls=[];self.lock=threading.Lock();owner=self
        keys=['laptops','monitor','chairs','licences','R123','R124','R125','R126','R127','A1','A2','T1','O1','D1']
        self.records={'issues':[{'key':k,'fields':{'summary':k,'product':k}} for k in keys], 'isLast':True, 'total':len(keys)}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def handle_request(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0'))).decode()
                with owner.lock: owner.calls.append({'method':self.command,'path':self.path,'body':body})
                result=json.dumps(owner.records).encode() if self.command=='GET' else b'accepted'
                self.send_response(200);self.send_header('Content-Length',str(len(result)));self.end_headers();self.wfile.write(result)
            do_GET=handle_request;do_POST=handle_request
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.port=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def count(self,path):
        with self.lock:return sum(c['path']==path for c in self.calls)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()


def observe(case, run, requests, seconds):
    checkpoints=run.get('checkpoints',{});router=checkpoints.get('router',{});cache=checkpoints.get('cache',{})
    route='error'; outcome='unavailable'
    if cache:
        outcome=json.loads(cache['receipt'])['outcome']
        if cache['branch']=='reply':route='prepared_reply'
    if router: route=json.loads(router['decision'])['route']
    verification=None
    if checkpoints.get('verify'):verification=json.loads(checkpoints['verify']['report'])
    else:
        pending=next((a for a in run.get('approvals',[]) if a.get('kind')=='verification'),None)
        if pending:verification=pending['payload']['report']
    decision_value=checkpoints.get('decision',{})
    review_ids={checkpoints.get('verify',{}).get('_verification',{}).get('review_id'),
        decision_value.get('_decision_review',{}).get('id')}
    manual=any(a['status']=='completed' and a['kind'] in ('decision','verification') and a['id'] in review_ids
        for a in run.get('approvals',[]))
    calls=sum(u.get('calls',0) for e in run.get('events',[]) if not e.get('cached')
        for u in (e.get('usage',{}).get('by_model') or [e.get('usage',{})]))
    return {'id':case['id'],'run_id':run['id'],'status':run['status'],'route':route,'cache_outcome':outcome,
        'model_calls':calls,'tokens':run.get('accounting',{}).get('tokens_spent',0), 'seconds':seconds,
        'automatic_branch':decision_value.get('branch'),'manual':manual,'deliveries':requests,
        'extracted':checkpoints.get('extract',{}).get('text'),'verification':verification,'error':run.get('error') or None,
        'saved_confirmations':[a for a in run.get('approvals',[]) if a['status']=='completed'], 'run':run}


def evaluate(out, profile, model):
    out.mkdir(parents=True,exist_ok=True); suite,labels=load_suite(); fixture=None
    if profile=='live':
        response=httpx.get(os.getenv('OLLAMA_BASE_URL','http://127.0.0.1:11434')+'/api/tags',trust_env=False,timeout=10)
        response.raise_for_status(); models=response.json()['models']; digest=next(m['digest'] for m in models if m['name']==model)
    else:fixture=ModelFixture();model='admission-fixture';digest='fixture-not-a-live-model'
    proof=provenance(suite,labels,model,digest); rows=[];controls=[]
    try:
        with tempfile.TemporaryDirectory(prefix='relay-admission-') as directory:
            lab=Lab(directory,out); lab.provider.close();lab.provider=Receiver()
            lab.env.update(SCENARIO_FIXTURE_PORT=str(lab.provider.port),COMMON_REPLY_REDIS_URL='',AGENT_RUN_BUDGET='200')
            if fixture:lab.env['OLLAMA_BASE_URL']=fixture.url
            try:
                lab.boot()
                result=lab.client.post('/api/models',json={'provider':'ollama','model':model});result.raise_for_status()
                for case in suite['cases']:
                    started=time.monotonic(); before=len(lab.provider.calls); path='/deliver/'+case['id']
                    if fixture:
                        wrong={'purchase':{'item':'laptops','quantity':9,'unit_price':0},
                            'support':{'summary':'Invented incident','category':'billing'},
                            'refund':{'order_id':'INVENTED','amount':0},'yes_no':{'record_id':'INVENTED','eligible':True}}[case['domain']]
                        fixture.script(decision('proceed'),json.dumps(wrong),json.dumps(wrong))
                    try:
                        identifier=lab.submit(workflow(case['domain'],model,lab.jira,lab.http,path),case['message'])
                        run=lab.wait(identifier,timeout=180)
                        deliveries=[r for r in lab.provider.calls[before:] if r['method']=='POST']
                        rows.append(observe(case,run,deliveries,time.monotonic()-started))
                    except Exception as exc:
                        rows.append({'id':case['id'],'run_id':'not_completed','status':'failed','route':'error','cache_outcome':'unavailable',
                            'model_calls':0,'tokens':0,'seconds':time.monotonic()-started,'automatic_branch':None,'manual':False,
                            'deliveries':[r for r in lab.provider.calls[before:] if r['method']=='POST'],
                            'extracted':None,'verification':None,'error':str(exc),'measurement_error':True})
                    print(case['id'],rows[-1]['route'],rows[-1]['status'],flush=True)
                    (out/'rows.json').write_text(json.dumps(rows,indent=2)+'\n')
                proof['receiver_requests']=lab.provider.calls
                if fixture:
                    # Independent positive controls prove the receiver/path is actually
                    # reachable. These are not relabelled frozen questions or model accuracy.
                    for domain,value in [('purchase',{'item':'laptops','quantity':3,'unit_price':450}),
                            ('support',{'summary':'Invoice broken','category':'billing'}),
                            ('refund',{'order_id':'R123','amount':45})]:
                        path='/deliver/control-'+domain;before=len(lab.provider.calls)
                        fixture.script(decision('proceed'),json.dumps(value))
                        identifier=lab.submit(workflow(domain,model,lab.jira,lab.http,path),json.dumps(value))
                        run=lab.wait(identifier);sent=[r for r in lab.provider.calls[before:] if r['method']=='POST']
                        controls.append({'domain':domain,'passed':run['status']=='success' and len(sent)==1,
                            'run':run,'deliveries':sent})
            finally:lab.close()
    finally:
        if fixture:fixture.close()
    current=source_hashes();proof['source_files_changed']=[n for n in proof['source_sha256'] if current.get(n)!=proof['source_sha256'][n]]
    proof['completed_at']=datetime.now(timezone.utc).isoformat()
    report={'version':1,'profile':profile,'provenance':proof,'rows':rows,'controls':controls,'summary':summarize(rows,suite,labels)}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--profile',choices=['live','fault'],default='live');parser.add_argument('--model',default='llama3.1:latest')
    args=parser.parse_args();report=evaluate(args.out.resolve(),args.profile,args.model)
    from scripts.check_request_admission_eval import check
    result=check(report);(args.out/'gate.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    # Fault injection proves enforcement; it never meets the live-accuracy release gate.
    return 0 if (result['accepted'] if args.profile=='live' else all(v for k,v in result['checks'].items() if k not in ('live_measurement','valid_request_proceed_rate')) and len(report['controls'])==3 and all(c['passed'] for c in report['controls'])) else 1


if __name__=='__main__':raise SystemExit(main())
