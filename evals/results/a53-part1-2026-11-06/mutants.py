"""A5.3 Part 1 mutants M1-M17: each applied to a fresh copy of the isolation base; its named oracle must fail.

A failure from an import, collection or fixture error is not a kill: the oracle tests must
fail by assertion, and the modules must collect all their cases (18 + 8 + 6 backend cases;
the PostgreSQL parameters skip here, as they need a database URL).
Usage: python mutants.py <file holding the isolation path> <python> <output file>
"""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); PY = sys.argv[2]; OUT = sys.argv[3]
TESTS = ['backend/tests/test_parallel_branches.py', 'backend/tests/test_parallel_accounting.py', 'backend/tests/test_binding_contract.py']
CO, WO, AS, WT = 'backend/app/compiler.py', 'backend/app/worker.py', 'backend/app/accounting_state.py', 'frontend/lib/workflow.ts'
INSIDE = ("                if (node.type in ('response', 'calculate') or is_control(definition)\n"
          "                        or node.on_error == 'route' or len(outgoing[id]) != 1):\n")
M = {
    # A Join takes the intersection of its arrivals, like any other merge.
    'M1': ([(CO, "combine = set.union if nodes[id].type == 'join' else set.intersection", "combine = set.intersection")],
           ['test_b01_']),
    # A Join may receive the branches of two forks.
    'M2': ([(CO, "if not 2 <= len(sources) <= 8 or len(feeding) != 1 or None in feeding:",
             "if not 2 <= len(sources) <= 8 or None in feeding:")], ['test_v07_']),
    # Write detection looks at the branch node only, not its attached tools, specialists and loop body.
    'M3': ([(CO, "if any(is_write_node(n, REGISTRY.get(n.type)) for n in attached):",
             "if is_write_node(full[id], REGISTRY.get(full[id].type)):")], ['test_v04_']),
    # The memory refusal checks flow nodes only.
    'M4': ([(CO, "for n in attached if n.type == 'agent' and n.config.get('role') == 'memory']",
             "for n in [full[id]] if n.type == 'agent' and n.config.get('role') == 'memory']")], ['test_v05a_']),
    # Every pair of sibling Agents that share a memory_key is refused.
    'M5': ([(CO, "        if len(ends) != 1 or None in ends:\n",
             "        keys = [full[c].config.get('memory_key', 'default') for chain, _, _ in branches for c in chain if full[c].type == 'agent']\n"
             "        if len(keys) != len(set(keys)): errors.append(f'{fork}: an Agent that writes memory cannot run in a parallel branch.')\n"
             "        if len(ends) != 1 or None in ends:\n")], ['test_v05b_']),
    # The branch cap is 9.
    'M6': ([(CO, "        elif len(links) > 8:\n", "        elif len(links) > 9:\n")], ['test_v06_']),
    # Branches may meet at an ordinary node: the merge counts as their end.
    'M7': ([(CO, "                errors.append(f'{step}: Parallel branches meet only at a Join.')",
             "                ends.discard(None); ends.add(step)")], ['test_v01_']),
    # The Join is compiled as one plain edge per branch.
    'M8': ([(CO, "if nodes_by_id[edge.target].type != 'join': graph.add_edge(node.id, edge.target)", "graph.add_edge(node.id, edge.target)"),
            (CO, "            graph.add_edge(sorted(e.source for e in workflow.edges if e.target == node.id), node.id)", "            pass")],
           ['test_r02_']),
    # No lock: commits run in threads in any order.
    'M9': ([(WO, "            async with accounting.lock:\n                generation,snapshot=accounting.newest()",
             "            if True:\n                generation,snapshot=accounting.newest()")], ['test_a01_action_only_snapshots_commit_in_order[sqlite]']),
    # The lock is kept, but each commit saves its caller's snapshot.
    'M10': ([(WO, "reserve_accounting(id,owner,newest))", "reserve_accounting(id,owner,snapshot))"),
             (WO, "record_accounting(id,owner,newest))", "record_accounting(id,owner,snapshot))")], ['test_a04_']),
    # The lock is released when the waiting branch is cancelled.
    'M11': ([(WO, "                try:return await asyncio.shield(task),cancelled", "                try:return await task,cancelled")], ['test_a03_']),
    # A branch may bind any node earlier in topological order, including a sibling.
    'M12': ([(CO, "            if declared and source in has:\n                continue",
              "            if declared and (source in has or source in order[:order.index(id)]):\n                continue")], ['test_b02_']),
    # A control node is allowed inside a branch.
    'M13': ([(CO, INSIDE, "                if (node.type in ('response', 'calculate')\n"
                          "                        or node.on_error == 'route' or len(outgoing[id]) != 1 and not is_control(definition)):\n")],
            ['test_v03_']),
    # Acknowledge the generation current when the commit returns, not the committed publication's.
    'M14': ([(WO, "if saved is True and generation is not None:durability.acknowledge(generation)",
              "if saved is True and generation is not None:durability.acknowledge(durability.generation)")],
            ['test_a02_spend_and_events_commit_in_order_and_pending_spend_stays_pending[sqlite]']),
    # The newest publication is chosen by spend generation, not publication sequence.
    'M15': ([(AS, "        self.latest=(self.sequence,self.durability.generation,deepcopy(snapshot))",
              "        if self.latest is None or self.durability.generation>self.latest[1]:\n"
              "            self.latest=(self.sequence,self.durability.generation,deepcopy(snapshot))")],
            ['test_a01_action_only_snapshots_commit_in_order[sqlite]']),
    # A branch node may have an incoming edge from outside its region.
    'M16': ([(CO, "while nodes[step].type != 'join' and len(arrivals[step]) == 1 and step not in owner:",
              "while nodes[step].type != 'join' and step not in owner:")], ['test_v01_']),
    # The editor's bindableOutputs intersects at a Join.
    'M17': ([(WT, "new Set(join ? [...a, ...b] : [...a].filter((x) => b.has(x)))", "new Set([...a].filter((x) => b.has(x)))")],
            ['the editor offers the bindings the backend accepts: fork_join']),
}
BAD = re.compile(r'ImportError|ModuleNotFoundError|SyntaxError|NameError|errors? during collection|fixture .* not found')
lines = []; ok = True
for name, (edits, planned) in M.items():
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'a53-{name}-', dir=BASE.parent))
    shutil.copytree(BASE, work / 'repo', ignore=shutil.ignore_patterns('.git')); repo = work / 'repo'
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b))
    if name == 'M17':
        r = subprocess.run(['node', '--experimental-strip-types', '--test', '--test-reporter=tap', 'tests/bindings.test.ts'], cwd=repo / 'frontend',
                           capture_output=True, text=True)
        output = r.stdout + r.stderr
        failed = sorted(set(re.findall(r'^not ok \d+ - (.+)$', output, re.M)))
        summary = ' '.join(re.findall(r'^# (?:pass|fail) \d+$', output, re.M))
        clean = not BAD.search(output) and 'pass' in summary
    else:
        r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', '-p', 'no:logging', '-rf', *TESTS],
                           cwd=repo, capture_output=True, text=True, env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin'})
        failed = sorted(set(re.findall(r'^FAILED \S+::(\S+?)(?: - |\s*$)', r.stdout, re.M)))
        summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
        counts = {k: int(v) for v, k in re.findall(r'(\d+) (passed|failed|skipped)', summary)}
        clean = not BAD.search(r.stdout) and sum(counts.values()) == 32 and 'error' not in summary
    killed = {t: [f for f in failed if f.startswith(t)] for t in planned}
    good = all(killed.values()) and clean; ok &= good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned tests {planned} | {summary}"
                 f"{'' if clean else ' | REJECTED: import/collection/fixture error'}\n  failed: {', '.join(failed)}")
    print(lines[-1], flush=True)
    shutil.rmtree(work)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n"); print(f'ALL PLANNED KILLS: {ok}')
