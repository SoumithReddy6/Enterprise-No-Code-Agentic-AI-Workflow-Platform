"""A53-I01 mutants M18-M20: each applied to a fresh copy of the isolation base; its named oracle must fail.

A failure from an import, collection or fixture error is not a kill. The module must collect
all 10 cases (the 3 PostgreSQL parameters skip here).
Usage: python mutants.py <file holding the isolation path> <python> <output file>
"""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); PY = sys.argv[2]; OUT = sys.argv[3]
TEST, AS = 'backend/tests/test_parallel_accounting.py', 'backend/app/accounting_state.py'
M = {
    # One save and return, as before the fix: newer spend that arrived during it is not rechecked.
    'M18': ([(AS, "    while durability.pending:\n", "    if durability.pending:\n")], ['test_a06a_', 'test_a06b_']),
    # A failed follow-up save lets the model call proceed instead of refusing it.
    'M19': ([(AS, "        except Exception:raise AccountingUnavailable(PENDING) from None\n        if saved is not True:raise AccountingUnavailable(PENDING)\n        durability.acknowledge(generation)",
              "        except Exception:return\n        if saved is not True:raise AccountingUnavailable(PENDING)\n        durability.acknowledge(generation)")], ['test_a06b_']),
    # The loop acknowledges the generation current after the save, not the one it saved.
    'M20': ([(AS, "        if saved is not True:raise AccountingUnavailable(PENDING)\n        durability.acknowledge(generation)",
              "        if saved is not True:raise AccountingUnavailable(PENDING)\n        durability.acknowledge(durability.generation)")], ['test_a06a_']),
}
BAD = re.compile(r'ImportError|ModuleNotFoundError|SyntaxError|NameError|errors? during collection|fixture .* not found')
lines = []; ok = True
for name, (edits, planned) in M.items():
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'a53-{name}-', dir=BASE.parent))
    shutil.copytree(BASE, work / 'repo', ignore=shutil.ignore_patterns('.git')); repo = work / 'repo'
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b))
    r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', '-p', 'no:logging', '-rf', TEST],
                       cwd=repo, capture_output=True, text=True, env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin'})
    failed = sorted(set(re.findall(r'^FAILED \S+::(\S+?)(?: - |\s*$)', r.stdout, re.M)))
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    counts = {k: int(v) for v, k in re.findall(r'(\d+) (passed|failed|skipped)', summary)}
    clean = not BAD.search(r.stdout) and sum(counts.values()) == 10 and 'error' not in summary
    killed = {t: [f for f in failed if f.startswith(t)] for t in planned}
    good = all(killed.values()) and clean; ok &= good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned tests {planned} | {summary}"
                 f"{'' if clean else ' | REJECTED: import/collection/fixture error'}\n  failed: {', '.join(failed)}")
    print(lines[-1], flush=True)
    shutil.rmtree(work)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n"); print(f'ALL PLANNED KILLS: {ok}')
