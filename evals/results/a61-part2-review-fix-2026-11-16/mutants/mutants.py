"""Run the A6.1 Part 2 review-fix mutants M53-M59 in a scratch copy of the frontend.

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
 'M53': ('connectionKind reads body and check as loop ports on every card',
         [(W, "if (isLoopType(sourceType) && (sourceHandle === 'body' || sourceHandle === 'check'))", "if (sourceHandle === 'body' || sourceHandle === 'check')")],
         [U('U09'), B('B08')]),
 'M54': ('savedConnection drops a flow handle on every card',
         [(W, "return isLoopType(sourceType) && connection.sourceHandle === 'flow' ?", "return connection.sourceHandle === 'flow' ?")],
         [U('U09'), B('B08')]),
 'M55': ('edgePort maps a Switch output case to the unnamed bottom port',
         [(W, "  if (sourceType === 'switch') return handle;\n", '')],
         [U('U09'), B('B08')]),
 'M56': ('Removal clears any setting a loop edge names',
         [(W, "if (!isAttachmentPort(type, port) || !owner || owner.config[port!] !== e.target) continue;", "if (!port || !owner || owner.config[port!] !== e.target) continue;")],
         [U('U10')]),
 'M57': ('Attaching sets any setting a loop edge names',
         [(W, "n.id === edge.source && isAttachmentPort(type, port) ?", "n.id === edge.source && port ?")],
         [U('U10')]),
 'M58': ('The editor saves every new connection as if from a loop card',
         [(E, 'const connection = savedConnection(drawn, from.type);', "const connection = savedConnection(drawn, 'repeat');")],
         [B('B08')]),
 'M59': ('For each also treats check as an attachment',
         [(W, "for_each: ['body'] };", "for_each: ['body', 'check'] };")],
         [U('U10'), U('U02')]),
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
