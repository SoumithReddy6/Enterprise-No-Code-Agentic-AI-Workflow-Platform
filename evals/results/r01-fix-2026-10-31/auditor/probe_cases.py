"""Run each case of an auditor probe module separately on an implementation tree.
argv: probe_plan.py path, implementation root, case names..."""
import asyncio,importlib.util,json,sys,traceback
probe,root,cases=sys.argv[1],sys.argv[2],sys.argv[3:]
sys.argv=[probe,root]
spec=importlib.util.spec_from_file_location('probe_'+str(abs(hash(probe))),probe);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
async def one(kind):
    try:
        r=await (m.shape_cases() if kind=='shapes' else m.real_case(kind))
        return {'case':kind,'probe_asserts':'all hold','status':r.get('status') if isinstance(r,dict) else None,'error':r.get('error') if isinstance(r,dict) else None}
    except AssertionError:
        tb=sys.exc_info()[2];frames=[f for f in traceback.walk_tb(tb) if f[0].f_code.co_filename==probe]
        frame,line=frames[-1];final=frame.f_locals.get('final') or {}
        return {'case':kind,'probe_asserts':f'failed at line {line}','source':open(probe).read().splitlines()[line-1].strip(),
                'status':final.get('status'),'error':final.get('error'),'provider_calls':len(frame.f_locals.get('calls',[]))}
    except Exception as e:
        return {'case':kind,'raised':type(e).__name__,'message':str(e)}
async def main():print(json.dumps({'application_root':root,'cases':[await one(k) for k in cases]},indent=2))
asyncio.run(main())
