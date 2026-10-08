"""Live local-model check through the same compiler; does not create workspace runs."""
import asyncio,json,sys,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from backend.app.compiler import compile_workflow
from backend.app.models import Workflow

async def main():
    workflow=Workflow.model_validate(json.loads((ROOT/'examples/purchase-routing.json').read_text()))
    rows=[]
    for message,expected in [('How do AI workflows work?','none'),('Hello!','none'),('Please order two USB cables at $12 each. The order total is $24.','auto')]:
        try:
            result=await asyncio.wait_for(compile_workflow(workflow,lambda _: '',message=message).graph.ainvoke({'values':{}},{'recursion_limit':150}),90)
            values=result['values'];reached=[n.id for n in workflow.nodes if n.type=='response' and n.id in values]
            rows.append({'input':message,'expected_response':expected,'actual_response':reached,'extraction':values.get('extract'),'passed':reached==[expected],'error':None})
        except Exception as exc:
            rows.append({'input':message,'passed':False,'error':{'type':type(exc).__name__,'message':str(exc)}})
        print(json.dumps(rows[-1]),flush=True)
    result={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'scope':'Live local llama3.1:latest, current example, actual compiler; no tools, writes, workspace runs or configuration edits.','rows':rows,'passed':all(r['passed'] for r in rows)}
    (Path(__file__).resolve().parent/'live-results.json').write_text(json.dumps(result,indent=2)+'\n')
    return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(asyncio.run(main()))
