"""A5.3 Part 2 review finding (P3): B01 must compare target handles. Mutant M26 on fresh copies of the isolation base.

M26: the editor serializes every edge into a Join with targetHandle 'tools' (the canvas still draws it), the
wrong-port payload the review built by hand; the real validator rejects it ("flow must enter the input port").
Run 1: the old spec (in the base) under M26 -- the gap: B01 passes. Run 2: the corrected spec under M26 -- B01
must fail by assertion. Run 3: the corrected spec without M26 -- B01 passes.
Usage: python m26.py <file holding the isolation path> <output file>
"""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); OUT = sys.argv[2]
SPEC = 'frontend/tests/browser/parallel-branches.spec.ts'
ED = 'frontend/components/workflow-editor.tsx'
A = "      targetHandle: e.targetHandle,\n"
B = "      targetHandle: nodes.find((n) => n.id === e.target)?.data.spec.type === 'join' ? 'tools' : e.targetHandle,\n"
lines = []
for name, mutate, new_spec in [('old spec + M26', True, False), ('corrected spec + M26', True, True), ('corrected spec, no mutant', False, True)]:
    work = pathlib.Path(tempfile.mkdtemp(prefix='a53-p2-M26-', dir=BASE.parent)); repo = work / 'repo'
    shutil.copytree(BASE, repo, symlinks=True, ignore=shutil.ignore_patterns('.git', '.vite-browser-tests', 'dist', 'test-results', 'playwright-report'))
    if mutate:
        p = repo / ED; s = p.read_text(); assert s.count(A) == 1; p.write_text(s.replace(A, B))
    if new_spec: (repo / SPEC).write_bytes(pathlib.Path(SPEC).read_bytes())
    r = subprocess.run(['node', 'node_modules/@playwright/test/cli.js', 'test', 'tests/browser/parallel-branches.spec.ts', '-g', 'B01',
                        '--reporter=line', '--timeout=20000'], cwd=repo / 'frontend', capture_output=True, text=True)
    out = re.sub(r'\x1b\[[0-9;]*m', '', r.stdout + r.stderr)
    summary = ' '.join(re.findall(r'^\s+(\d+ (?:passed|failed))', out, re.M))
    detail = [l.strip() for l in out.splitlines() if re.search(r'^\s+[-+]\s+"|Error:', l)][:6]
    lines.append(f'{name}: B01 {summary}' + ''.join(f'\n  {d}' for d in detail)); print(lines[-1], flush=True)
open(OUT, 'w').write('\n'.join(lines) + '\n')
