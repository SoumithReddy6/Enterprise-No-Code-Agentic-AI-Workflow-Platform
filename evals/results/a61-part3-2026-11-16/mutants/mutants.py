"""A6.1 Part 3 mutants. Each mutant is one exact text replacement in product code, applied to
a fresh scratch copy of the implementation (git archive of the isolation commit). M00 changes
nothing and must pass every oracle. An oracle that selects no test, or a lab that runs no row,
stops the run: a kill is real only when the oracle ran.

Oracles: `va` = backend/tests/test_repeat_write_approval.py (every test's result recorded);
`lab` = the Repeat lab of the gate (repeat_lab.py: S25-S29, every row recorded).
Usage: python mutants.py <git repo of the implementation> <out dir> [ids...]
"""
import json, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPEAT, COMPILER, STORAGE = 'backend/app/repeat.py', 'backend/app/compiler.py', 'backend/app/storage.py'
MUTANTS = {
    'M00': (None, None, None, ['lab', 'va'], []),
    'G1': (REPEAT, '        text = source if previous is None else exact_json.dumps(', '        text = source if True else exact_json.dumps(', ['lab'], ['S25']),
    'G2': (REPEAT, '    met = judge(node_id, number, verdict, config)', '    met = judge(node_id, number, value, config)', ['lab'], ['S25']),
    'G3': (REPEAT, '    met = judge(node_id, number, verdict, config)', '    met = True if index == 0 else judge(node_id, number, verdict, config)', ['lab'], ['S25', 'S26a']),
    'G4': (REPEAT, "        if config.on_limit == 'fail':\n            raise ValueError(f'{node_id}: the stop rule was not met", "        if False:\n            raise ValueError(f'{node_id}: the stop rule was not met", ['lab'], ['S26a']),
    'G5': (COMPILER, "        errors += [f'{n.id}: external writes belong after the Repeat.' for n in attached if is_write_node(n, REGISTRY.get(n.type))]", '        errors += []', ['lab'], ['S26c']),
    'G6': (REPEAT, "        if checked[0] == 'budget':\n            return {'index': index, 'status': 'interrupted', 'budget_halt': checked[1], 'calls': calls + checked[2]}",
           "        if checked[0] == 'budget':\n            return {'index': index, 'status': 'success', 'value': value, 'verdict': '{\"pass\": true, \"feedback\": \"\"}', 'met': True}", ['lab'], ['S27']),
    'G7': (REPEAT, '    rows = checked_rows(node_id, completed_indices(ctx.run, node_id), config)', '    rows = []', ['lab'], ['S28']),
    'G8': (STORAGE, "JobRecord.lease_until<timestamp))", "JobRecord.lease_until<timestamp+25))", ['lab'], ['S28']),
    'G9': (COMPILER, '                if completed and node.id in completed:', "                if completed and node.id in completed and node.type!='repeat':", ['lab'], ['S29']),
    'G10': (COMPILER, "        if config.on_limit == 'last':", '        if False:', ['lab', 'va'], ['S26b', 'test_va1_']),
    'G11': (COMPILER, '            later = [full[id] for id in attachment_closure(after_repeat(node.id, flow), deps) if id in full]',
            '            later = [full[id] for id in after_repeat(node.id, flow) if id in full]', ['va'], ['test_va4_', 'test_va5_', 'test_va6_']),
    'G12': (COMPILER, "n.config.get('approval') is not True]", "n.config.get('approval') is False]", ['va'], ['test_va2_']),
    'G13': (COMPILER, " if e.source == repeat_id and e.sourceHandle != 'error']", ' if e.source == repeat_id]', ['va'], ['test_va10_']),
    'G14': (COMPILER, '    for edge in flow.edges: outgoing.setdefault(edge.source, []).append(edge.target)',
            "    for edge in flow.edges:\n        if edge.sourceHandle != 'error': outgoing.setdefault(edge.source, []).append(edge.target)", ['va'], ['test_va12_']),
    'G15': (COMPILER, '        if step in found or step == repeat_id: continue', '        if step == repeat_id: continue', ['va'], ['test_va13_']),
}


def va(copy, out):
    r = subprocess.run([sys.executable, '-m', 'pytest', 'backend/tests/test_repeat_write_approval.py', '-q', '-rA', '-p', 'no:cacheprovider'],
                       cwd=copy, capture_output=True, text=True, timeout=300)
    (out / 'va.txt').write_text(r.stdout + r.stderr)
    results = {m.group(2).split('::')[-1]: m.group(1).lower() for m in re.finditer(r'^(PASSED|FAILED|ERROR) (\S+)', r.stdout, re.M)}
    if not results: raise SystemExit(f'{out.name}: the va oracle selected no test')
    return results


def lab(copy, out):
    subprocess.run([sys.executable, str(HERE / 'repeat_lab.py'), str(out / 'lab')], cwd=copy, capture_output=True, text=True, timeout=600)
    rows = json.loads((out / 'lab/rows.json').read_text())
    ids = [r['id'] for r in rows]
    if not set(ids) >= {'S25', 'S26a', 'S26b', 'S26c', 'S27', 'S28', 'S29'}: raise SystemExit(f'{out.name}: the lab ran {ids}')
    return {r['id']: r['status'] for r in rows}, {r['id']: r.get('error') for r in rows if r['status'] != 'passed'}


def run(mid, repo, root):
    path, old, new, oracles, planned = MUTANTS[mid]
    copy = Path(tempfile.mkdtemp(prefix=f'p3-{mid}-', dir=root)); out = root / 'results' / mid; out.mkdir(parents=True, exist_ok=True)
    subprocess.run(f'git -C "{repo}" archive HEAD | tar -x -C "{copy}"', shell=True, check=True)
    if path:
        text = (copy / path).read_text()
        if text.count(old) != 1: raise SystemExit(f'{mid}: the mutation point occurs {text.count(old)} times')
        (copy / path).write_text(text.replace(old, new))
    result = {'id': mid, 'file': path, 'planned': planned, 'results': {}, 'errors': {}}
    if 'va' in oracles: result['results'].update(va(copy, out))
    if 'lab' in oracles:
        rows, errors = lab(copy, out); result['results'].update(rows); result['errors'] = errors
    failed = sorted(k for k, v in result['results'].items() if v != 'passed')
    plan_hits = {p: [k for k in result['results'] if k == p or k.startswith(p)] for p in planned}
    for p, hits in plan_hits.items():
        if not hits: raise SystemExit(f'{mid}: the planned oracle {p} selected nothing')
    result['failed'] = failed
    result['killed'] = (not failed) if mid == 'M00' else all(any(result['results'][h] != 'passed' for h in hits) for hits in plan_hits.values())
    (out / 'result.json').write_text(json.dumps(result, indent=1) + '\n')
    shutil.rmtree(copy)  # A path this run created with mkdtemp.
    print(mid, 'control passes' if mid == 'M00' and result['killed'] else ('killed' if result['killed'] else 'SURVIVED'), failed, flush=True)
    return result


if __name__ == '__main__':
    repo, root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(); ids = sys.argv[3:] or list(MUTANTS)
    with ThreadPoolExecutor(4) as pool: results = list(pool.map(lambda m: run(m, repo, root), ids))
    (HERE / 'summary.json').write_text(json.dumps({r['id']: {'killed' if r['id'] != 'M00' else 'control_passes': r['killed'],
                                                            'planned': r['planned'], 'failed': r['failed']} for r in results}, indent=1) + '\n')
