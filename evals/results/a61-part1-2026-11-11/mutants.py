"""A6.1 Part 1 mutants. Each mutant edits one product file in a fresh scratch copy of the
tree, runs its oracle tests there, and must make them fail. The working tree is not changed.

Run from the repository root: .venv/bin/python evals/results/a61-part1-2026-11-11/mutants.py <scratch dir>
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
TESTS = 'backend/tests/test_repeat.py'

MUTANTS = [
    ('M1', 'the stop rule is ignored', R,
     "    met = judge(node_id, number, verdict, config)\n    return {'index': index, 'status': 'success'",
     "    judge(node_id, number, verdict, config); met = False\n    return {'index': index, 'status': 'success'", 'r01'),
    ('M2', 'max_iterations + 1 iterations', R, 'elif len(evaluated) >= limit:', 'elif len(evaluated) > limit:', 'r03'),
    ('M3', 'previous is the first draft', R, "'previous': previous['value']", "'previous': rows[0]['value']", 'r02'),
    ('M4', 'a malformed verdict counts as not met', R,
     "    except ValueError as exc:\n        raise ValueError(f'{node_id}: iteration {number}: {exc}') from None",
     "    except ValueError as exc:\n        return False", 'r06'),
    ('M5', 'on_limit last without truncation metadata', R, '    if reason:\n        outputs[TRUNCATION_KEY]', '    if False:\n        outputs[TRUNCATION_KEY]', 'r04'),
    ('M6', 'saved iterations run again on resume', R,
     '    rows = checked_rows(node_id, completed_indices(ctx.run, node_id), config)\n    restored',
     '    rows = []\n    restored', 'r11'),
    ('M7', 'the restored met is trusted', R, "        if met != row['met'] or (met and not last):", '        if False:', 'r12'),
    ('M8', 'the value is shortened instead of refused', R,
     "            raise ValueError(f'{node_id}: iteration {index + 1} output is too large to save (limit {MAX_ENTRY_BYTES} bytes).')",
     "            entry = {**entry, 'value': entry['value'][:1000], 'verdict': entry['verdict'][:1000]}", 'r13'),
    ('M9', "the write rule skips the checker's closure", 'backend/app/compiler.py',
     'attached = [full[id] for id in attachment_closure(deps.get(node.id, []), deps) if id in full]',
     'attached = [full[id] for id in attachment_closure(deps.get(node.id, [])[:1], deps) if id in full]', 'v04'),
    ('M10', 'the body call is not charged to the action budget (reinterpreted)', R,
     'action_budget=budget, charge_first_attempt=charges_action(node.type))', 'action_budget=budget, charge_first_attempt=False)', 'r08 and not r08b'),
    ('M11', 'reused iterations are not added to reused_owners', R,
     "    await verify_owners(ctx, [owner(node.id, row['index']) for row in rows])\n    reuse(ctx, node.id, rows)",
     "    await verify_owners(ctx, [owner(node.id, row['index']) for row in rows])", 'r15'),
    ('M12', 'nested events lose item_index', R, "        await emit({**event, **loop_fields})", "        await emit({**event, 'loop_node_id': node_id})", 'r10 and not r10b'),
    ('M13', 'kb_errors does not recognize Repeat owners', 'backend/app/platform_validation.py',
     "if node.type in ('for_each','repeat')}", "if node.type=='for_each'}", 'r17 and not r17b and not r17c or r18 and not r18b'),
    ('M14', 'the Repeat does not set child_base', R, ', index, child_base=invocation)', ', index)', 'r10 and not r10b'),
    ('M15', 'child_base is applied to every caller', 'backend/app/agent_runtime.py',
     "(getattr(parent,'child_base',None) or checkpoint_owner)", '(parent.invocation_id if parent is not None else checkpoint_owner)', 'r10b'),
    ('M16', 'the stop rule runs before the completeness check', R,
     '    calls += 1\n    answer = result[1]\n    kind = budget_kind(answer)',
     "    calls += 1\n    answer = result[1]\n    kind = '' if judge(node_id, number, answer.get('text', ''), config) else budget_kind(answer)", 'r20'),
    ('M17', "the interrupted iteration's draft is the fallback", R,
     "            return {'index': index, 'status': 'interrupted', 'budget_halt': checked[1], 'calls': calls + checked[2]}",
     "            return {'index': index, 'status': 'success', 'value': value, 'verdict': value, 'met': False}", 'r19_a_budget'),
    ('M18', 'the interrupted row is not saved', R,
     "        record_progress(ctx, index, entry)\n        await emit(",
     "        if entry['status'] != 'success': rows.append(entry); continue\n        record_progress(ctx, index, entry)\n        await emit(", 'r21a_replay_after'),
    ('M19', "the Repeat's verdict type check is removed", R, '            if kind is not None and json_kind(value) != kind:', '            if False:', 'r22b'),
    ('M20', 'the cached-Repeat restore check is removed', 'backend/app/compiler.py',
     '                        outputs=await restored_outputs(node_ctx,node,config,outputs)\n', '', 'r24 and not r24c'),
    ('M21', "the cached check ignores text", R, '    if derived is None or saved != derived:',
     "    if derived is None or {k: v for k, v in saved.items() if k != 'text'} != {k: v for k, v in derived.items() if k != 'text'}:", 'r24 and text'),
    ('M22', "the handler's point-of-use source check is removed", R,
     '            # The next body receives the saved draft and feedback.\n            await verify_owners(ctx, restored)',
     '            # The next body receives the saved draft and feedback.\n            pass', 'r17b'),
    ('M23', "the restore branch's source check is removed", R,
     "    await verify_owners(ctx, [owner(node.id, row['index']) for row in rows])\n    reuse(", "    reuse(", 'r18b'),
    ('M24', 'no halt row when no call ran', R,
     "        record_progress(ctx, index, entry)\n        await emit(",
     "        if entry['status'] == 'interrupted' and entry['calls'] == 0: rows.append(entry); continue\n        record_progress(ctx, index, entry)\n        await emit(", 'r19b or r21a_replay_of'),
    ('M25', 'the restore equality ignores _truncation', R, '    if derived is None or saved != derived:',
     "    if derived is None or {k: v for k, v in saved.items() if k != '_truncation'} != {k: v for k, v in derived.items() if k != '_truncation'}:", 'r25_a_cached'),
    ('M26', 'run_truncation does not read the Repeat summary', 'backend/app/worker.py',
     "summarized=node_id in loops or (node_id in repeats and 'summary' in outputs)", 'summarized=node_id in loops', 'r25_one_loop'),
    ('M27', 'saving a calls:0 halt row charges an action', R,
     "        record_progress(ctx, index, entry)\n        await emit(",
     "        if entry['status'] == 'interrupted' and entry['calls'] == 0 and (ctx.run or {}).get('action_budget') is not None:\n            ctx.run['action_budget']['counter'] += 1\n        record_progress(ctx, index, entry)\n        await emit(", 'r19b or r21a_replay_of'),
    ('M28', 'the replay re-runs the last fully evaluated row', R,
     '    restored = [owner(node_id, row',
     "    rows = rows[:-1] if rows and rows[-1]['status'] == 'success' else rows\n    restored = [owner(node_id, row", 'r21b'),
    ('M29', "the handler returns a terminal row's draft without the source check", R,
     '            if restored and not verified:\n                await verify_owners(ctx, restored)\n            return outcome',
     '            return outcome', 'r17c'),
    ('M30', 'the restore check compares the whole checkpoint', R,
     '    saved = {key: value for key, value in outputs.items() if key != PROVENANCE_KEY} if isinstance(outputs, dict) else None',
     '    saved = dict(outputs) if isinstance(outputs, dict) else None', 'r24c and not r24c_d'),
    ('M31', 'the restore check ignores keys outside the outcome fields', R,
     '    if derived is None or saved != derived:', '    if derived is None or {k: saved.get(k) for k in derived} != derived:', 'r24c_d'),
    ('M32', "run_truncation also reads a Repeat's _truncation", 'backend/app/worker.py',
     'if outputs.get(TRUNCATION_KEY) and not (node_id in repeats and summarized):', 'if outputs.get(TRUNCATION_KEY):', 'r25_one_loop'),
    ('M33', 'run_truncation ignores _truncation for every Repeat', 'backend/app/worker.py',
     'if outputs.get(TRUNCATION_KEY) and not (node_id in repeats and summarized):', 'if outputs.get(TRUNCATION_KEY) and node_id not in repeats:', 'r25_a_recovered'),
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
