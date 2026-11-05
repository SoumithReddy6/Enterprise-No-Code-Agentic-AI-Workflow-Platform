"""TA06 mutants M1-M8: each applied to a fresh copy of the isolation base; its named oracle must fail.

A failure from an import, collection or fixture error is not a kill: the oracle tests must
fail by assertion, and the module must collect all 15 backend cases.
"""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); PY = sys.argv[2]; OUT = sys.argv[3]
NEW = 'backend/tests/test_usage_status.py'
PR, RG, OM = 'backend/app/providers.py', 'backend/app/registry.py', 'backend/app/operator_metrics.py'
OLLAMA_SEND = "            response = await _send(http, usage, http.build_request(\n                'POST', ollama_url() + '/api/chat',"
M = {
    # The call vanishes again when no count was recorded.
    'M1': ([(RG, "    if ctx.run is None or status is None:return", "    if ctx.run is None or status is None or not usage:return")],
           ['test_v01_', 'test_v02_missing_counts_are_marked_not_zeroed[openai-null]', 'test_v06_']),
    # A response with one count missing is recorded as reported.
    'M2': ([(PR, "{2: 'reported', 1: 'partial'}", "{2: 'reported', 1: 'reported'}")],
           ['test_v02_missing_counts_are_marked_not_zeroed[ollama-partial]', 'test_v02_missing_counts_are_marked_not_zeroed[claude-partial]']),
    # A reported zero is treated as a missing count.
    'M3': ([(PR, "        if type(value) is int and value >= 0:\n            usage[key] = usage.get(key, 0) + value\n            valid += 1",
                 "        if type(value) is int and value > 0:\n            usage[key] = usage.get(key, 0) + value\n            valid += 1")],
           ['test_v03_read_timeouts[then-zero]']),
    # A dispatched call with no response is not counted.
    'M4': ([(PR, "        return 'unanswered' if self.dispatched > self.undelivered else None", "        return None")],
           ['test_v03_read_timeouts[exhausted]', 'test_v04_']),
    # A call whose earlier HTTP attempt timed out is classified by that attempt, not by its response.
    'M5': ([(PR, "        if self.received:\n", "        if self.received and self.dispatched == 1:\n")],
           ['test_v03_read_timeouts[then-zero]']),
    # Dispatch is recorded before request preparation (the TA06-D01 counterexample).
    'M6': ([(PR, OLLAMA_SEND, "            if isinstance(usage, CallUsage): usage.dispatched += 1\n" + OLLAMA_SEND)],
           ['test_v05_nothing_reached_a_provider[invalid-url]']),
    # Receipt is recorded only once usage is parsed (the TA06-D02 counterexample).
    'M7': ([(PR, "    # Non-streaming: the body is read. Status, JSON and usage checks come after receipt.\n    if managed:\n        usage.received = True\n", "")],
           ['test_v06_']),
    # Metric rows drop the new counters.
    'M8': ([(OM, "        for field in USAGE_FIELDS:", "        for field in USAGE_FIELDS[:3]:")],
           ['test_v01_', 'test_v02_', 'test_v06_']),
}
BAD = re.compile(r'ImportError|ModuleNotFoundError|SyntaxError|NameError|errors? during collection|fixture .* not found')
lines = []; ok = True
for name, (edits, planned) in M.items():
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'ta06-{name}-', dir=BASE.parent))
    shutil.copytree(BASE, work / 'repo', ignore=shutil.ignore_patterns('.git')); repo = work / 'repo'
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b))
    r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', '-rf', NEW], cwd=repo,
                       capture_output=True, text=True, env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin'})
    failed = sorted(set(re.findall(r'^FAILED \S+::(\S+?)(?: - |\s*$)', r.stdout, re.M)))
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    counts = {k: int(v) for v, k in re.findall(r'(\d+) (passed|failed)', summary)}
    collected = sum(counts.values()) == 15 and 'error' not in summary
    killed = {t: [f for f in failed if f.startswith(t)] for t in planned}
    clean = not BAD.search(r.stdout) and collected
    good = all(killed.values()) and clean; ok &= good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned tests {planned} | {summary}"
                 f"{'' if clean else ' | REJECTED: import/collection/fixture error'}\n  failed: {', '.join(failed)}")
    shutil.rmtree(work)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n"); print(open(OUT).read())
