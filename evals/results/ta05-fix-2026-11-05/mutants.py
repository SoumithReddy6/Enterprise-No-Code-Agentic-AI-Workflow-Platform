"""TA05 mutants M1-M9: each applied to a fresh copy of the isolation base; its named oracle must fail.

A failure from an import, collection or fixture error is not a kill: the oracle tests must
fail by assertion, and the module must collect all 9 cases.
Usage: python mutants.py <file holding the isolation path> <python> <output file>
"""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); PY = sys.argv[2]; OUT = sys.argv[3]
NEW = 'backend/tests/test_token_admission_limit.py'
ST, TB, AP = 'backend/app/storage.py', 'backend/app/token_budget.py', 'backend/app/approvals.py'
FINISH = "            row.data={**row.data,**updates,'status':status,'finished_at':now()};row.status=status\n            Store._settle_token_limit(row)\n"
CANCEL = "                job.status='cancelled';row.status='cancelled';row.data={**row.data,'status':'cancelled','finished_at':now()}\n                Store._settle_token_limit(row)\n"
CLOSE = "    store._settle_token_limit(run)\n"
TRY = "        try:\n            stored=Store._accounting(row)\n            spent=checked_snapshot(stored)['tokens_spent'] if row.data.get('accounting_contract') is not None else restored_tokens(stored)\n            record=overshoot(spent,run_token_limit())\n"
RESUME = "row.data={**{k:v for k,v in row.data.items() if k!='token_limit'},'status':'queued',"
KEEP = "        row.data={**data,**({'token_limit':record} if record else {})}"
M = {
    # The record is written only when the worker finishes with success.
    'M1': ([(ST, FINISH, FINISH.replace("            Store._settle_token_limit(row)\n", "            if status=='success':Store._settle_token_limit(row)\n"))],
           ['test_l02_', 'test_l06_']),
    # A run that ends exactly at the limit gets a record.
    'M2': ([(TB, "spent<=limit:return None", "spent<limit:return None")], ['test_l03_']),
    # An unlimited run (limit 0) gets a record.
    'M3': ([(TB, "    if not limit or type(spent)", "    if limit is None or type(spent)")], ['test_l04_no_overshoot_records_nothing[unlimited]']),
    # The limit is read outside the guard, so an invalid setting makes finish_run raise.
    'M4': ([(ST, TRY, "        limit=run_token_limit()\n" + TRY.replace("run_token_limit())", "limit)"))], ['test_l05_']),
    # over is reported as the whole spend.
    'M5': ([(TB, "'over':spent-limit}", "'over':spent}")], ['test_l01_']),
    # Spend is read from the run data instead of the durable accounting column.
    'M6': ([(ST, "            stored=Store._accounting(row)\n", "            stored=row.data.get('accounting')\n")], ['test_l01_']),
    # request_cancel finishes a queued run without the record (TA05-D01).
    'M7': ([(ST, CANCEL, CANCEL.replace("                Store._settle_token_limit(row)\n", ""))], ['test_l07_']),
    # approvals._close finishes a rejected run without the record (TA05-D01).
    'M8': ([(AP, CLOSE, "")], ['test_l08_']),
    # An earlier attempt's record is kept: neither resume nor the terminal helper removes it (TA05-D02).
    'M9': ([(ST, RESUME, "row.data={**row.data,'status':'queued',"),
            (ST, KEEP, KEEP.replace("{**data,", "{**row.data,"))], ['test_l02_']),
}
BAD = re.compile(r'ImportError|ModuleNotFoundError|SyntaxError|NameError|errors? during collection|fixture .* not found')
lines = []; ok = True
for name, (edits, planned) in M.items():
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'ta05-{name}-', dir=BASE.parent))
    shutil.copytree(BASE, work / 'repo', ignore=shutil.ignore_patterns('.git')); repo = work / 'repo'
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b))
    r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', '-rf', NEW], cwd=repo,
                       capture_output=True, text=True, env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin'})
    failed = sorted(set(re.findall(r'^FAILED \S+::(\S+?)(?: - |\s*$)', r.stdout, re.M)))
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    counts = {k: int(v) for v, k in re.findall(r'(\d+) (passed|failed)', summary)}
    collected = sum(counts.values()) == 9 and 'error' not in summary
    killed = {t: [f for f in failed if f.startswith(t)] for t in planned}
    clean = not BAD.search(r.stdout) and collected
    good = all(killed.values()) and clean; ok &= good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned tests {planned} | {summary}"
                 f"{'' if clean else ' | REJECTED: import/collection/fixture error'}\n  failed: {', '.join(failed)}")
    shutil.rmtree(work)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n"); print(open(OUT).read())
