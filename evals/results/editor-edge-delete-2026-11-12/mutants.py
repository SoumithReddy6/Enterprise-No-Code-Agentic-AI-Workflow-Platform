"""Mutants for the editor Delete fix. Each mutant edits workflow-editor.tsx in a fresh
scratch copy of frontend/ (node_modules linked, not copied), runs its oracle browser tests
there, and must make them fail. The working tree is not changed.

Run from the repository root: .venv/bin/python evals/results/editor-edge-delete-2026-11-12/mutants.py <scratch dir>
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
F = 'components/workflow-editor.tsx'
LINE = "                  if (changes.some((c) => c.type === 'select' && c.selected)) select('');\n"
CLEAR = "    if (id) setEdges((es) => (es.some((e) => e.selected) ? es.map((e) => (e.selected ? { ...e, selected: false } : e)) : es));\n"
DROP = "      { id, type: 'workflow', position: spec.position, data: { spec } },\n    ]);\n    select(id);\n"

MUTANTS = [
    ('Q1', 'an edge selection keeps the editor node selection (the original bug)', [(LINE, '')], 'D01|D02'),
    ('Q2', 'the node selection is cleared only when an edge is removed', [
        (LINE, "                  if (changes.some((c) => c.type === 'remove')) select('');\n")], 'D01|D02'),
    ('Q3', 'a node the editor selects keeps the edge selection', [(CLEAR, '')], 'D05'),
    ('Q4', 'the palette drop selects the node without the helper', [
        (DROP, DROP.replace('    select(id);', '    setSelected(id);'))], 'D05'),
]
SKIP = ('node_modules', 'dist', 'test-results', 'playwright-report', 'playwright-report 2', 'tsconfig.tsbuildinfo')


def main():
    scratch = Path(sys.argv[1])
    results = []
    for ident, what, edits, oracle in MUTANTS:
        copy = Path(tempfile.mkdtemp(prefix=f'{ident}-', dir=scratch)) / 'frontend'
        shutil.copytree(ROOT / 'frontend', copy, ignore=shutil.ignore_patterns(*SKIP))
        (copy / 'node_modules').symlink_to(ROOT / 'frontend' / 'node_modules')
        source = (copy / F).read_text()
        counts = [source.count(old) for old, _ in edits]
        if counts != [1] * len(edits):
            results.append({'id': ident, 'what': what, 'killed': False, 'error': f'pattern counts {counts}'})
            print(json.dumps(results[-1]), flush=True); continue
        for old, new in edits: source = source.replace(old, new)
        (copy / F).write_text(source)
        run = subprocess.run(['npx', 'playwright', 'test', 'tests/browser/edge-delete.spec.ts', '--reporter=line', '-g', oracle],
                             cwd=copy, capture_output=True, text=True, timeout=600)
        tail = [l.strip() for l in run.stdout.splitlines() if l.strip().endswith(('passed', 'failed')) or ' failed' in l or ' passed (' in l]
        results.append({'id': ident, 'what': what, 'oracle': oracle, 'killed': run.returncode != 0 and any('failed' in l for l in tail),
                        'result': '; '.join(tail[-2:])})
        print(json.dumps(results[-1]), flush=True)
    (Path(__file__).parent / 'mutants.json').write_text(json.dumps(results, indent=2) + '\n')
    print('killed', sum(r['killed'] for r in results), 'of', len(results))
    return 0 if all(r['killed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
