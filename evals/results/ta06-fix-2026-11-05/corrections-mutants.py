"""TA06-I01/I02 correction mutants M9-M11, run like mutants.py on the corrections isolation base."""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); PY = sys.argv[2]; OUT = sys.argv[3]
NEW = 'backend/tests/test_usage_status.py'
OB, PR = 'backend/app/observability.py', 'backend/app/providers.py'
M = {
    # TA06-I01 reverted: the journal filter drops the status again.
    'M9': ([(OB, ",'approval_kind','usage_status'}", ",'approval_kind'}")], ['test_v01_']),
    # The status passes the filter, but any value is accepted.
    'M10': ([(OB, "    if data.get('usage_status',None) is not None and data['usage_status'] not in USAGE_STATUSES:data.pop('usage_status')\n", "")], ['test_v01_']),
    # TA06-I02 reverted: a scheme rejected before connecting counts as an unanswered call.
    'M11': ([(PR, ", httpx.PoolTimeout, httpx.UnsupportedProtocol)", ", httpx.PoolTimeout)")], ['test_v05_nothing_reached_a_provider[invalid-url]']),
}
BAD = re.compile(r'ImportError|ModuleNotFoundError|SyntaxError|NameError|errors? during collection|fixture .* not found')
lines = []; ok = True
for name, (edits, planned) in M.items():
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'ta06c-{name}-', dir=BASE.parent))
    shutil.copytree(BASE, work / 'repo', ignore=shutil.ignore_patterns('.git')); repo = work / 'repo'
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b))
    r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', '-rf', NEW], cwd=repo,
                       capture_output=True, text=True, env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin'})
    failed = sorted(set(re.findall(r'^FAILED \S+::(\S+?)(?: - |\s*$)', r.stdout, re.M)))
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    counts = {k: int(v) for v, k in re.findall(r'(\d+) (passed|failed)', summary)}
    clean = not BAD.search(r.stdout) and sum(counts.values()) == 15 and 'error' not in summary
    good = all([f for f in failed if f.startswith(t)] for t in planned) and clean; ok &= good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned tests {planned} | {summary}"
                 f"{'' if clean else ' | REJECTED: import/collection/fixture error'}\n  failed: {', '.join(failed)}")
    shutil.rmtree(work)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n"); print(open(OUT).read())
