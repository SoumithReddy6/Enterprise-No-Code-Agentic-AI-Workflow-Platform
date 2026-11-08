"""A5.3 Part 2 mutants M21-M25: each applied to a fresh copy of the isolation base; its named oracle must fail.

A failure from an import, transform or collection error is not a kill: both unit cases and all five
browser cases must run, and the oracle must fail by assertion. Copies keep the node_modules symlink.
Usage: python mutants.py <file holding the isolation path> <output file>
"""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); OUT = sys.argv[2]
WT, ED, CSS = 'frontend/lib/workflow.ts', 'frontend/components/workflow-editor.tsx', 'frontend/app/globals.css'
M = {
    # The Join is not offered in the palette.
    'M21': ([(WT, "        join: 'Control',\n", "")], ['B01']),
    # Every edge into the Join counts: error routes, attachments and repeated sources.
    'M22': ([(WT, """  return new Set(
    edges
      .filter((e) => e.target === join && (!e.kind || e.kind === 'flow') && e.sourceHandle !== 'error')
      .map((e) => e.source),
  ).size;""", "  return edges.filter((e) => e.target === join).length;")], ['U02']),
    # The editor's bindableOutputs intersects at a Join (Part 1's M17, now through the UI).
    'M23': ([(WT, "    const join = byId.get(id)?.type === 'join';", "    const join = false;")], ['B01']),
    # The quick guide keeps parallel execution in the future-release note.
    'M24': ([(ED, """                  This foundation supports single-user, acyclic workflows. Team
                  permissions are a future release.""", """                  This foundation supports single-user, acyclic workflows.
                  Loops, parallel execution, approvals and team permissions are
                  future releases.""")], ['B05']),
    # The cancelled card has no dashed border.
    'M25': ([(CSS, "  border-color: #94a3b8;\n  border-style: dashed;\n", "  border-color: #94a3b8;\n")], ['B04']),
}
BAD = re.compile(r'Cannot find module|SyntaxError|Transform failed|Failed to resolve import|ERR_MODULE_NOT_FOUND|Internal server error')
lines = []; ok = True
for name, (edits, planned) in M.items():
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'a53-p2-{name}-', dir=BASE.parent)); repo = work / 'repo'
    shutil.copytree(BASE, repo, symlinks=True, ignore=shutil.ignore_patterns('.git', '.vite-browser-tests', 'dist', 'test-results', 'playwright-report'))
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b))
    fe = repo / 'frontend'
    u = subprocess.run(['node', '--experimental-strip-types', '--test', '--test-reporter=tap', 'tests/parallel.test.ts'], cwd=fe, capture_output=True, text=True)
    unit_failed = [re.match(r'(U\d+)', t).group(1) for t in re.findall(r'^not ok \d+ - (.+)$', u.stdout, re.M)]
    unit_ran = len(re.findall(r'^(?:not )?ok \d+ - U\d+', u.stdout, re.M))
    b = subprocess.run(['node', 'node_modules/@playwright/test/cli.js', 'test', 'tests/browser/parallel-branches.spec.ts', '--reporter=line', '--timeout=15000'],
                       cwd=fe, capture_output=True, text=True)
    out = re.sub(r'\x1b\[[0-9;]*m', '', b.stdout + b.stderr)
    browser_failed = sorted(set(re.findall(r'^\s+\d+\) \S+ › (B\d+)', out, re.M)))
    counts = {k: int(v) for v, k in re.findall(r'^\s+(\d+) (passed|failed|flaky|skipped)', out, re.M)}
    clean = not BAD.search(out + u.stdout + u.stderr) and unit_ran == 2 and sum(counts.values()) == 5
    failed = sorted(set(unit_failed)) + browser_failed
    good = all(t in failed for t in planned) and clean; ok &= good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned {planned} | unit {2 - len(unit_failed)}/2 passed, browser {counts}"
                 f"{'' if clean else ' | REJECTED: import/collection error or missing cases'}\n  failed: {', '.join(failed) or 'none'}")
    print(lines[-1], flush=True)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n"); print(f'ALL PLANNED KILLS: {ok}')
