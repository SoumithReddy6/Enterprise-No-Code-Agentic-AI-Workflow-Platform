"""DEF-03 mutants. Each mutant is one exact text replacement in product code, applied to a
fresh scratch copy of the fix (git archive of the isolation commit). M00 changes nothing and
must pass every oracle. An oracle that selects no test stops the run, and each planned test
must be among the results: a kill is real only when that test ran.

Oracle `walk`: test_validation_cost.py and the files whose workflows the fixture records or
whose rules use the walks (test_switch, test_request_contract_validation, test_provenance_static,
test_provenance_foundation, test_repeat, test_compiler, test_execution_safety). Oracle `full`:
the whole backend suite, run for M00 (control) and for a mutant that `walk` does not kill.
A mutant is killed when any test fails; `planned_hit` lists which of its planned tests failed.
Usage: python mutants.py <git repo of the fix> <out dir> [ids...]
"""
import json, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

COMPILER, PROVENANCE, MAIN = 'backend/app/compiler.py', 'backend/app/provenance.py', 'backend/app/main.py'
T = 'backend/tests/test_validation_cost.py::'
MUTANTS = {
    'M00': (None, None, None, []),
    # The card's two (docs/tasks/02-DEF-03-validation-exponential-time.md, "Mutants").
    'M01': (COMPILER, '    key = (*state, stop_at_sources)\n', '    key = state\n',
            [T + 'test_a_record_read_after_a_model_query_is_still_a_trusted_switch_input']),
    'M02': (COMPILER, '    visited = set()\n    found = _model_walk(', "    visited = memo.setdefault('visited', set())\n    found = _model_walk(",
            [T + 'test_a_diamond_with_a_model_on_one_side_is_still_refused']),
    # The rule of the session prompt: a node on the current path must not be cached as False.
    'M03': (COMPILER, '    if found:memo[key] = True\n    return found', '    memo[key] = found\n    return found',
            [T + 'test_a_node_on_the_walk_path_is_not_remembered_as_safe']),
    'M04': (COMPILER, '    if not found:memo.update(', '    memo.update(',
            [T + 'test_a_node_on_the_walk_path_is_not_remembered_as_safe']),
    'M05': (COMPILER, '    if key in memo:return memo[key]\n', '',
            [T + 'test_unchecked_model_input_visits_each_node_once']),
    'M06': (PROVENANCE, '    if remember and cut[0] >= depth:memo[(ref, field)] = value', '    memo[(ref, field)] = value',
            [T + 'test_a_label_found_on_the_walk_path_is_not_remembered']),
    'M07': (PROVENANCE, '    if remember and cut[0] >= depth:memo', '    if remember and cut[0] >= depth - 1:memo',
            [T + 'test_a_label_found_on_the_walk_path_is_not_remembered']),
    'M08': (PROVENANCE, 'len(refs) - 1 <= len(nodes) * 4)[0]', 'True)[0]', []),
    'M09': (PROVENANCE, "    if (ref, field) in memo:return memo[(ref, field)], _NO_CUT\n", '',
            [T + 'test_static_label_cost_is_linear']),
    'M10': (COMPILER, '    if not sep or not node or source in path:return False\n    if node.type in VERIFIERS:return port',
            '    if not sep or not node:return False\n    if source in path:return True\n    if node.type in VERIFIERS:return port',
            [T + 'test_each_walk_gives_the_same_answers_as_before_on_small_graphs[fresh-memo]']),
    'M11': (COMPILER, '    if source in memo:return memo[source]\n', '',
            [T + 'test_checked_dependency_cost_is_linear']),
    'M12': (MAIN, 'return await asyncio.to_thread(validate_workflow,workflow)+', 'return validate_workflow(workflow)+',
            [T + 'test_validate_route_does_not_block_the_event_loop']),
}
WALK = [f'backend/tests/{name}' for name in ('test_validation_cost.py', 'test_switch.py', 'test_request_contract_validation.py',
        'test_provenance_static.py', 'test_provenance_foundation.py', 'test_repeat.py', 'test_compiler.py', 'test_execution_safety.py')]


def pytest(copy, out, name, targets):
    r = subprocess.run([sys.executable, '-m', 'pytest', *targets, '-q', '-rA', '-p', 'no:cacheprovider'],
                       cwd=copy, capture_output=True, text=True, timeout=1800)
    (out / f'{name}.txt').write_text(r.stdout + r.stderr)
    results = {m.group(2): m.group(1).lower() for m in re.finditer(r'^(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED) (\S+)', r.stdout, re.M)}
    if not results: raise SystemExit(f'{out.name}: the {name} oracle selected no test')
    return results


def run(mid, repo, root):
    path, old, new, planned = MUTANTS[mid]
    copy = Path(tempfile.mkdtemp(prefix=f'd03-{mid}-', dir=root)); out = root / 'results' / mid; out.mkdir(parents=True, exist_ok=True)
    subprocess.run(f'git -C "{repo}" archive HEAD | tar -x -C "{copy}"', shell=True, check=True)
    if path:
        text = (copy / path).read_text()
        if text.count(old) != 1: raise SystemExit(f'{mid}: the mutation point occurs {text.count(old)} times')
        (copy / path).write_text(text.replace(old, new))
    # Some tests read the commit (git rev-parse HEAD), so the copy is a repository too.
    subprocess.run('git init -q && git add -A && git -c user.name=m -c user.email=m@local commit -qm m', shell=True, cwd=copy, check=True)
    results = pytest(copy, out, 'walk', WALK)
    bad = lambda rs: sorted(k for k, v in rs.items() if v in ('failed', 'error', 'xpass'))
    for p in planned:
        if p not in results: raise SystemExit(f'{mid}: the planned test {p} did not run')
    planned_hit = [p for p in planned if results[p] != 'passed']
    killed_walk = bool(bad(results))
    oracle = 'walk'
    if mid == 'M00' or not killed_walk:
        results = pytest(copy, out, 'full', ['backend/tests']); oracle = 'walk+full'
    failed = bad(results)
    killed = (not failed) if mid == 'M00' else (killed_walk or bool(failed))
    result = {'id': mid, 'file': path, 'planned': planned, 'planned_hit': planned_hit, 'oracle': oracle, 'failed': failed, 'killed': killed,
              'ran': len(results)}
    (out / 'result.json').write_text(json.dumps(result, indent=1) + '\n')
    shutil.rmtree(copy)  # A path this run created with mkdtemp.
    print(mid, 'control passes' if mid == 'M00' and killed else ('killed' if killed else 'SURVIVED'), oracle, failed, flush=True)
    return result


if __name__ == '__main__':
    repo, root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(); ids = sys.argv[3:] or list(MUTANTS)
    with ThreadPoolExecutor(3) as pool: results = list(pool.map(lambda m: run(m, repo, root), ids))
    (root / 'summary.json').write_text(json.dumps({r['id']: {'control_passes' if r['id'] == 'M00' else 'killed': r['killed'],
        'oracle': r['oracle'], 'planned': r['planned'], 'planned_hit': r['planned_hit'], 'failed': r['failed'], 'ran': r['ran']} for r in results}, indent=1) + '\n')
