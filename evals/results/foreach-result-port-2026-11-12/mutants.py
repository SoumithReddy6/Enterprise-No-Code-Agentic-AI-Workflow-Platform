"""Mutants for the For each result-port fix. Each mutant applies its edits to one product
file in a fresh scratch copy of the tree, runs its oracle tests there, and must make them
fail. The working tree is not changed.

Run from the repository root: .venv/bin/python evals/results/foreach-result-port-2026-11-12/mutants.py <scratch dir>
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PYTHON = str(ROOT / '.venv/bin/python')
I = 'backend/app/iteration.py'
C = 'backend/app/compiler.py'
TESTS = 'backend/tests/test_for_each_result_port.py'

MUTANTS = [
    ('P1', "a Retrieve body is read from 'text'", I,
     [("RESULT_PORTS={'retrieve':'context'}", "RESULT_PORTS={}")], 'f01 or f02'),
    ('P2', "a missing result port reads as '' with status success (00281fb)", I,
     [("        elif not isinstance(outcome.outputs.get(result_port(body.type)),str):", "        elif False:"),
      ("'value':output[port],'error':''}", "'value':output.get(port,''),'error':''}")], 'f03'),
    ('P3', "the item label is read from 'text'", I,
     [("'text':provenance.output_label(output,port)}}", "'text':provenance.output_label(output)}}")], 'f02'),
    ('P4', 'a saved item row is not checked on resume', I,
     [("            check_restored_item(ctx.node_id,body,index,saved)\n", "")], 'f04'),
    ('P5', 'a loop checkpoint is not checked on restore', C,
     [("                                check_restored_item(node.id,body,position,entry)", "                                pass")], 'f04'),
    ('P6', 'a dropped value counts as a lost result', I,
     [("            and entry.get('value')=='' and not entry.get('value_dropped')):", "            and entry.get('value')==''):")], 'f05'),
    ('P7', 'an empty text result counts as a lost result', I,
     [("    if (result_port(body.type)!='text' and isinstance(entry,dict)", "    if (isinstance(entry,dict)")], 'f06'),
]


def main():
    scratch = Path(sys.argv[1])
    results = []
    for ident, what, path, edits, oracle in MUTANTS:
        copy = Path(tempfile.mkdtemp(prefix=f'{ident}-', dir=scratch))
        for part in ('backend', 'scripts', 'frontend/tests/browser'):
            shutil.copytree(ROOT / part, copy / part, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
        source = (copy / path).read_text()
        counts = [source.count(old) for old, _ in edits]
        if counts != [1] * len(edits):
            results.append({'id': ident, 'what': what, 'killed': False, 'error': f'pattern counts {counts}'})
            print(json.dumps(results[-1]), flush=True); continue
        for old, new in edits: source = source.replace(old, new)
        (copy / path).write_text(source)
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
