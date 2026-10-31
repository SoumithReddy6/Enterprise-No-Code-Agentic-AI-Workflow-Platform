"""TA02 mutants M1-M12: each applied to a fresh copy of the isolation base; its named oracle must fail.

A failure from an import, collection or fixture error is not a kill: the oracle tests must
fail by assertion or by the expected exception, and the module must collect all 24 cases.
"""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); PY = sys.argv[2]; OUT = sys.argv[3]
NEW = 'backend/tests/test_accounting_durability.py'
ST, WK, AP, AS = 'backend/app/storage.py', 'backend/app/worker.py', 'backend/app/approvals.py', 'backend/app/accounting_state.py'
CP, EP, AR = 'backend/app/compiler.py', 'backend/app/execution_policy.py', 'backend/app/agent_runtime.py'
M = {
    # Separate checkpoint and accounting commits: the event commit ignores its snapshot.
    'M1': ([(ST, "            if accounting is not None:self._set_accounting(row,accounting)\n"
                 "            elif data.get('accounting_contract') is not None and publishes(event):raise AccountingUnavailable(MISSING)\n", "")],
           ['test_t01_', 'test_t02_outcome_transaction[after_commit]', 'test_t07_']),
    # Cancellation (and every other emission) leaves without refreshing the sink.
    'M2': ([(CP, "        if accounting_sink is not None:accounting_sink.update(accounting_snapshot(run_state))\n        await forward(event)",
                 "        await forward(event)")], ['test_t06_']),
    # Pause rebuilds its snapshot from the older database copy.
    'M3': ([(AP, "        budget=dict(accounting['action_budget'])\n",
                 "        accounting=dict(store._accounting(run) or {})\n        budget=dict(accounting['action_budget'])\n")],
           ['test_t03_pause_snapshot[valid]']),
    # No monotonic guard.
    'M4': ([(ST, "        if incoming['tokens_spent']<previous:raise AccountingUnavailable(STALE)\n", "")],
           ['test_t04_', 'test_t05_']),
    # Pending known spend is ignored before the next provider request.
    'M5': ([(AS, "    if not durability.pending:return\n", "    return\n")], ['test_t08_']),
    # Legacy and malformed state are promoted to trusted.
    'M6': ([(AS, "    marker=(run or {}).get('accounting_contract')\n    if marker is None:return False\n",
                 "    return True\n")], ['test_t10_legacy_trust[model]', 'test_t11_']),
    # No claim-time check of an unstarted approved write on an older run.
    'M7': ([(WK, "            if not durability.trusted and approvals.has_unstarted_approved_write(self.store,id,tenant_id):\n"
                 "                raise AccountingUnavailable(LEGACY_APPROVED_WRITE)\n", "")],
           ['test_t10_legacy_trust[approved_write]']),
    # The durability refusal is an ordinary, recoverable node failure.
    'M8': ([(EP, ",LeaseLost,AccountingUnavailable)", ",LeaseLost)")], ['test_t09_']),
    # The grounded repair wrapper converts the refusal into an answer-contract failure.
    'M9': ([(AR, "    except CONTROL_FLOW:raise  # A refused repair request is not an invalid answer.\n", "")],
           ['test_t09_durability_not_recoverable[grounded_continue]']),
    # The structured-only repair wrapper converts the refusal into a structure failure.
    'M10': ([(AR, "        except token_budget.TokenBudgetExhausted:raise\n        except CONTROL_FLOW:raise\n",
                  "        except token_budget.TokenBudgetExhausted:raise\n")],
            ['test_t09_durability_not_recoverable[structured_route]']),
    # A false persistence acknowledgment is accepted.
    'M11': ([(AS, "    if saved is not True:raise AccountingUnavailable(PENDING)\n", "")],
            ['test_t08_pending_before_repair[failed]']),
    # Acknowledging marks the latest generation saved, not the captured one.
    'M12': ([(AS, "    def acknowledge(self,generation):self.acknowledged=max(self.acknowledged,generation)",
                  "    def acknowledge(self,generation):self.acknowledged=self.generation")], ['test_t06_']),
}
BAD = re.compile(r'ImportError|ModuleNotFoundError|SyntaxError|NameError|errors? during collection|fixture .* not found')
lines = []; ok = True
for name, (edits, planned) in M.items():
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'ta02-{name}-', dir=BASE.parent))
    shutil.copytree(BASE, work / 'repo', ignore=shutil.ignore_patterns('.git')); repo = work / 'repo'
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b))
    r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', '-rf', NEW], cwd=repo,
                       capture_output=True, text=True, env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin'})
    failed = sorted(set(re.findall(r'^FAILED \S+::(\S+?)(?: - |\s*$)', r.stdout, re.M)))
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    collected = re.search(r'(\d+) (?:passed|failed)', summary) is not None and 'error' not in summary
    killed = {t: [f for f in failed if f.startswith(t)] for t in planned}
    clean = not BAD.search(r.stdout) and collected
    good = all(killed.values()) and clean; ok &= good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned tests {planned} | {summary}"
                 f"{'' if clean else ' | REJECTED: import/collection/fixture error'}\n  failed: {', '.join(failed)}")
    shutil.rmtree(work)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n"); print(open(OUT).read())
