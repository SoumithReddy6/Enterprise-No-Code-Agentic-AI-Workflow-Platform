"""Reproduce the screenshot and audit the newer null guard without changing real history."""
import asyncio,json,subprocess,sys
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from backend.app.compiler import compile_workflow,validate_workflow
from backend.app.models import Workflow

async def evaluate(raw,message,model_text,expected):
    events=[];calls=0
    async def emit(event):events.append(event)
    async def model(*args,**kwargs):
        nonlocal calls
        calls+=1
        kwargs['usage'].update(prompt_tokens=1,completion_tokens=1)
        return model_text
    workflow=Workflow.model_validate(raw)
    errors=validate_workflow(workflow)
    assert not errors,errors
    with patch('backend.app.providers.ollama_chat',model):
        try:
            result=await compile_workflow(workflow,lambda _: '',emit=emit,message=message).graph.ainvoke({'values':{}},{'recursion_limit':150})
            values=result['values'];error=None
        except Exception as exc:
            values={};error={'type':type(exc).__name__,'message':str(exc)}
    reached=[n.id for n in workflow.nodes if n.type=='response' and n.id in values]
    return {'message':message,'model_output':model_text,'node_count':len(workflow.nodes),'expected_response':expected,'actual_response':reached,'passed':reached==[expected] if expected else not reached,'model_calls':calls,'error':error,'extraction':values.get('extract'),'gate':values.get('gate'),'amount_condition':values.get('check'),'output':[values[n]['text'] for n in reached]}

async def main():
    old=json.loads(subprocess.check_output(['git','show','HEAD^:examples/purchase-routing.json'],cwd=ROOT,text=True))
    current=json.loads((ROOT/'examples/purchase-routing.json').read_text())
    cases=[
      ('screenshot_old',old,'How do AI workflows work?','{"amount":0}',None),
      ('new_unknown_null',current,'How do AI workflows work?','{"amount":null}','none'),
      ('new_unknown_zero',current,'How do AI workflows work?','{"amount":0}','none'),
      ('new_unknown_other_number',current,'Hello!','{"amount":24}','none'),
      ('missing_total',current,'Please order two USB cables.','{"amount":null}','none'),
      ('explicit_zero',current,'Please order promotional stickers. The order total is $0.','{"amount":0}','auto'),
      ('low',current,'Please order two USB cables. The order total is $24.','{"amount":24}','auto'),
      ('boundary',current,'Please order a desk. The order total is $1,000.','{"amount":1000}','auto'),
      ('high',current,'Please order three laptops. The order total is $1,350.','{"amount":1350}','review'),
      ('negative',current,'The refund is $10.','{"amount":-10}',None),
      ('malformed_type',current,'How do AI workflows work?','{"amount":"0"}',None)]
    rows=[]
    for name,raw,message,text,expected in cases:
        rows.append({'case':name,**await evaluate(raw,message,text,expected)})
    result={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'scope':'Actual compiler, extraction, schema and branch execution; deterministic provider responses; no external writes or real workspace runs.','rows':rows,'unresolved':[r['case'] for r in rows if not r['passed']]}
    (Path(__file__).resolve().parent/'purchase-results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'rows':len(rows),'unresolved':result['unresolved'],'cases':[{k:r[k] for k in ('case','expected_response','actual_response','model_calls','passed')} for r in rows]},indent=2))
    return 1 if result['unresolved'] else 0

if __name__=='__main__':raise SystemExit(asyncio.run(main()))
