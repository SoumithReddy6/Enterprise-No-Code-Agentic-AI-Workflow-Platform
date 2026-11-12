"""Mutants for the populated-downgrade check. Each runs in a fresh scratch copy of the isolation tree."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
iso, scratch, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
MUTANTS = {
    'M1 missing loop rows hydrate as an empty successful loop': ('backend/app/storage.py',
        "if missing:return unavailable(f'{missing} of {count} stored item results are missing')",
        "if missing:return {**outputs,'results':[]}"),
    'M2 a contract run with no accounting is treated as an older run': ('backend/app/accounting_state.py',
        "    checked_snapshot(run.get('accounting'))\n    return True",
        "    if run.get('accounting') is None:return False\n    checked_snapshot(run.get('accounting'))\n    return True"),
    'M3 missing accounting is given an inferred zero baseline': ('backend/app/accounting_state.py',
        "    checked_snapshot(run.get('accounting'))\n    return True",
        "    if run.get('accounting') is None:run['accounting']=zero_snapshot()\n    checked_snapshot(run.get('accounting'))\n    return True"),
    'M3b both guards give missing accounting a zero baseline': [('backend/app/accounting_state.py',
        "    checked_snapshot(run.get('accounting'))\n    return True",
        "    if run.get('accounting') is None:run['accounting']=zero_snapshot()\n    checked_snapshot(run.get('accounting'))\n    return True"),
        ('backend/app/storage.py',
        "        previous=checked_snapshot(stored)['tokens_spent'] if row.data.get('accounting_contract') is not None else restored_tokens(stored)\n        if incoming",
        "        previous=checked_snapshot(stored or zero_snapshot())['tokens_spent'] if row.data.get('accounting_contract') is not None else restored_tokens(stored)\n        if incoming")],
}
rows = []
for name, edits in MUTANTS.items():
    edits = edits if isinstance(edits, list) else [edits]
    copy = Path(tempfile.mkdtemp(prefix='dg-mutant-', dir=scratch)) / 'tree'
    shutil.copytree(iso, copy, symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))
    for path, old, new in edits:
        target = copy / path; text = target.read_text()
        assert text.count(old) == 1, (name, path)
        target.write_text(text.replace(old, new))
    subprocess.run(['git', 'add', '-A'], cwd=copy, check=True); subprocess.run(['git', '-c', 'user.email=x@x', '-c', 'user.name=x', 'commit', '-qm', name], cwd=copy, check=True)
    env = {**os.environ, 'PYTHONPATH': str(copy)}
    result = subprocess.run([str(copy / '.venv/bin/python'), '-u', '-m', 'scripts.check_milestone_storage', '--out', str(copy / 'ev'), '--checks', 'populated-downgrade'],
                            cwd=copy, env=env, capture_output=True, text=True, timeout=300)
    row = next(json.loads(line) for line in result.stdout.splitlines() if line.startswith('{"name"'))
    rows.append({'mutant': name, 'files': [path for path, _, _ in edits], 'killed': not row['passed'], 'error': row.get('error')}); print(json.dumps(rows[-1]), flush=True)
(out / 'mutants.json').write_text(json.dumps(rows, indent=2) + '\n')
print(f"{sum(r['killed'] for r in rows)} of {len(rows)} killed")
