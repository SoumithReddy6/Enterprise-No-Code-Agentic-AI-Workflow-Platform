"""Reproduce Step 6 mutation checks and an exact-file clean checkout proof.

Run from the repository root with its Python environment. Never mutates the checkout,
index or HEAD; all changed implementations live in disposable /tmp exports.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT=Path.cwd()
OUT=Path(__file__).resolve().parent
PYTHON=ROOT/'.venv/bin/python'
NEW=[
    'backend/app/request_safety.py','backend/migrations/versions/0004_approval_kind.py',
    'backend/tests/test_request_review.py','backend/tests/test_request_review_migration.py',
    'backend/tests/test_request_safety_boundary.py','scripts/scenarios/request_enforcement.py',
    'scripts/scenarios/request_review_worker.py']


def files():
    changed=subprocess.check_output(['git','diff','--name-only','HEAD'],cwd=ROOT,text=True).splitlines()
    return sorted(set(changed+NEW))


def export(target):
    archive=subprocess.check_output(['git','archive','HEAD'],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:tar.extractall(target,filter='data')
    for name in files():
        path=target/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,path)


def pytest(target,args,log):
    env={**os.environ,'PYTHONPATH':str(target),'PYTHONHASHSEED':'0'}
    result=subprocess.run([str(PYTHON),'-m','pytest',*args,'-q'],cwd=target,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    log.write_text(result.stdout)
    return result


def mutations():
    policy='backend/app/execution_policy.py';safety='backend/app/request_safety.py';approvals='backend/app/approvals.py'
    cases=[
        ('no pre-attempt eligibility',policy,'if eligibility is not None:','if False and eligibility is not None:',
         ['backend/tests/test_request_safety_boundary.py::test_shared_seam_refuses_before_action_debit_operation_or_retry']),
        ('attached dispatcher bypass',policy,"confirmed=await guard(child,node,{'input':input_text},identity)",'confirmed=None',
         ['backend/tests/test_request_safety_boundary.py::test_direct_attached_dispatch_cannot_bypass_the_shared_guard']),
        ('denial becomes agent observation',policy,'ApprovalPause,ReviewPause,UnsafeExecutionError,UncertainWriteError','ApprovalPause,ReviewPause,UncertainWriteError',
         ['backend/tests/test_request_review.py::test_attached_agent_write_cannot_change_the_verified_value']),
        ('contradictory Condition branch',safety,"if outputs.get('branch')!=expected:raise UnsafeExecutionError('Condition result", "if False:raise UnsafeExecutionError('Condition result",
         ['backend/tests/test_request_review.py::test_cached_automatic_condition_cannot_change_the_verified_branch']),
        ('expired control path bypass',safety,'if write or decision:await checked_control_path(ctx)','if False:await checked_control_path(ctx)',
         ['backend/tests/test_request_review.py::test_expired_manual_control_cannot_authorize_an_independently_bound_write']),
        ('confirmation validity skipped',approvals,"if type(choice) is not bool or kind=='verification' and choice is not True:", 'if False:',
         ['backend/tests/test_request_review.py::test_cached_verification_never_restores_damaged_authority[review]']),
        ('review consumption loses fence',approvals,"if not store._fence(s,run_id,owner):raise LeaseLost('Execution lease is no longer owned.')", "if False:raise LeaseLost('Execution lease is no longer owned.')",
         ['backend/tests/test_request_review.py::test_review_consumption_and_restore_require_the_current_owner']),
        ('derived value not recomputed',safety,"if outputs.get('text')!=expected:raise UnsafeExecutionError('Derived output changed after its checked inputs.')", 'if False:raise UnsafeExecutionError()',
         ['backend/tests/test_request_review.py::test_cached_derivation_cannot_change_checked_values_and_coalesce_reads_only_its_branch']),
        ('approval kind unconstrained','backend/migrations/versions/0004_approval_kind.py',
         'batch.create_check_constraint(\'ck_run_approval_kind\',"kind IN (\'write\',\'decision\',\'verification\')")','pass',
         ['backend/tests/test_request_review_migration.py::test_database_itself_rejects_unknown_approval_kinds']),
    ]
    rows=[]
    with tempfile.TemporaryDirectory(prefix='relay-step6-mutations-',dir='/private/tmp') as temp:
        tree=Path(temp);export(tree)
        for index,(name,path,before,after,args) in enumerate(cases):
            file=tree/path;original=file.read_text()
            if before not in original:raise RuntimeError('Mutation target absent: '+name)
            file.write_text(original.replace(before,after,1))
            try:result=pytest(tree,args,OUT/f'mutant-{index}.log')
            finally:file.write_text(original)
            killed=result.returncode==1 and ' failed' in result.stdout and 'ERROR collecting' not in result.stdout
            rows.append({'name':name,'caught':killed,'exit_code':result.returncode,'tests':args})
            print(name,'caught' if killed else 'NOT CAUGHT',flush=True)
    report={'rows':rows,'passed':all(r['caught'] for r in rows)}
    (OUT/'mutations.json').write_text(json.dumps(report,indent=2)+'\n')
    return report['passed']


def isolation():
    with tempfile.TemporaryDirectory(prefix='relay-step6-isolation-',dir='/private/tmp') as temp:
        tree=Path(temp);export(tree)
        env={**os.environ,'PYTHONPATH':str(tree)}
        imported=subprocess.check_output([str(PYTHON),'-c','import backend.app;print(backend.app.__file__)'],cwd=tree,env=env,text=True).strip()
        assert Path(imported).is_relative_to(tree),imported
        result=pytest(tree,['backend/tests'],OUT/'isolation.log')
        report={'base':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'files':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files()},
            'import_from_export':True,'exit_code':result.returncode,'summary':result.stdout.splitlines()[-1]}
        (OUT/'isolation-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
        print(report['summary'],flush=True)
        return result.returncode==0


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['mutations','isolation'])
    args=parser.parse_args()
    raise SystemExit(0 if (mutations() if args.mode=='mutations' else isolation()) else 1)
