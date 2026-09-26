import asyncio,json,tempfile,os
from pathlib import Path
from cryptography.fernet import Fernet
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.app.tool_service import ToolService,UncertainWriteError
from backend.tests.test_iteration import loop_flow
from backend.tests.test_node_error_policy import flow
from backend.app.compiler import compile_workflow
from backend.tests.test_iteration import platform,issues
from backend.app import providers
results={}
async def main():
 with tempfile.TemporaryDirectory() as tmp:
  store=Store('sqlite:///'+tmp+'/db',Fernet.generate_key());tools=ToolService(store)
  jira=tools.save_connection({'name':'jira','provider':'jira','endpoint':'https://example.com'})['id']
  http=tools.save_connection({'name':'http','provider':'http','endpoint':'https://example.com'})['id']
  worker=Worker(store)
  async def prepared(p,tenant_id='local'):return json.dumps({'issues':[{'key':'T-1'},{'key':'T-2'}]})
  worker.tools.execute_prepared=prepared
  w=loop_flow(connection_id=jira);w.nodes[3].type='tool_http';w.nodes[3].config={'connection_id':http,'method':'POST','enable_writes':True,'approval':True}
  r=store.create_run(w.model_dump(mode='json'),'go');await worker.execute(store.claim_next(worker.owner));saved=store.run(r['id'])
  results['worker_approval']={'status':saved['status'],'approvals':len(saved.get('approvals',[])),'results':saved.get('checkpoints',{}).get('each')}
  w=flow(on_error='continue');w.nodes[-1].inputs={'text':'input.message'};w.nodes[1].type='tool_http';w.nodes[1].config={'connection_id':http,'method':'POST','enable_writes':True,'approval':False}
  async def failed(*a):raise ValueError('transport failed after send')
  worker.tools.execute=failed
  r=store.create_run(w.model_dump(mode='json'),'go');await worker.execute(store.claim_next(worker.owner));saved=store.run(r['id']);results['worker_uncertain_write']={'status':saved['status'],'write_nodes':saved.get('write_nodes'),'output':saved['output']}
  w=loop_flow(connection_id=jira,max_items=1)
  async def ok(*a):return 'done'
  worker.tools.execute=ok
  r=store.create_run(w.model_dump(mode='json'),'go');await worker.execute(store.claim_next(worker.owner));saved=store.run(r['id']);results['worker_truncation']={'status':saved['status'],'run_truncated':saved.get('truncated'),'summary':[e for e in saved['events'] if e.get('kind')=='loop_summary'][0]}
  w=loop_flow(connection_id=jira);w.nodes[3].type='tool_http';w.nodes[3].config={'connection_id':http,'method':'POST','enable_writes':True,'approval':False}
  count=0
  async def write(*a):
   nonlocal count
   count+=1
   return 'sent'
  worker.tools.execute=write
  original_event=store.worker_event
  def cancel_after_saved_item(*a):
   persisted=original_event(*a)
   return False if a[-1].get('kind')=='loop_item' else persisted
  store.worker_event=cancel_after_saved_item
  r=store.create_run(w.model_dump(mode='json'),'go');await worker.execute(store.claim_next(worker.owner));saved=store.run(r['id'])
  store.worker_event=original_event
  try:store.resume_run(r['id']);resume='allowed'
  except ValueError as e:resume=str(e)
  results['write_loop_resume']={'completed_network_calls':count,'progress':len(saved.get('loop_progress',{}).get('each',{})),'resume':resume}
 # Token usage goes through the real provider accounting path; only network is mocked.
 os.environ['RELAY_RUN_TOKEN_LIMIT']='8';calls=[]
 original=providers.ollama_chat
 async def chat(model,system,prompt,usage=None,**kw):
  calls.append(1);usage.update(prompt_tokens=4,completion_tokens=4);return 'answer'
 providers.ollama_chat=chat
 w=loop_flow();w.nodes[3].type='agent';w.nodes[3].config={'provider':'ollama','model':'fake'}
 resolver,_=platform(issues(2));progress={}
 async def stop(e):
  if e.get('kind')=='loop_item':progress[str(e['item_index'])]=e['item_result'];raise asyncio.CancelledError()
 try:await compile_workflow(w,platform_resolver=resolver,emit=stop).graph.ainvoke({'values':{}})
 except BaseException:pass
 await compile_workflow(w,platform_resolver=resolver,loop_progress={'each':progress}).graph.ainvoke({'values':{}})
 results['token_budget_resume']={'limit':8,'actual_provider_tokens':len(calls)*8,'model_calls':len(calls)}
 providers.ollama_chat=original;os.environ.pop('RELAY_RUN_TOKEN_LIMIT')
 print(json.dumps(results,indent=2))
asyncio.run(main())
