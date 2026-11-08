"""Review P3 proof (docs: the A5.3 Part 3 implementation review): S24 must check that the replacement
worker waited for the killed worker's lease. M34 lets a worker claim a running job 25 s before its
lease ends. Three runs of rows.py, each on a fresh copy of the committed tree (945cae5):
  old S24 + M34  -> expected PASS (the gap)
  new S24 + M34  -> expected FAIL (the new assertion)
  new S24        -> expected PASS
Usage: python m34.py <scratch dir> <rows.py> <new parallel_branches.py> <output file>
"""
import json, os, pathlib, shutil, subprocess, sys, tempfile
SP, ROWS, NEW, OUT = map(pathlib.Path, sys.argv[1:5]); ROWS = ROWS.resolve(); NEW = NEW.resolve()
ROOT = pathlib.Path.cwd()
OLD = "        eligible=or_(JobRecord.status=='queued',and_(JobRecord.status=='running',JobRecord.lease_until<timestamp))"
MUT = "        eligible=or_(JobRecord.status=='queued',and_(JobRecord.status=='running',JobRecord.lease_until<timestamp+25))"
lines = []
for label, spec, mutant, expect in [('old S24 + M34', None, True, 'passed'), ('new S24 + M34', NEW, True, 'failed'), ('new S24', NEW, False, 'passed')]:
    work = pathlib.Path(tempfile.mkdtemp(prefix='a53-p3-m34-', dir=SP)); repo = work / 'repo'
    subprocess.run(f'git archive 945cae5 | tar -x -C "{repo}"' if repo.mkdir() is None else '', shell=True, check=True, cwd=ROOT)
    if spec: shutil.copy2(spec, repo / 'scripts/scenarios/parallel_branches.py')
    if mutant:
        p = repo / 'backend/app/storage.py'; s = p.read_text(); assert s.count(OLD) == 1; p.write_text(s.replace(OLD, MUT))
    subprocess.run([sys.executable, '-u', str(ROWS), str(work / 'rows')], cwd=repo, env={**os.environ, 'PYTHONPATH': str(repo)},
                   capture_output=True, text=True, timeout=400)
    rows = {r['id']: r for r in json.loads((work / 'rows' / 'rows.json').read_text())}
    s24 = rows['S24']; ok = s24['status'] == expect and len(rows) == 4
    lines.append(f"{label}: S24 {s24['status']} (expected {expect}) {'OK' if ok else 'UNEXPECTED'} | "
                 f"{s24.get('error') or json.dumps({k: s24['details'][k] for k in ('resumed_after_seconds', 'claimed_after_lease_end_seconds') if k in s24['details']})}"
                 f" | other rows: {', '.join(f'{k} {v['status']}' for k, v in rows.items() if k != 'S24')}")
    print(lines[-1], flush=True)
OUT.write_text('\n'.join(lines) + '\n')
