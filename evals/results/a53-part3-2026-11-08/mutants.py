"""A5.3 Part 3 mutants M27-M33: each is applied to product code in a fresh copy of the isolation base,
then the four fork rows (rows.py) run against deployed API and worker processes. A mutant is
KILLED when its planned row fails by assertion and all four rows ran (no LAB/boot failure).
Usage: python mutants.py <file holding the isolation path> <rows.py> <output file> [mutant ...]

Run 1 (mutants-run1.txt) had a defective M27: each node got its own lock, so nothing was
serialized and M27 survived. The shared lock above is the correction.
"""
import json, pathlib, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); ROWS = pathlib.Path(sys.argv[2]).resolve(); OUT = sys.argv[3]
C = 'backend/app/compiler.py'
JOIN = "            graph.add_edge(sorted(e.source for e in workflow.edges if e.target == node.id), node.id)"
SERIAL = '''

_M27_LOCK = []


def _m27_serial(fn):
    """M27: one node handler at a time in the worker process (one lock shared by every node)."""
    import asyncio
    async def run(state):
        if not _M27_LOCK: _M27_LOCK.append(asyncio.Lock())
        async with _M27_LOCK[0]: return await fn(state)
    return run
'''
M = {
    # One node handler at a time: branches cannot overlap.
    'M27': ([(C, "        graph.add_node(node.id, handler_factory(node, definition, config))",
                 "        graph.add_node(node.id, _m27_serial(handler_factory(node, definition, config)))"),
             (C, "\n\nBRANCH_CLOSED = ", SERIAL + "\n\nBRANCH_CLOSED = ")], 'S22'),
    # The Join is entered by one edge per branch instead of one list edge.
    'M28': ([(C, JOIN, "            for source in sorted(e.source for e in workflow.edges if e.target == node.id): graph.add_edge(source, node.id)")], 'S22'),
    # A failed HTTP tool node (the branch nodes of these rows) is recovered as if on_error were continue.
    'M29': ([(C, "                          if node.on_error=='fail':\n                              await emit(failure)",
                 "                          if node.on_error=='fail' and node.type!='tool_http':\n                              await emit(failure)")], 'S23a'),
    # The rule "external writes belong after the Join" is removed.
    'M30': ([(C, "                    errors.append(f'{id}: external writes belong after the Join.')",
                 "                    pass")], 'S23b'),
    # Restore ignores saved checkpoints of HTTP tool nodes (the branch nodes of these rows).
    'M31': ([(C, "                if completed and node.id in completed:\n",
                 "                if completed and node.id in completed and node.type!='tool_http':\n")], 'S24'),
    # Join bindings use the intersection, not the union.
    'M32': ([(C, "        combine = set.union if nodes[id].type == 'join' else set.intersection",
                 "        combine = set.intersection")], 'S22'),
    # The Join waits only for its first branch (the review's mutation).
    'M33': ([(C, JOIN, "            graph.add_edge(sorted(e.source for e in workflow.edges if e.target == node.id)[:1], node.id)")], 'S22'),
}
lines, ok, detail = [], True, {}
for name, (edits, planned) in M.items():
    if sys.argv[4:] and name not in sys.argv[4:]: continue
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'a53-p3-{name}-', dir=BASE.parent)); repo = work / 'repo'
    shutil.copytree(BASE, repo, ignore=shutil.ignore_patterns('.git', '__pycache__'))
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b, 1))
    out = work / 'rows'
    r = subprocess.run([sys.executable, '-u', str(ROWS), str(out)], cwd=repo, env={**__import__('os').environ, 'PYTHONPATH': str(repo)},
                       capture_output=True, text=True, timeout=400)
    rows = json.loads((out / 'rows.json').read_text()) if (out / 'rows.json').exists() else []
    failed = [row['id'] for row in rows if row['status'] == 'failed']
    clean = len(rows) == 4 and not any(k in (row.get('error') or '') for row in rows for k in ('ImportError', 'SyntaxError', 'ModuleNotFoundError'))
    good = clean and planned in failed; ok &= good
    detail[name] = {row['id']: row.get('error', 'passed') for row in rows}
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned {planned} | failed: {', '.join(failed) or 'none'}"
                 f"{'' if clean else ' | REJECTED: rows did not all run: ' + r.stderr[-400:]}")
    for row in rows:
        if row['status'] == 'failed': lines.append(f"  {row['id']}: {row['error'][:300]}")
    print('\n'.join(lines[-1 - len(failed):]), flush=True)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n")
pathlib.Path(OUT).with_suffix('.json').write_text(json.dumps(detail, indent=2))
print(f'ALL PLANNED KILLS: {ok}')
