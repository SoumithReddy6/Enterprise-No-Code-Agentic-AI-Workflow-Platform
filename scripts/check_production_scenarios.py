"""Production-shaped contract tests against isolated, separately deployed API and worker.

Real HTTP, auth, SQL storage, leases, approvals, and tool HTTP transport. External
Jira/delivery systems are deterministic local HTTP fixtures; no live external writes.
Use --all to add repository checks; --live-models adds the Ollama/Docker campaign.
Every failed/skipped scenario keeps the exit code nonzero. No expected-failure waivers.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
import httpx
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1]


def gate_passes(rows):return bool(rows) and all(row.get('status')=='passed' for row in rows)


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def flow_nodes(nodes):
    return {'version':1,'name':'Scenario','nodes':nodes,'edges':[{'id':str(i),'source':a['id'],'target':b['id']} for i,(a,b) in enumerate(zip(nodes,nodes[1:]))]}


def input_node():return {'id':'input','type':'chat_input'}
def response(binding='input.message',id='out'):return {'id':id,'type':'response','inputs':{'text':binding}}


class Provider:
    def __init__(self):
        self.calls=[];self.lock=threading.Lock()
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def handle_request(self):
                body=self.rfile.read(int(self.headers.get('Content-Length',0))).decode()
                path=urlsplit(self.path).path
                with owner.lock:
                    owner.calls.append({'method':self.command,'path':path,'body':body})
                    number=sum(c['path']==path for c in owner.calls)
                if path=='/rest/api/3/search/jql':
                    query=parse_qs(urlsplit(self.path).query).get('jql',['3'])[0]
                    n=int(query) if query.isdigit() else 3
                    result=json.dumps({'issues':[{'key':f'LAB-{i}','fields':{'summary':f'Ticket {i}'}} for i in range(n)]}).encode()
                else:result=b'accepted'
                if path.startswith('/uncertain/'):
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                if path.startswith('/hold/'):time.sleep(5)
                code=503 if path.startswith('/flaky/') and number==1 else 200
                self.send_response(code);self.send_header('Content-Length',str(len(result)));self.send_header('Content-Type','application/json');self.send_header('Retry-After','0');self.end_headers()
                with contextlib.suppress(BrokenPipeError,ConnectionResetError):self.wfile.write(result)
            do_GET=handle_request
            do_POST=handle_request
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.port=self.server.server_address[1]
    def count(self,path):
        with self.lock:return sum(c['path']==path for c in self.calls)
    def close(self):self.server.shutdown();self.server.server_close()


class Lab:
    def __init__(self,directory,output):
        self.directory=Path(directory);self.output=output;self.provider=Provider();self.processes=[];self.logs=[]
        self.port=free_port();self.url=f'http://127.0.0.1:{self.port}'
        (self.directory/'SCENARIO_LAB_ONLY').touch()
        self.env={**os.environ,'DATA_DIR':str(self.directory),'DATABASE_URL':f'sqlite:///{self.directory}/relay.db','CREDENTIAL_ENCRYPTION_KEY':Fernet.generate_key().decode(),'RELAY_EMBEDDED_WORKER':'false','AUTH_REGISTRATION_MODE':'closed','SCENARIO_FIXTURE_PORT':str(self.provider.port),'PYTHONPATH':str(ROOT)}
        self.client=httpx.Client(base_url=self.url,timeout=10,trust_env=False)
    def start(self,role):
        log=open(self.output/f'{role}-{len(self.processes)}.log','w');self.logs.append(log)
        proc=subprocess.Popen([sys.executable,'-m','scripts.scenarios.lab',role,'--port',str(self.port)],cwd=ROOT,env=self.env,stdout=log,stderr=subprocess.STDOUT)
        self.processes.append(proc)
        if role=='worker':self.worker=proc
        return proc
    def boot(self):
        self.start('api')
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                if self.client.get('/api/health').status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.1)
        else:raise RuntimeError('API failed to start; inspect API log')
        r=self.client.post('/api/auth/register',json={'email':'operator@example.test','password':'Scenario-password-2026!'});assert r.status_code==201,r.text
        self.tenant=r.json()['tenant_id'];self.start('worker')
        endpoint=f'http://scenario-provider.invalid:{self.provider.port}'
        self.http=self.connection('http',endpoint);self.jira=self.connection('jira',endpoint)
    def connection(self,provider,endpoint):
        r=self.client.post('/api/connections',json={'name':provider,'provider':provider,'endpoint':endpoint});assert r.status_code==200,r.text;return r.json()['id']
    def submit(self,w):
        r=self.client.post('/api/runs',json={'workflow':w,'message':'Customer case'});assert r.status_code==201,r.text;return r.json()['id']
    def wait(self,id,statuses=('success','failed','cancelled','awaiting_approval'),timeout=20):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            r=self.client.get('/api/runs/'+id);assert r.status_code==200,r.text
            run=r.json()
            if run['status'] in statuses:return run
            time.sleep(.05)
        raise AssertionError(f'Run {id} did not reach {statuses}: {run["status"]}')
    def decide(self,id,approval,action='approve',digest=None):
        return self.client.post(f'/api/runs/{id}/{action}',json={'approval_id':approval['id'],'digest':digest or approval['digest']})
    def tool(self,path,write=False,approval=None,on_error='fail',retry=0):
        cfg={'connection_id':self.http,'path':path,'method':'POST' if write else 'GET','enable_writes':write,'body':'{input}'}
        if approval is not None:cfg['approval']=approval
        return {'id':'work','type':'tool_http','config':cfg,'inputs':{'input':'input.message'},'on_error':on_error,'retry':{'attempts':retry,'base_delay':0}}
    def batch(self,path,n=3,write=True,approval=True,retry=0,limit=100):
        body=self.tool(path,write,approval,retry=retry);body['inputs']={}
        w=flow_nodes([input_node(),{'id':'tickets','type':'tool_jira','config':{'connection_id':self.jira,'jql':str(n)},'inputs':{'input':'input.message'}},{'id':'each','type':'for_each','inputs':{'items':'tickets.items'},'config':{'body':'work','max_items':limit}},response()])
        w['nodes'].append(body);w['edges'].append({'id':'body','source':'each','target':'work','kind':'loop','sourceHandle':'body','targetHandle':'input'})
        return w
    def read_job(self,id):
        import sqlite3
        with sqlite3.connect(self.directory/'relay.db') as db:
            return db.execute('SELECT status,owner,lease_until FROM execution_jobs WHERE run_id=?',(id,)).fetchone()
    def close(self):
        for p in reversed(self.processes):
            if p.poll() is None:p.terminate()
        for p in self.processes:
            try:p.wait(timeout=8)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        self.client.close();self.provider.close()
        for f in self.logs:f.close()


def scenarios(lab):
    def approval_batch():
        path='/deliver/batch';id=lab.submit(lab.batch(path));approvals=[];reviewed=[]
        for index in range(3):
            run=lab.wait(id);assert run['status']=='awaiting_approval',run.get('error')
            pending=next(a for a in run['approvals'] if a['status']=='pending');approvals.append(pending['id']);reviewed.append(pending['payload']['body'])
            assert lab.provider.count(path)==index
            assert lab.read_job(id)==('awaiting_approval','',0.0)
            assert lab.decide(id,pending,digest='0'*64).status_code==409
            assert lab.provider.count(path)==index
            assert lab.decide(id,pending).status_code==200
            assert lab.decide(id,pending).status_code==200
        run=lab.wait(id);assert run['status']=='success',run.get('error')
        assert len(set(approvals))==3 and lab.provider.count(path)==3
        received=[json.loads(c['body']) for c in lab.provider.calls if c['path']==path]
        assert received==reviewed,'Delivered body differs from the exact reviewed body'
        assert len(run['loop_progress']['each'])==3
        return {'run_id':id,'writes':3,'distinct_approvals':3}
    def rejected_batch():
        path='/deliver/reject';id=lab.submit(lab.batch(path));run=lab.wait(id);a=run['approvals'][0]
        assert lab.decide(id,a,'reject').status_code==200
        run=lab.wait(id);assert run['status']=='cancelled' and lab.provider.count(path)==0
        assert lab.client.post(f'/api/runs/{id}/resume').status_code==409
        return {'run_id':id,'writes':0}
    def uncertain(policy):
        path='/uncertain/'+policy;w=flow_nodes([input_node(),lab.tool(path,True,False,policy),response()])
        if policy=='route':w['nodes'].append(response(id='fallback'));w['edges'].append({'id':'err','source':'work','target':'fallback','sourceHandle':'error'})
        id=lab.submit(w);run=lab.wait(id)
        assert run['status']=='failed' and 'uncertain' in run['error'].lower(),run
        assert lab.provider.count(path)==1
        assert not any(e.get('node_id') in ('out','fallback') and e['status']=='success' for e in run['events'])
        assert lab.client.post(f'/api/runs/{id}/resume').status_code==409
        return {'run_id':id,'received_writes':1,'resume_refused':True}
    def restarts():
        path='/deliver/restart';id=lab.submit(lab.batch(path,n=2));run=lab.wait(id)
        assert lab.decide(id,run['approvals'][0]).status_code==200
        run=lab.wait(id);assert run['status']=='awaiting_approval'
        assert lab.provider.count(path)==1 and len(run['loop_progress']['each'])==1
        lab.worker.kill();lab.worker.wait();lab.start('worker')
        a=next(a for a in run['approvals'] if a['status']=='pending');assert lab.decide(id,a).status_code==200
        run=lab.wait(id);assert run['status']=='success',run.get('error');assert lab.provider.count(path)==2
        return {'run_id':id,'received_writes':2,'worker_replaced':True}
    def crash_after_settled():
        path='/deliver/crash-settled';(lab.directory/'crash-after-item').touch()
        id=lab.submit(lab.batch(path,n=2,approval=False))
        deadline=time.monotonic()+15
        while lab.worker.poll() is None and time.monotonic()<deadline:time.sleep(.05)
        assert lab.worker.poll()==91,'Crash failpoint did not fire'
        before=lab.client.get('/api/runs/'+id).json()
        assert len(before.get('loop_progress',{}).get('each',{}))==1
        assert lab.provider.count(path)==1
        lab.start('worker')
        # Use the real 30-second lease expiry; do not mutate the database to speed recovery.
        run=lab.wait(id,timeout=40);assert run['status']=='success',run.get('error')
        assert lab.provider.count(path)==2,'Completed item was sent again'
        return {'run_id':id,'writes':2,'crash_after_sql_commit':True,'lease_expiry':'real'}
    def crash_during_write():
        path='/hold/crash-uncertain';id=lab.submit(lab.batch(path,n=2,approval=False))
        deadline=time.monotonic()+15
        while lab.provider.count(path)==0 and time.monotonic()<deadline:time.sleep(.02)
        assert lab.provider.count(path)==1,'Receiver did not receive write'
        lab.worker.kill();lab.worker.wait();lab.start('worker')
        run=lab.wait(id,timeout=40)
        assert run['status']=='failed' and 'external write' in run['error'],run
        assert lab.provider.count(path)==1,'Uncertain item was sent again'
        assert lab.client.post(f'/api/runs/{id}/resume').status_code==409
        return {'run_id':id,'writes':1,'uncertain_resume_refused':True}
    def retries():
        path='/flaky/read';id=lab.submit(flow_nodes([input_node(),lab.tool(path,retry=2),response('work.text')]))
        run=lab.wait(id);assert run['status']=='success' and lab.provider.count(path)==2
        assert any(e['status']=='retrying' for e in run['events']);return {'run_id':id,'requests':2}
    def body_retry():
        path='/flaky/body';id=lab.submit(lab.batch(path,n=1,write=False,retry=2));run=lab.wait(id)
        assert lab.provider.count(path)==2,{'requests':lab.provider.count(path),'result':run.get('checkpoints',{}).get('each')}
        assert run['checkpoints']['each']['failed']=='0';return {'run_id':id}
    def routed_binding():
        w=flow_nodes([input_node(),lab.tool('/read',on_error='route'),response('work.text')]);w['nodes'].append(response('work.text','fallback'));w['edges'].append({'id':'err','source':'work','target':'fallback','sourceHandle':'error'})
        r=lab.client.post('/api/validate',json=w);assert not r.json()['valid'],r.json();return {'invalid_binding_rejected':True}
    def truncation():
        id=lab.submit(lab.batch('/read',n=3,write=False,limit=1));run=lab.wait(id)
        assert run['truncated'] is True,{'run_id':id,'truncated':run.get('truncated')};return {'run_id':id}
    def denied_ssrf():
        conn=lab.connection('http','http://127.0.0.1:1');tool=lab.tool('/read');tool['config']['connection_id']=conn
        id=lab.submit(flow_nodes([input_node(),tool,response()]));run=lab.wait(id)
        assert run['status']=='failed' and 'public' in run['error'];return {'run_id':id}
    def registration_closed():
        with httpx.Client(base_url=lab.url,trust_env=False) as client:
            r=client.post('/api/auth/register',json={'email':'second@example.test','password':'Second-password-2026!'});assert r.status_code==403,r.text
            assert client.get('/api/runs').status_code==401
        return {'second_registration':403,'anonymous_runs':401}
    def tenant_isolation():
        from backend.app.storage import Store
        # Seed a second synthetic tenant as setup; test isolation via real login/HTTP.
        store=Store(lab.env['DATABASE_URL'],lab.env['CREDENTIAL_ENCRYPTION_KEY'].encode())
        from backend.app.auth import AccountRecord,password_hash
        from sqlalchemy.orm import Session
        with Session(store.engine) as s:
            s.add(AccountRecord(id='scenario-other-account',tenant_id='scenario-other-tenant',email='other@example.test',password_hash=password_hash('Other-password-2026!')));s.commit()
        store.engine.dispose()
        id=lab.submit(flow_nodes([input_node(),response()]));lab.wait(id)
        with httpx.Client(base_url=lab.url,trust_env=False) as client:
            r=client.post('/api/auth/login',json={'email':'other@example.test','password':'Other-password-2026!'});assert r.status_code==200,r.text
            assert client.get('/api/runs/'+id).status_code==404
            assert client.get('/api/connections').json()==[]
        return {'foreign_run':404,'foreign_connections':0}
    def save_conflict():
        w=flow_nodes([input_node(),response()]);r=lab.client.post('/api/workflows',json=w);assert r.status_code==201,r.text;saved=r.json()
        # GET carries the concurrency token consumed by PUT.
        current=lab.client.get('/api/workflows/'+saved['id']).json()
        token=current['updated_at'];body={**w,'updated_at':token}
        a=lab.client.put('/api/workflows/'+saved['id'],json={**body,'name':'First tab'})
        b=lab.client.put('/api/workflows/'+saved['id'],json={**body,'name':'Stale tab'})
        assert a.status_code==200 and b.status_code==409,(a.text,b.text)
        return {'fresh':200,'stale':409}
    def approval_stale_connection():
        conn=lab.connection('http',f'http://scenario-provider.invalid:{lab.provider.port}')
        tool=lab.tool('/deliver/stale',True);tool['config']['connection_id']=conn
        id=lab.submit(flow_nodes([input_node(),tool,response()]));run=lab.wait(id);a=run['approvals'][0]
        r=lab.client.put('/api/connections/'+conn,json={'name':'changed','provider':'http','endpoint':f'http://scenario-provider.invalid:{lab.provider.port}/changed'});assert r.status_code==200
        assert lab.decide(id,a).status_code==200
        run=lab.wait(id);assert run['status']=='failed' and lab.provider.count('/deliver/stale')==0
        return {'run_id':id,'writes':0}
    return [
        ('S01','Batch1','Ticket batch: approve each exact payload',approval_batch),
        ('S02','Batch1','Reject customer reply batch without sending',rejected_batch),
        ('S03','Batch1','Uncertain delivery cannot continue',lambda:uncertain('continue')),
        ('S04','Batch1','Uncertain delivery cannot route onward',lambda:uncertain('route')),
        ('S05','Batch1','Worker restart between batch approvals',restarts),
        ('S15','Batch1','Abrupt crash after a durable write item',crash_after_settled),
        ('S16','Batch1','Kill worker after receiver accepts a write',crash_during_write),
        ('S06','A3','Transient vendor read retries',retries),
        ('S07','A3-A4','Loop body retries actually apply',body_retry),
        ('S08','A3','Invalid recovery binding rejected before execution',routed_binding),
        ('S09','A4','Incomplete batch visible at run level',truncation),
        ('S10','Security','SSRF denied outside fixture boundary',denied_ssrf),
        ('S11','Security','Closed registration and anonymous denial',registration_closed),
        ('S12','Security','Tenant isolation through authenticated HTTP',tenant_isolation),
        ('S13','Storage','Two tabs cannot overwrite silently',save_conflict),
        ('S14','Approvals','Changed destination invalidates reviewed write',approval_stale_connection),
    ]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',default='evals/results/production-scenarios');parser.add_argument('--all',action='store_true');parser.add_argument('--live-models',action='store_true');args=parser.parse_args()
    output=Path(args.out).resolve();output.mkdir(parents=True,exist_ok=True);rows=[]
    source_files=[*sorted((ROOT/'backend/app').rglob('*.py')),*sorted((ROOT/'scripts/scenarios').glob('*.py')),Path(__file__)]
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    started=time.time();head_at_start=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    def record(id,phase,name,fn):
        stamp=time.monotonic()
        try:details=fn();row={'id':id,'phase':phase,'name':name,'status':'passed','details':details}
        except Exception as exc:row={'id':id,'phase':phase,'name':name,'status':'failed','error':f'{type(exc).__name__}: {exc}'}
        row['seconds']=round(time.monotonic()-stamp,3);rows.append(row);print(f'{id} {row["status"]}: {name}',flush=True)
        if row['status']=='failed':print(row['error'][:700],flush=True)
    with tempfile.TemporaryDirectory(prefix='relay-scenario-lab-') as temp:
        lab=Lab(temp,output)
        try:
            lab.boot()
            for id,phase,name,fn in scenarios(lab):record(id,phase,name,fn)
            if args.live_models:
                def live_loop_usage():
                    r=lab.client.post('/api/models',json={'provider':'ollama','model':'llama3.1:latest'})
                    assert r.status_code==201,r.text
                    w=lab.batch('/unused',n=2,write=False)
                    body=w['nodes'][-1];body['type']='agent';body['config']={'provider':'ollama','model':'llama3.1:latest','temperature':0,'max_tokens':32,'system':'Reply with exactly OK.'}
                    id=lab.submit(w);run=lab.wait(id,timeout=60)
                    assert run['status']=='success',run.get('error')
                    usage=[e['usage'] for e in run['events'] if e.get('usage')]
                    assert usage and sum(u.get('prompt_tokens',0)+u.get('completion_tokens',0) for u in usage)>0,{'run_id':id,'completed_items':len(run.get('loop_progress',{}).get('each',{})),'usage_events':usage}
                    return {'run_id':id,'usage_events':usage}
                record('L01','Batch1-budget','Real Ollama batch usage survives into durable events',live_loop_usage)
            # Logs contain only synthetic scenario data. Store run histories as evidence.
            histories=[]
            for item in lab.client.get('/api/runs').json():histories.append(lab.client.get('/api/runs/'+item['id']).json())
            (output/'runs.json').write_text(json.dumps(histories,indent=2))
            (output/'provider-requests.json').write_text(json.dumps(lab.provider.calls,indent=2))
        except Exception as exc:rows.append({'id':'LAB','phase':'infrastructure','status':'failed','error':repr(exc)})
        finally:lab.close()
    def command(name,cmd):
        with open(output/(name+'.log'),'w') as log:r=subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        assert r.returncode==0,f'{name} exited {r.returncode}; see {name}.log'
        return {'command':cmd,'exit_code':r.returncode}
    record('M01','Batch1-contract','Mixed approval modes inside one loop agent',lambda:command('mixed-approval',[sys.executable,'-m','scripts.scenarios.mixed_approval']))
    if args.all:
        for name,cmd in [('backend',[sys.executable,'-m','pytest','backend/tests','-q']),('frontend-tests',['npm','--prefix','frontend','test']),('typecheck',['npm','--prefix','frontend','run','typecheck']),('lint',['npm','--prefix','frontend','run','lint']),('build',['npm','--prefix','frontend','run','build'])]:record(name,'repository',name,lambda n=name,c=cmd:command(n,c))
    if args.live_models:
        def live():
            latest=ROOT/'evals/results/workflows-latest.json';original=latest.read_bytes() if latest.exists() else None
            try:
                result=command('live-models',[sys.executable,'-u','-m','scripts.check_workflows'])
                data=json.loads(latest.read_text());(output/'live-models.json').write_text(json.dumps(data,indent=2))
                assert data['results'] and all(r['ok'] for r in data['results']),'Live workflow assertions failed'
                return result
            finally:
                if original is not None:latest.write_bytes(original)
                elif latest.exists():latest.unlink()
        record('live-models','integration','Ollama, embeddings, Docker and knowledge campaign',live)
    changed=[str(p.relative_to(ROOT)) for p in source_files if hashlib.sha256(p.read_bytes()).hexdigest()!=hashes[str(p.relative_to(ROOT))]]
    if changed:rows.append({'id':'provenance','status':'failed','error':'Source changed during campaign','files':changed})
    report={'head_at_start':head_at_start,'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'working_tree':subprocess.check_output(['git','status','--short'],cwd=ROOT,text=True),'started_at':started,'completed_at':time.time(),'source_sha256':hashes,'source_files_changed':changed,'deployment':'isolated localhost API process + worker process + HTTP provider fixture; SQLite','external_services':'Jira/delivery fixtures over real HTTP; no live external writes','rows':rows,'passed':gate_passes(rows)}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(f'Report: {output}/report.json',flush=True)
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
