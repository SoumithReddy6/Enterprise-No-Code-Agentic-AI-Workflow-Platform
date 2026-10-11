import asyncio,hashlib,json,sys,time
from pathlib import Path
from datetime import datetime,timezone
import httpx
root=Path(sys.argv[1]); out=Path(sys.argv[2]); sys.path.insert(0,str(root))
from backend.app.models import Workflow
from backend.app.compiler import compile_workflow
suite=json.loads(Path(sys.argv[3]).read_text()); wf=Workflow.model_validate(json.loads((root/'examples/purchase-routing.json').read_text()))
async def main():
 started=datetime.now(timezone.utc).isoformat()
 async with httpx.AsyncClient() as client:
  models=(await client.get('http://localhost:11434/api/tags')).json()['models']
 model=next(m for m in models if m['name']=='llama3.1:latest')
 source_before={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'backend/app').rglob('*.py')}
 rows=[]
 for c in suite['cases']:
  if c['domain']!='purchase': continue
  events=[]
  async def emit(e): events.append(e)
  t=time.monotonic()
  try:
   result=(await compile_workflow(wf,lambda _: '',emit,c['message']).graph.ainvoke({'values':{}}))['values']
   final=next((n for n in ('auto','review','none') if n in result),None)
   error=None
  except Exception as e:
   result={}; final=None; error=str(e)
  rows.append({'id':c['id'],'expected':c['expected'],'message_sha256':hashlib.sha256(c['message'].encode()).hexdigest(),'answer':result.get('extract',{}).get('text'),'final':final,'decisions':{e['node_id']:e['decision'] for e in events if 'decision' in e},'seconds':round(time.monotonic()-t,3),'error':error})
  print(c['id'], final, error,flush=True)
 source={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'backend/app').rglob('*.py')}
 out.write_text(json.dumps({'started_at':started,'completed_at':datetime.now(timezone.utc).isoformat(),'suite_sha256':hashlib.sha256(Path(sys.argv[3]).read_bytes()).hexdigest(),'workflow_sha256':hashlib.sha256((root/'examples/purchase-routing.json').read_bytes()).hexdigest(),'model':{'name':model['name'],'digest':model['digest']},'source_hashes':source,'source_files_changed_during_run':[name for name in sorted(source.keys() | source_before.keys()) if source.get(name)!=source_before.get(name)],'notes':['Same existing purchase example, 30 applicable cases; other domains have no current configured workflow.','Shadow labels only; no admission gate is implemented.','This run uses real local Ollama through the product compiler, without external writes.'],'rows':rows},indent=2)+'\n')
asyncio.run(main())
