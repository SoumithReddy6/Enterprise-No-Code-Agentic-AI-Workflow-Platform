"""Mutants for the A6.1 Part 1 review fixes (review of 011a9e0). Each mutant edits one product
file in a fresh scratch copy of the tree, runs its oracle tests there, and must make them
fail. The working tree is not changed.

Run from the repository root: .venv/bin/python evals/results/a61-part1-review-2026-11-11/mutants.py <scratch dir>
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
G = 'backend/app/answerability_guard.py'
TESTS = 'backend/tests/test_repeat.py'

MUTANTS = [
    # P1: recheck restored sources before every return.
    ('N1', 'the return check runs only when the sources were not checked before the body (011a9e0)', R,
     '            if restored:\n                await verify_owners(ctx, restored)\n            return outcome',
     '            if restored and not verified:\n                await verify_owners(ctx, restored)\n            return outcome', 'r17d'),
    ('N2', 'no source check at the return', R,
     '            if restored:\n                await verify_owners(ctx, restored)\n            return outcome',
     '            return outcome', 'r17c or r17d'),
    # P2: a guard abstention stops the Repeat and its decision travels on.
    ('N3', 'the body abstention is not checked', R,
     "    if guard_abstained(answer):\n", "    if False:\n", 'r27a or r27b'),
    ('N4', 'a Retrieve or Query decision is not read', R,
     "    if decision is not None and decision['decision'] == 'abstain':\n        return True",
     "    if False:\n        return True", 'r27b'),
    ('N5', "an Agent's honoured abstention is not read", R,
     "        return json.loads(output.get('grounding') or '{}').get('abstention_source') == 'answerability_guard'",
     "        return False", 'r27a or r27c'),
    ('N6', 'the checker abstention is not checked', R,
     "        if guard_abstained(checked[1]):\n", "        if False:\n", 'r27c'),
    ('N7', 'a checker abstention returns the unchecked draft', R,
     "            return {'index': index, 'status': 'abstained', 'value': verdict}",
     "            return {'index': index, 'status': 'abstained', 'value': value}", 'r27c'),
    ('N8', 'the summary does not carry the decision', R,
     "'truncated': False, 'truncation_reason': '', 'answerability': dict(GUARD_DECISION)}",
     "'truncated': False, 'truncation_reason': ''}", 'r27a or r27b'),
    ('N9', 'from_outputs does not read a summary', G,
     "        if value is None:value=json.loads(outputs.get('summary','{}')).get('answerability')\n", "", 'r27b'),
    ('N10', 'a saved guard row may carry other keys', R,
     "            if set(row) != ABSTAIN_KEYS or not last or not isinstance(row['value'], str):",
     "            if not last or not isinstance(row['value'], str):", 'r27e'),
    ('N11', "a saved guard row's value type is not checked", R,
     "            if set(row) != ABSTAIN_KEYS or not last or not isinstance(row['value'], str):",
     "            if set(row) != ABSTAIN_KEYS or not last:", 'r27e'),
    ('N12', 'a saved guard row is not accepted on restore', R,
     "        if row.get('status') == 'abstained':\n", "        if False:\n", 'r27d'),
    ('N13', 'the guard row is not size checked', R,
     "        if entry['status'] != 'interrupted' and entry_bytes(entry) > MAX_ENTRY_BYTES:",
     "        if entry['status'] == 'success' and entry_bytes(entry) > MAX_ENTRY_BYTES:", 'r27f'),
    # P3: the body result comes from its declared port.
    ('N14', "a Retrieve body is read from 'text'", R,
     "RESULT_PORTS = {'retrieve': 'context'}", "RESULT_PORTS = {}", 'r28a or r28b'),
    ('N15', "a missing result port reads as '' (011a9e0)", R,
     "    if not isinstance(value, str):\n        raise ValueError(f\"{node_id}: iteration {number}: the {role}'s {port} output is missing.\")\n    return value",
     "    return value if isinstance(value, str) else ''", 'r28b'),
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
