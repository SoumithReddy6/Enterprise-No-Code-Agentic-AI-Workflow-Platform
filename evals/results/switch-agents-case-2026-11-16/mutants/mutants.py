"""Run the Switch `agents` case mutants A1-A3 in a scratch copy of the frontend.

Usage: mutants.py <copy> <out dir> [ids]. M00 is the control (no change): every oracle must
pass, and an oracle that selects no test stops the run, so a kill cannot be a missing test.
"""
import json, subprocess, sys
from pathlib import Path
copy, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
only = set(sys.argv[3:])
W, E = 'lib/workflow.ts', 'components/workflow-editor.tsx'
U = lambda *ids: ('unit', ids)
B = lambda *ids: ('browser', ids)
MUTANTS = {
 'M00': ('control: no change', [], [U('U01','U02','U03','U04','U05','U06','U07','U08','U09','U10'),
                                    B('B01','B02','B03','B04','B05','B06','B07','B08'), ('spec', ('switch.spec.ts',))]),
 'A1': ('agents is the specialists port on every card (the code before the fix)',
        [(W, "if (sourceType === 'agent' && sourceHandle === 'agents')", "if (sourceHandle === 'agents')"),
         (W, "return targetType === 'agent' ? 'agent' : null;", "return sourceType === 'agent' && targetType === 'agent' ? 'agent' : null;")],
        [U('U09'), B('B08')]),
 'A2': ('The Agent card specialists port connects as flow',
        [(W, "if (sourceType === 'agent' && sourceHandle === 'agents')", "if (false && sourceType === 'agent' && sourceHandle === 'agents')")],
        [U('U09')]),
 'A3': ('The Agent card specialists port connects to any target',
        [(W, "return targetType === 'agent' ? 'agent' : null;", "return 'agent';")],
        [U('U09')]),
}
results = {}
for mid, (desc, edits, oracles) in MUTANTS.items():
    if only and mid not in only: continue
    files = {f for f, _, _ in edits}
    saved = {f: (copy / f).read_text() for f in files}
    try:
        for f, old, new in edits:
            text = (copy / f).read_text()
            assert text.count(old) == 1, (mid, f, old[:60], text.count(old))
            (copy / f).write_text(text.replace(old, new))
        log, verdicts = [], []
        for kind, ids in oracles:
            if kind == 'unit':
                cmd = ['node', '--experimental-strip-types', '--test', '--test-name-pattern', '^(' + '|'.join(ids) + ') ', 'tests/repeat.test.ts']
            elif kind == 'browser':
                cmd = ['npx', 'playwright', 'test', 'tests/browser/repeat.spec.ts', '--reporter=line', '-g', '(' + '|'.join(ids) + ') ']
            else:
                cmd = ['npx', 'playwright', 'test', *[f'tests/browser/{i}' for i in ids], '--reporter=line']
            p = subprocess.run(cmd, cwd=copy, capture_output=True, text=True)
            log.append(f"$ {' '.join(cmd)}\nexit {p.returncode}\n{p.stdout[-4000:]}\n{p.stderr[-2000:]}")
            ran_none = 'No tests found' in p.stdout + p.stderr or 'ℹ tests 0' in p.stdout
            assert not ran_none, (mid, kind, ids, 'oracle selected no test')
            verdicts.append({'oracle': f'{kind}:{",".join(ids)}', 'killed': p.returncode != 0})
        (out / f'{mid}.txt').write_text(f'{mid}: {desc}\n\n' + '\n\n'.join(log))
        results[mid] = {'change': desc, 'oracles': verdicts, 'killed': any(v['killed'] for v in verdicts)}
        print(mid, results[mid]['killed'], [(v['oracle'], v['killed']) for v in verdicts], flush=True)
    finally:
        for f, text in saved.items(): (copy / f).write_text(text)
prev = json.loads((out / 'summary.json').read_text()) if (out / 'summary.json').exists() else {}
(out / 'summary.json').write_text(json.dumps({**prev, **results}, indent=1) + '\n')
