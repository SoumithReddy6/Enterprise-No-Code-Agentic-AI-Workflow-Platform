"""Repeat Step 7 clean-export and focused mutation checks without editing the checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
NEW = [
    'frontend/lib/request-routing.ts', 'frontend/components/workflow-setup.tsx',
    'frontend/components/request-checks.tsx', 'frontend/tests/request-routing.test.ts',
    'frontend/tests/browser/request-routing.spec.ts', 'frontend/tests/browser/request-nodes.json',
    'backend/tests/test_request_examples.py', 'docs/request-workflow-setup.md',
    *['examples/request-admission/' + name for name in
      ['qa.json','purchase.json','support.json','refund.json','yesno.json','reference-fixtures.json','README.md']],
]


def export():
    directory = Path(tempfile.mkdtemp(prefix='relay-step7-export-'))
    archive = directory / 'head.tar'
    with archive.open('wb') as f:
        subprocess.run(['git','archive','HEAD'],cwd=ROOT,stdout=f,check=True)
    with tarfile.open(archive) as tar: tar.extractall(directory, filter='data')
    changed = subprocess.check_output(['git','diff','--name-only','HEAD'],cwd=ROOT,text=True).splitlines()
    paths = sorted(set(changed + NEW))
    for name in paths:
        target = directory / name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT / name, target)
    (directory/'frontend/node_modules').symlink_to(ROOT/'frontend/node_modules',target_is_directory=True)
    (directory/'.venv').symlink_to(ROOT/'.venv',target_is_directory=True)
    manifest = {'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'export':str(directory), 'overlay':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in paths}}
    return directory,manifest


def run(directory, args, name, frontend=False):
    cwd=directory/'frontend' if frontend else directory
    log=OUT/(name+'.log')
    with log.open('w') as f:
        result=subprocess.run(args,cwd=cwd,env={**os.environ,'PYTHONPATH':str(directory),'PYTHONHASHSEED':'1'},stdout=f,stderr=subprocess.STDOUT)
    return {'exit_code':result.returncode,'log':log.name,'tail':log.read_text().splitlines()[-8:]}


def mutations(directory):
    cases=[
        ('contract_disabled','frontend/lib/request-routing.ts','contract: 2,','contract: 1,',['node','--experimental-strip-types','--test','tests/request-routing.test.ts']),
        ('nonfinite_json_allowed','frontend/lib/workflow.ts',"typeof value === 'number' && !Number.isFinite(value)",'false',['node','--experimental-strip-types','--test','tests/request-routing.test.ts']),
        ('expiry_ignored','frontend/lib/request-routing.ts','approval.expires_at <= now','false',['node','--experimental-strip-types','--test','tests/request-routing.test.ts']),
        ('missing_choice_allowed','frontend/lib/approvals.ts',"kind === 'decision' &&\n    typeof choice !== 'boolean'", "kind === 'decision' && false",['node','--experimental-strip-types','--test','tests/request-routing.test.ts']),
        ('payload_reserialized','frontend/lib/approvals.ts','encode(approval.payload_json)','encode(JSON.stringify(JSON.parse(approval.payload_json)))',['node','--experimental-strip-types','--test','tests/approvals.test.ts']),
        ('manual_undisclosed','frontend/lib/request-routing.ts',"['condition', 'calculate'].includes(type)","[].includes(type)",['node','--experimental-strip-types','--test','tests/request-routing.test.ts']),
        ('template_defaults_shared','frontend/lib/request-routing.ts','return structuredClone(workflow);','return workflow;',['node','--experimental-strip-types','--test','tests/request-routing.test.ts']),
        ('false_branch_sent_true','frontend/components/approval-review.tsx',"onDecision(approval, 'approve', false)","onDecision(approval, 'approve', true)",['npx','playwright','test','request-routing.spec.ts','--grep','human decision sends explicit false']),
        ('stale_json_retained','frontend/components/platform-settings.tsx','draft && Object.is(draft.value, value) ? draft.text : null','draft?.text ?? null',['npx','playwright','test','request-routing.spec.ts','--grep','second workflow']),
        ('comparison_missing','frontend/components/approval-review.tsx','typeof payload?.value === \'string\'','false',['npx','playwright','test','request-routing.spec.ts','--grep','interpretation review confirm']),
        ('stale_run_success','frontend/components/workflow-editor.tsx',"await api(`/runs/${runId}/${action}`, 'POST', decision);\n      if (runViewVersion.current !== viewVersion) return;", "await api(`/runs/${runId}/${action}`, 'POST', decision);",['npx','playwright','test','request-routing.spec.ts','--grep','delayed review success']),
        ('stale_run_failure','frontend/components/workflow-editor.tsx',"} catch (e) {\n      if (runViewVersion.current !== viewVersion) return;\n      setNotice", "} catch (e) {\n      setNotice",['npx','playwright','test','request-routing.spec.ts','--grep','delayed review failure']),
    ]
    rows=[]
    for name,path,old,new,command in cases:
        file=directory/path;original=file.read_text()
        if original.count(old)!=1:raise RuntimeError(f'{name}: expected one mutation anchor, found {original.count(old)}')
        try:
            file.write_text(original.replace(old,new,1))
            result=run(directory,command,'mutation-'+name,frontend=True)
            if result['exit_code']==0:raise RuntimeError(f'{name}: mutation survived')
            content=(OUT/result['log']).read_text()
            if command[0]=='npx' and ('Error: expect' not in content or '1 failed' not in content):
                raise RuntimeError(f'{name}: browser failed without its assertion; not a detected mutation')
            rows.append({'name':name,'caught':True,**result})
            print(name,'caught',flush=True)
        finally:file.write_text(original)
    (OUT/'mutations.json').write_text(json.dumps(rows,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mutations',action='store_true');args=parser.parse_args()
    directory,manifest=export()
    (OUT/'isolation-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if args.mutations:mutations(directory);return
    commands=[('backend',[str(ROOT/'.venv/bin/python'),'-m','pytest','backend/tests','-q'],False),
        ('frontend',['npm','test'],True),('typecheck',['npm','run','typecheck'],True),('lint',['npm','run','lint'],True),('build',['npm','run','build'],True)]
    rows=[]
    for name,command,frontend in commands:
        result=run(directory,command,'isolation-'+name,frontend)
        rows.append({'name':name,**result});print(name,result['exit_code'],result['tail'][-1],flush=True)
        if result['exit_code']:raise SystemExit(result['exit_code'])
    (OUT/'isolation.json').write_text(json.dumps(rows,indent=2)+'\n')


if __name__=='__main__':main()
