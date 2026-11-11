"""Mutants for the A6.1 Part 1 recheck fix (recheck of 936e6c8: the upstream guard inside a
Repeat). Each mutant edits one product file in a fresh scratch copy of the tree, runs its
oracle tests there, and must make them fail. The working tree is not changed.

Run from the repository root: .venv/bin/python evals/results/a61-part1-recheck-2026-11-11/mutants.py <scratch dir>
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYTHON = str(ROOT / '.venv/bin/python')
R = 'backend/app/repeat.py'
C = 'backend/app/compiler.py'
TESTS = 'backend/tests/test_repeat.py'

MUTANTS = [
    ('O1', 'the compiler does not hand the upstream decision to the Repeat (936e6c8)', C,
     "upstream_abstained=any(guarded_dependency(ref.split('.')[0],set()) for ref in node.inputs.values()))",
     "upstream_abstained=False)", 'r27g'),
    ('O2', 'the handler ignores the upstream decision', R,
     "        if ctx.upstream_abstained and (checker is not None or body.type in MODEL_TYPES):",
     "        if False:", 'r27g or r27h or r27j'),
    ('O3', 'a checker does not count as a model the loop would call', R,
     "        if ctx.upstream_abstained and (checker is not None or body.type in MODEL_TYPES):",
     "        if ctx.upstream_abstained and body.type in MODEL_TYPES:", 'r27h'),
    ('O4', 'every Repeat refuses, also a tool body without a checker', R,
     "        if ctx.upstream_abstained and (checker is not None or body.type in MODEL_TYPES):",
     "        if ctx.upstream_abstained:", 'r27i'),
    ('O5', 'the upstream refusal is not saved as a row (the loop_item event persists it)', R,
     "        await emit({'kind': 'loop_item', 'status': 'info',",
     "        if not (entry['status'] == 'abstained' and ctx.upstream_abstained): await emit({'kind': 'loop_item', 'status': 'info',", 'r27j'),
]
def main():
    scratch = Path(sys.argv[1])
    results = []
    for ident, what, path, old, new, oracle in MUTANTS:
        copy = Path(tempfile.mkdtemp(prefix=f'{ident}-', dir=scratch))
        for part in ('backend', 'scripts', 'frontend/tests/browser'):
            shutil.copytree(ROOT / part, copy / part, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
        source = (copy / path).read_text()
        if source.count(old) != 1:
            results.append({'id': ident, 'what': what, 'killed': False, 'error': f'pattern found {source.count(old)} times'})
            print(json.dumps(results[-1]), flush=True); continue
        (copy / path).write_text(source.replace(old, new))
        run = subprocess.run([PYTHON, '-m', 'pytest', TESTS, '-q', '-p', 'no:cacheprovider', '-k', oracle],
                             cwd=copy, capture_output=True, text=True, timeout=600)
        tail = run.stdout.strip().splitlines()[-1] if run.stdout.strip() else run.stderr[-300:]
        results.append({'id': ident, 'what': what, 'file': path, 'oracle': oracle, 'killed': run.returncode == 1 and 'failed' in tail, 'result': tail})
        print(json.dumps(results[-1]), flush=True)
    (Path(__file__).parent / 'mutants.json').write_text(json.dumps(results, indent=2) + '\n')
    print('killed', sum(r['killed'] for r in results), 'of', len(results))
    return 0 if all(r['killed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
