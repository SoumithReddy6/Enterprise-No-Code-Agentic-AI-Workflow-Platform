"""TA03 mutants M1-M8: each applied to a fresh copy of the isolation base; its named oracle must fail.

A failure from an import, collection or fixture error is not a kill: the oracle tests must
fail by assertion, and the module must collect all 13 cases.
"""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); PY = sys.argv[2]; OUT = sys.argv[3]
NEW = 'backend/tests/test_usage_attribution.py'
CP, AR, IT, EP = 'backend/app/compiler.py', 'backend/app/agent_runtime.py', 'backend/app/iteration.py', 'backend/app/execution_policy.py'
INCLUSIVE = "**({'usage':run_state['usage'][node.id]} if run_state.get('usage',{}).get(node.id) else {})"
M = {
    # The parent's cancellation reports the checkpoint owner's inclusive total again.
    'M1': ([(CP, "await emit(with_own_usage({'node_id': node.id, 'status': 'cancelled'},run_state,identity))",
                 "await emit({'node_id': node.id, 'status': 'cancelled'," + INCLUSIVE + "})")],
           ['test_u01_', 'test_u02_']),
    # An active specialist unwinds without reporting its own usage.
    'M2': ([(AR, "            if not outcome_saved(exc,identity):\n"
                 "                await emit(with_own_usage({**common,'status':'cancelled'},ctx.run,identity))\n", "")],
           ['test_u02_']),
    # An active loop item unwinds without reporting its own usage.
    'M3': ([(IT, "        if not outcome_saved(exc,identity):\n"
                 "            await item_emit(with_own_usage({**common,'status':'cancelled'},ctx.run,identity))\n", "")],
           ['test_u04_']),
    # Unsafe, uncertain and accounting failures report the inclusive total.
    'M4': ([(CP, "failure=with_own_usage({'node_id':node.id,'status':'failed','error':error},run_state,identity)",
                 "failure={'node_id':node.id,'status':'failed','error':error," + INCLUSIVE + "}")],
           ['test_u08_refusal_after_paid_work[unsafe]', 'test_u08_refusal_after_paid_work[uncertain]']),
    # A child whose outcome already committed reports it again as cancelled.
    'M5': ([(AR, "            if not outcome_saved(exc,identity):\n                await emit(with_own_usage({**common,'status':'cancelled'}",
                 "            if True:\n                await emit(with_own_usage({**common,'status':'cancelled'}")],
           ['test_u05_cancellation_while_an_outcome_commit_is_acknowledged[child]']),
    # Any saved outcome below the parent suppresses the parent's own report.
    'M6': ([(CP, "                      if not outcome_saved(exc,identity):\n",
                 "                      if not isinstance(exc,PersistedNodeCancellation):\n")],
           ['test_u05_cancellation_while_an_outcome_commit_is_acknowledged[child]']),
    # An invocation with no usage of its own inherits its checkpoint owner's total.
    'M7': ([(EP, "    usage=usage_for(run,identity)\n    return {**event,'usage':usage} if usage else event",
                 "    usage=usage_for(run,identity) or deepcopy((run or {}).get('usage',{}).get(identity.checkpoint_owner))\n"
                 "    return {**event,'usage':usage} if usage else event")],
           ['test_u03_child_cancelled_before_reporting_usage[agent]', 'test_u03_child_cancelled_before_reporting_usage[tool]']),
    # A resumed attempt starts from the saved per-invocation usage, so replayed work is reported again.
    'M8': ([(CP, "               'usage_by_invocation':{},\n",
                 "               'usage_by_invocation':dict((accounting or {}).get('usage_by_invocation') or {}),\n")],
           ['test_u07_', 'test_u09_']),
}
BAD = re.compile(r'ImportError|ModuleNotFoundError|SyntaxError|NameError|errors? during collection|fixture .* not found')
lines = []; ok = True
for name, (edits, planned) in M.items():
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'ta03-{name}-', dir=BASE.parent))
    shutil.copytree(BASE, work / 'repo', ignore=shutil.ignore_patterns('.git')); repo = work / 'repo'
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b))
    r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', '-rf', NEW], cwd=repo,
                       capture_output=True, text=True, env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin'})
    failed = sorted(set(re.findall(r'^FAILED \S+::(\S+?)(?: - |\s*$)', r.stdout, re.M)))
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    counts = {k: int(v) for v, k in re.findall(r'(\d+) (passed|failed)', summary)}
    collected = sum(counts.values()) == 13 and 'error' not in summary
    killed = {t: [f for f in failed if f.startswith(t)] for t in planned}
    clean = not BAD.search(r.stdout) and collected
    good = all(killed.values()) and clean; ok &= good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned tests {planned} | {summary}"
                 f"{'' if clean else ' | REJECTED: import/collection/fixture error'}\n  failed: {', '.join(failed)}")
    shutil.rmtree(work)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n"); print(open(OUT).read())
