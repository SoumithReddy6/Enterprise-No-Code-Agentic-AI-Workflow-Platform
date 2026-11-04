"""Reproducible clean export and targeted mutations; never edits the real checkout."""
import argparse,hashlib,json,os,shutil,subprocess,tarfile,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).resolve().parent
PYTHON=ROOT/'.venv/bin/python'

def export(directory):
 archive=directory/'head.tar'
 with archive.open('wb') as file:subprocess.run(['git','archive','HEAD'],cwd=ROOT,stdout=file,check=True)
 tree=directory/'tree';tree.mkdir()
 with tarfile.open(archive) as file:file.extractall(tree,filter='data')
 for name in json.loads((OUT/'verification-files.json').read_text()):
  destination=tree/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,destination)
 (tree/'.git').symlink_to(ROOT/'.git',target_is_directory=True) # read-only git identity/provenance commands only
 (tree/'frontend/node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
 return tree

def main():
 args=argparse.ArgumentParser();args.add_argument('--mutations',action='store_true');args=args.parse_args();rows=[]
 with tempfile.TemporaryDirectory(prefix='relay-step8-export-') as temp:
  tree=export(Path(temp))
  if args.mutations:
   mutations=[('duplicate-send','scripts/eval_request_admission.py','if len(row[\'deliveries\'])>1:return False','if False:return False'),
    ('wrong-destination','scripts/eval_request_admission.py'," or delivery.get('path')!=destination",''),
    ('wrong-value','scripts/eval_request_admission.py',"if value != exact_loads(json.dumps(expected['expected_value'])) or row['automatic_branch'] != expected['safe_branch']:return False","if row['automatic_branch'] != expected['safe_branch']:return False"),
    ('missing-confirmation','scripts/check_request_admission_eval.py',"if row['manual'] and not matching_confirmation(row,next(c for c in suite['cases'] if c['id']==row['id'])):","if False:"),
    ('missing-manifest','scripts/check_request_admission_eval.py',"if not isinstance(manifest,dict) or set(manifest)!=set(source_hashes()) or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in manifest.values()):","if False:"),
    ('fault-not-exercised','scripts/check_request_admission_eval.py',"'faults_reached_verification':report['profile']!='fault' or all(","'faults_reached_verification':True or all("),
    ('missing-controls','scripts/check_request_admission_eval.py',"'fault_positive_controls':report['profile']!='fault' or controls_pass(report.get('controls'))","'fault_positive_controls':True"),
    ('missing-worker-tags','backend/app/compiler.py',"await emit({**event,'request_stage':node.type} if node.type in ('greeting_cache','request_router','verify_extraction') else event)","await emit(event)")]
   for name,path,before,after in mutations:
    target=tree/path;original=target.read_text();assert original.count(before)==1,(name,path)
    target.write_text(original.replace(before,after))
    log=OUT/(name+'-mutant.log')
    with log.open('w') as file:p=subprocess.run([str(PYTHON),'-m','pytest','backend/tests/test_request_admission_eval.py','backend/tests/test_request_admission_metrics.py','-q'],cwd=tree,stdout=file,stderr=subprocess.STDOUT)
    text=log.read_text();caught=p.returncode==1 and 'FAILED backend/tests/' in text
    rows.append({'name':name,'caught':caught,'exit_code':p.returncode});print(rows[-1],flush=True);target.write_text(original)
   (OUT/'mutations.json').write_text(json.dumps(rows,indent=2)+'\n')
   return 0 if all(r['caught'] for r in rows) else 1
  code={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in json.loads((OUT/'verification-files.json').read_text())}
  assert all(hashlib.sha256((tree/n).read_bytes()).hexdigest()==h for n,h in code.items())
  commands=[('backend',[str(PYTHON),'-m','pytest','backend/tests','-q']),('frontend-units',['npm','--prefix','frontend','test']),('typecheck',['npm','--prefix','frontend','run','typecheck']),('lint',['npm','--prefix','frontend','run','lint']),('build',['npm','--prefix','frontend','run','build'])]
  for name,cmd in commands:
   with (OUT/('isolation-'+name+'.log')).open('w') as file:
    start=time.monotonic();p=subprocess.run(cmd,cwd=tree,stdout=file,stderr=subprocess.STDOUT)
   rows.append({'name':name,'exit_code':p.returncode,'seconds':time.monotonic()-start});print(rows[-1],flush=True)
  (OUT/'isolation.json').write_text(json.dumps({'files_sha256':code,'commands':rows,'passed':all(r['exit_code']==0 for r in rows)},indent=2)+'\n')
  return 0 if all(r['exit_code']==0 for r in rows) else 1

if __name__=='__main__':raise SystemExit(main())
