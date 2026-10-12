"""Typed review with real API/worker processes, SIGKILL and isolated SQL databases.

Synthetic Ollama output and a local receiver test enforcement and recovery; they do not
measure model semantic accuracy. PostgreSQL schemas and SQLite databases are disposable.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from sqlalchemy import update
from sqlalchemy.orm import Session
from scripts.check_milestone_storage import database,deployed,wait_for,migrations
from scripts.scenarios.request_routing import ModelFixture,decision
from scripts.check_production_scenarios import ROOT
from backend.app.storage import JobRecord


def workflow(connection,path):
    return {'name':'Verified delivery','contract':2,'nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'cache','type':'greeting_cache','inputs':{'message':'input.message'}},
        {'id':'router','type':'request_router','inputs':{'message':'input.message'},'config':{'purpose':'Handle purchase requests','provider':'ollama','model':'review-fixture'}},
        {'id':'extract','type':'llm','inputs':{'prompt':'input.message'},'config':{'provider':'ollama','model':'review-fixture'}},
        {'id':'verify','type':'verify_extraction','inputs':{'original':'input.message','value':'extract.text'},'config':{
            'expected_schema':{'type':'object','required':['amount'],'properties':{'amount':{'type':'number','minimum':0}}},
            'required_fields':['/amount'],'field_rules':[{'field':'/amount','kind':'quoted'},{'field':'/amount','kind':'enum','allowed_values':[0,1200]}]}},
        {'id':'send','type':'tool_http','inputs':{'input':'verify.text'},'config':{'connection_id':connection,'method':'POST','path':path,'body':'{input}','enable_writes':True,'approval':False}},
        {'id':'out','type':'response','inputs':{'text':'send.text'}},
        *[{'id':n+'Reply','type':'response','inputs':{'text':n+'.reply'}} for n in ('cache','router','verify')]],
        'edges':[
            {'id':'a','source':'input','target':'cache'},
            {'id':'b','source':'cache','target':'router','sourceHandle':'continue'},
            {'id':'c','source':'router','target':'extract','sourceHandle':'continue'},
            {'id':'d','source':'extract','target':'verify'},
            {'id':'e','source':'verify','target':'send','sourceHandle':'continue'},
            {'id':'f','source':'send','target':'out'},
            *[{'id':n+'Reply','source':n,'target':n+'Reply','sourceHandle':'reply'} for n in ('cache','router','verify')]]}


def start(lab,stage=''):
    env={**lab.env,'REVIEW_FAILPOINT':stage}
    log=open(lab.output/f'review-worker-{len(lab.processes)}.log','w');lab.logs.append(log)
    process=subprocess.Popen([sys.executable,'-m','scripts.scenarios.request_review_worker'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    lab.processes.append(process);return process


def kill(process):
    process.send_signal(signal.SIGKILL);process.wait(timeout=10)
    assert process.returncode==-signal.SIGKILL


def recover(store,identifier):
    # The killed process cannot renew. Expire only this disposable job, rather than
    # waiting a real lease interval or replacing fencing with a mock clock.
    with Session(store.engine) as session:
        session.execute(update(JobRecord).where(JobRecord.run_id==identifier).values(lease_until=0));session.commit()


def probe(kind,stage,out):
    model=ModelFixture()
    try:
        with database(kind) as url,deployed(url,out) as (lab,store):
            lab.env['OLLAMA_BASE_URL']=model.url
            response=lab.client.post('/api/models',json={'provider':'ollama','model':'review-fixture'})
            assert response.status_code==201,response.text
            model.script(decision('proceed'),'{"amount":0}')
            path='/deliver/'+kind+'-'+stage
            if stage=='pause':process=start(lab,'pause')
            else:process=start(lab)
            identifier=lab.submit(workflow(lab.http,path),'Unrelated question')
            pending=lab.wait(identifier);assert pending['status']=='awaiting_approval',pending.get('error')
            review=pending['approvals'][0]
            assert review['kind']=='verification' and lab.provider.count(path)==0
            with Session(store.engine) as session:
                job=session.get(JobRecord,identifier);assert job.owner=='' and job.lease_until==0
            if stage=='pause':wait_for(lambda:(lab.directory/'review-barrier').exists());kill(process)
            else:process.terminate();process.wait(timeout=10)
            assert lab.decide(identifier,review,digest='0'*64).status_code==409
            assert lab.decide(identifier,review).status_code==200
            assert lab.decide(identifier,review).status_code==200
            process=start(lab,'' if stage=='pause' else stage)
            if stage!='pause':
                wait_for(lambda:(lab.directory/'review-barrier').exists(),30)
                assert lab.provider.count(path)==(0 if stage=='confirmation' else 1)
                kill(process);recover(store,identifier);process=start(lab)
            result=lab.wait(identifier,statuses=('success','failed','cancelled'),timeout=30)
            if stage=='uncertain':
                assert result['status']=='failed' and result.get('write_nodes'),result
                assert lab.client.post('/api/runs/'+identifier+'/resume').status_code==409
            else:
                assert result['status']=='success',result.get('error')
                receipt=result['checkpoints']['verify']['_verification']
                assert receipt['run_id']==identifier and receipt['review_id']==review['id']
                assert result['checkpoints']['verify']['_provenance']['ports']['text']=='guessed'
                assert result['accounting']['action_budget']['counter']==1,result['accounting']
                assert result['accounting']['tokens_spent']==20
            assert lab.provider.count(path)==1 and len(model.calls)==2
            assert all(json.loads(call['body'])=={'amount':0} for call in lab.provider.calls if call['path']==path)
            assert lab.decide(identifier,review).status_code==200
            return {'database':kind,'window':stage,'worker_sigkill':True,'status':result['status'],
                'deliveries':1,'model_calls':2,'review_kind':review['kind'],'lease_released':True,
                'digest_mismatch_refused':True,'duplicate_confirmation_idempotent':True}
    finally:model.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True)
    args=parser.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    paths=[*sorted((ROOT/'backend/app').rglob('*.py')),*sorted((ROOT/'backend/migrations').rglob('*.py')),
        Path(__file__),ROOT/'scripts/scenarios/request_review_worker.py']
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    rows=[];started=time.time()
    def record(name,operation):
        try:details=operation();row={'name':name,'status':'passed','details':details}
        except Exception as exc:row={'name':name,'status':'failed','error':f'{type(exc).__name__}: {exc}'}
        rows.append(row);print(name,row['status'],flush=True)
    record('PostgreSQL migrations round trip and model parity',migrations)
    for kind in ('sqlite','postgres'):
        for stage in ('pause','confirmation','settlement','uncertain'):
            record(kind+' '+stage,lambda k=kind,s=stage:probe(k,s,out/(k+'-'+s)))
    changed=[name for name,value in hashes.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=value]
    report={'started_at':started,'completed_at':time.time(),'source_sha256':hashes,'source_files_changed':changed,
        'rows':rows,'passed':all(row['status']=='passed' for row in rows) and not changed,
        'scope':'Separate API and worker processes, local HTTP/model fixtures, SQLite and disposable PostgreSQL schemas; no live semantic accuracy claim'}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
