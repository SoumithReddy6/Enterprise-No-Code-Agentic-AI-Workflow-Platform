"""Two plan mutants, with exact failure checks and an N15 full-checkpoint control."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

repo=Path(__file__).resolve().parents[3]
folder=Path(__file__).resolve().parent
python=sys.executable  # Preserve the virtualenv entry point rather than resolving its symlink.
results=[]
for mutant in ('N14','N15'):
    root=Path(tempfile.mkdtemp(prefix='relay-r01-plan-r3-mutant-',dir='/private/tmp'))
    archive=subprocess.Popen(['git','archive','9801c46'],cwd=repo,stdout=subprocess.PIPE)
    unpack=subprocess.run(['tar','-x','-C',str(root)],stdin=archive.stdout,check=True)
    archive.stdout.close()
    assert archive.wait()==0
    built=subprocess.run([python,str(folder/'prepare_candidate.py'),str(root)],capture_output=True,text=True)
    assert built.returncode==0,built.stderr
    if mutant=='N14':
        path=root/'backend/app/registry.py'
        before='    await verify_published(ctx,ctx.node_id,sources+reused)'
        after='    await verify_published(ctx,ctx.node_id,sources)'
    else:
        path=root/'backend/app/iteration.py'
        before="            if ctx.run is not None:ctx.run.setdefault('reused_owners',set()).add(item_owner(ctx,index))\n"
        after=''
    text=path.read_text()
    assert text.count(before)==1
    path.write_text(text.replace(before,after,1))
    for kind in ('cached_loop_after_reuse','cached_partial_after_reuse'):
        run=subprocess.run([python,str(folder/'probe_plan.py'),str(root),'--case',kind],
            capture_output=True,text=True,timeout=60,cwd=repo)
        (folder/f'{mutant}-{kind}.log').write_text(run.stdout+run.stderr)
        expected=0 if mutant=='N15' and kind=='cached_loop_after_reuse' else 1
        assert run.returncode==expected,(mutant,kind,run.returncode,run.stderr[-3000:])
        if expected:
            assert 'AssertionError' in run.stderr and "'status': 'success'" in run.stderr,run.stderr[-3000:]
        results.append({'mutant':mutant,'case':kind,'expected_exit':expected,'actual_exit':run.returncode,
            'outcome':'unsafe publication detected' if expected else 'full-checkpoint control still refuses',
            'application_root':str(root)})
(folder/'mutants.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
