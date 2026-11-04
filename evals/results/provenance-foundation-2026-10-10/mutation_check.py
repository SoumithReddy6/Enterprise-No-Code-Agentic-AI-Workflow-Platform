"""Task-1 fault checks in a disposable export; never mutate the user's checkout.

Run with the repository virtualenv from the repository root. Each mutation must
compile and fail its targeted permanent regression; normal import/syntax failures
are not accepted as proof.
"""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path.cwd()
OUT = Path(__file__).resolve().parent
SCRATCH = Path(tempfile.mkdtemp(prefix='relay-foundation-mutants-', dir='/private/tmp'))
with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git', 'archive', 'HEAD']))) as archive:
    archive.extractall(SCRATCH, filter='data')
files = subprocess.check_output(['git', 'diff', '--name-only', '--', 'backend'], text=True).splitlines()
files.append('backend/tests/test_provenance_foundation.py')
for name in files:
    target = SCRATCH / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / name, target)

P = 'backend/app/provenance.py'
C = 'backend/app/calculate.py'
T = 'backend/tests/test_provenance_foundation.py'
MUTANTS = [
    ('template_evidence', P, "if port not in record.get('eligible_ports', []):", 'if False:', 'prompt_examples'),
    ('opaque_tool_source', P, "else:\n        record['ports'] = {p: GUESSED for p in ports}\n    return record",
     "else:\n        record['ports'] = {p: SOURCE for p in ports}\n    return record", 'python_echo'),
    ('field_identity', P, "return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)",
     "return '/' + '.'.join(str(p) for p in parts)", 'field_identity'),
    ('numeric_prefix', P, "value = exact_json.loads(digits.replace(',', '').lstrip('+'))",
     "value = Decimal(re.match(r'[+-]?[0-9]+', digits).group())", 'exponents_quote'),
    ('unused_operators', C, "if node.value == '+': return left + right\n                if node.value == '-': return left - right\n                return left * right",
     "return {'+': left + right, '-': left - right, '*': left * right}[node.value]", 'selected_arithmetic'),
    ('rounded_sign', C, 'operand.copy_negate()', '-operand', 'sign_operations'),
    ('rounded_abs', C, 'args[0].copy_abs()', 'abs(args[0])', 'sign_operations'),
    ('field_byte_budget', P, 'while len(json.dumps(labels, ensure_ascii=False).encode()) > MAX_BYTES - 768:',
     'while False:', '64_long_fields'),
    ('static_short_circuit', P, "used, conditional = dependencies(parse((node.config or {}).get('expression', '')))",
     "from .calculate import paths\n            used, conditional = paths(parse((node.config or {}).get('expression', ''))), False", 'static_short_circuits'),
    ('lost_decision_receipt', 'backend/app/compiler.py', "receipt=(outputs.get(PROVENANCE_KEY) or {}).get('decision')",
     'receipt=None', 'checkpoint_replay_preserves'),
    ('false_absence', P, "raise IncompleteTraversal('provenance nesting limit')", 'return', 'depth_limit'),
    ('attached_labels', 'backend/app/execution_policy.py', 'return {**outputs,provenance.PROVENANCE_KEY:provenance.checked(record)}',
     'return outputs', 'attached_labels_and_item_labels'),
    ('grounding_source', P, "GUESSED if p in ('text', 'grounding') else SOURCE", "GUESSED if p == 'text' else SOURCE", 'grounding'),
    ('old_label_acceptance', P, "if record['v'] == 2 or independent_source(node):", 'if True:', 'legacy_opaque_tool or pre_fix_derived'),
]
results = []
for name, filename, old, new, selection in MUTANTS:
    path = SCRATCH / filename
    original = path.read_text()
    assert original.count(old) == 1, (name, original.count(old))
    changed = original.replace(old, new)
    compile(changed, filename, 'exec')
    path.write_text(changed)
    try:
        run = subprocess.run([str(ROOT / '.venv/bin/python'), '-B', '-m', 'pytest', '-q', T, '-k', selection],
                             cwd=SCRATCH, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    finally:
        path.write_text(original)
    (OUT / f'mutant-{name}.log').write_text(run.stdout)
    killed = (run.returncode == 1 and 'failed' in run.stdout and 'ERROR collecting' not in run.stdout
              and 'ImportError' not in run.stdout and 'NameError' not in run.stdout)
    results.append({'mutation': name, 'target': selection, 'exit_code': run.returncode, 'killed': killed})
    print(name, 'caught' if killed else 'NOT CAUGHT', flush=True)
(OUT / 'mutations.json').write_text(json.dumps({'base_commit': subprocess.check_output(
    ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'results': results}, indent=2) + '\n')
raise SystemExit(0 if all(r['killed'] for r in results) else 1)
