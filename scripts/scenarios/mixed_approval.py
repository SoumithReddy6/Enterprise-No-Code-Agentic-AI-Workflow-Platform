"""Deterministic Worker+Store contract probe; model and delivery are fixtures.

Not a deployed-network test. Fails until mixed approval modes resume safely.
"""
import asyncio,json,tempfile,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from cryptography.fernet import Fernet
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.app.models import Workflow
from backend.app import registry
from backend.app.approvals import decide
from backend.tests.test_execution_safety import batch_write_flow,jira_connection,http_connection,jira_payload
async def main():
 with tempfile.TemporaryDirectory() as d:
  store=Store('sqlite:///'+d+'/db',Fernet.generate_key());worker=Worker(store)
  http=http_connection(store);flow=batch_write_flow(jira_connection(store),http).model_dump()
  agent=next(n for n in flow['nodes'] if n['id']=='send');agent.update(type='agent',config={'provider':'demo'})
  for name,approval in [('first',False),('second',True)]:
   flow['nodes'].append({'id':name,'type':'tool_http','config':{'connection_id':http,'method':'POST','enable_writes':True,'approval':approval}})
   flow['edges'].append({'id':name,'source':name,'target':'send','kind':'tool','targetHandle':'tools'})
  responses=iter([json.dumps({'action':'call','target':name,'input':'payload'}) for name in ['first','second']]+[json.dumps({'action':'final','text':'done'})])
  calls=[]
  async def model(*args):return {'text':next(responses),'provider':'demo'}
  async def prepared(p,*args):
   if p['connection']['provider']=='jira':return jira_payload(1)
   calls.append('approved');return 'sent'
  async def execute(*args):calls.append('unapproved');return 'sent'
  registry.llm_node=model;worker.tools.execute_prepared=prepared;worker.tools.execute=execute
  row=store.create_run(Workflow.model_validate(flow).model_dump(),'go',approval_required=True)
  await worker.execute(store.claim_next(worker.owner));before=store.run(row['id'])
  approval=next(a for a in before['approvals'] if a['status']=='pending')
  decide(store,row['id'],'local',approval['id'],approval['digest'],'approve')
  await worker.execute(store.claim_next(worker.owner));after=store.run(row['id'])
  print(json.dumps({'before':{k:before.get(k) for k in ['status','write_nodes','loop_progress']},'after':{k:after.get(k) for k in ['status','error','write_nodes','loop_progress']},'calls':calls,'approval_status':after['approvals'][0]['status']},indent=2))
  assert after['status']=='success', 'Approved continuation was blocked by an earlier completed write: '+after.get('error','')
  assert calls==['unapproved','approved'],calls

if __name__=='__main__':asyncio.run(main())
