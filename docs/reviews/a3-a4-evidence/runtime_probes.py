import asyncio,json,os
from backend.app.compiler import compile_workflow,validate_workflow
from backend.app.models import Node,Edge
from backend.app.tool_service import TransientToolError,UncertainWriteError
from backend.tests.test_node_error_policy import flow,platform
from backend.tests.test_iteration import loop_flow,platform as loop_platform,issues
from backend.app.approvals import ApprovalPause
from backend.app import registry
results={}
async def execute(w,resolver,**kwargs):
 events=[]
 async def emit(e):events.append(e)
 try:
  r=await compile_workflow(w,platform_resolver=resolver,emit=emit,message='go',**kwargs).graph.ainvoke({'values':{}})
  return {'values':r['values'],'events':events}
 except BaseException as e:return {'error':type(e).__name__+': '+str(e),'events':events}
async def main():
 w=flow(on_error='continue');w.nodes[-1].inputs={'text':'input.message'}
 w.nodes[1].type='tool_http';w.nodes[1].config={'connection_id':'c','method':'POST','enable_writes':True}
 p,c=platform(1,UncertainWriteError('remote outcome unknown'))
 r=await execute(w,p);results['uncertain_write_continue']={'validation':validate_workflow(w),'out':r.get('values',{}).get('out'), 'error':r.get('error')}
 w=flow(on_error='route');w.edges[1].sourceHandle='output'
 w.nodes.append(Node(id='fallback',type='response',inputs={'text':'work.text'}));w.edges.append(Edge(id='err',source='work',target='fallback',sourceHandle='error'))
 p,c=platform(1);r=await execute(w,p);results['error_branch_reads_failed_output']={'validation':validate_workflow(w),'error':r.get('error')}
 w=loop_flow();w.nodes[3].retry.attempts=3;w.nodes[3].retry.base_delay=0
 def flaky(i,t):raise TransientToolError('temporary')
 p,c=loop_platform(issues(1),flaky);r=await execute(w,p);results['body_retry_ignored']={'validation':validate_workflow(w),'calls':len(c),'results':r.get('values',{}).get('each')}
 w=loop_flow();w.nodes[3].type='retrieve';w.nodes[3].config={'knowledge_base_id':'kb'}
 async def retrieval(action,*args):
  if action=='tool':return {'text':'raw','items':issues(1),'truncated':False,'warnings':[]}
  if action=='kb_retrieve':return {'query':'q','context':'passage','sources':[]}
  if action=='record_vector_sources':return []
  if action=='verify_vector_sources':return []
  raise ValueError(action)
 # use actual handler-shaped fixture to isolate collection, not remote retrieval contracts
 original=registry.REGISTRY['retrieve'].handler
 async def retrieve(*a):return {'query':'q','context':'actual evidence passage','sources':'[]'}
 registry.REGISTRY['retrieve'].handler=retrieve
 r=await execute(w,retrieval);results['retrieve_body_data_lost']=r.get('values',{}).get('each');registry.REGISTRY['retrieve'].handler=original
 w=loop_flow();p,c=loop_platform(issues(2),lambda i,t:'x'*600000)
 saved={'each':{str(i):{'index':i,'status':'success','value':'x'*600000,'error':''} for i in range(2)}}
 r=await execute(w,p,loop_progress=saved);results['resume_bypasses_size_cap']={'bytes':len(json.dumps(r['values']['each']['results'])),'calls':len(c),'summary':next(e for e in r['events'] if e.get('kind')=='loop_summary')}
 w=loop_flow(on_item_error='stop');p,c=loop_platform(issues(3));saved={'each':{'0':{'index':0,'status':'failed','value':'','error':'failed before crash'}}}
 r=await execute(w,p,loop_progress=saved);results['resume_ignores_stop']={'calls_after_failed_index':len(c),'results':r['values']['each']['results']}
 def pause(i,t):raise ApprovalPause('work','each','each:0:work',{})
 p,c=loop_platform(issues(2),pause);r=await execute(loop_flow(),p);results['approval_swallowed']=r['values']['each']
 w=loop_flow();w.nodes[3].type='agent';w.nodes[3].config={'provider':'demo'}
 w.nodes.append(Node(id='calc',type='tool_python',config={'code':'print(input_text)'}));w.edges.append(Edge(id='tool',source='calc',target='work',kind='tool',targetHandle='tools'))
 calls=[];sequence=iter(['{"action":"call","target":"calc","input":"x"}','{"action":"final","text":"done"}']*3)
 originalmodel=registry.llm_node
 async def model(*a):return {'text':next(sequence),'provider':'demo'}
 registry.llm_node=model;os.environ['AGENT_RUN_BUDGET']='1'
 p,c=loop_platform(issues(3));ids=[]
 async def traced(action,*args):
  if action=='tool' and args[0]=='tool_python':ids.append(args[-1])
  return await p(action,*args)
 r=await execute(w,traced);results['action_budget_resets_per_item']={'budget':1,'tool_calls':len(c),'invocation_ids':ids,'error':r.get('error')};registry.llm_node=originalmodel;os.environ.pop('AGENT_RUN_BUDGET')
 print(json.dumps(results,indent=2))
asyncio.run(main())
