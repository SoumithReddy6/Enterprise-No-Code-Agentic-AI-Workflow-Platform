# TA03 independent implementation review evidence

Reviewed SHA: `dfbadf92374acd6387edde814f3f232db2216584`.
Review date: 2026-11-03, America/New_York. Some execution log timestamps fall on November 4 UTC.
Review: `docs/reviews/ta03-implementation-review-2026-11-03.md`.

| Artifact | Contents |
|---|---|
| `focused-tests.txt` | 179 tests passed, exit 0 |
| `probe_review.py`, `independent-probes.json` | Nine scenarios using synthetic provider responses and temporary databases, including loop/specialist composition and pause commit cancellation |
| `deadline-pause.json` | Two additional real-deadline scenarios, helper and root pause commit, both with exact usage and a named timeout failure |
| `hosted-ci.json` | Five completed successful jobs, exact checkout SHA verified from backend logs, two backend suite summaries |
| `before-sha256.json`, `preserved-inputs.json` | 80 original source, test, design and evidence files retain their initial hashes |

The probe loads the old diagnostic module without running its main and uses only the
current-code scenarios. It writes results into this review directory. The independent
loop scenario creates a fixture run plus a modified run in one disposable database and
claims the unused fixture before executing the modified workflow.

The first helper/root pause probes cancel the graph task directly while an acknowledged
save is held. The extra deadline probes exercise the Worker's real run timeout because
framework-propagated cancellation has different wrapping behavior. Both paths preserve
the required usage totals. Typed refusal probes deliberately inject the existing error
types after paid work and do not send an external write.

Run the focused suite or nine-scenario probe from the repository root:

```bash
.venv/bin/python -m pytest -q backend/tests/test_usage_attribution.py \
  backend/tests/test_accounting_durability.py backend/tests/test_operations.py \
  backend/tests/test_execution_policy.py backend/tests/test_operator_metrics.py \
  backend/tests/test_token_budget.py backend/tests/test_token_budget_recovery.py \
  backend/tests/test_iteration.py backend/tests/test_loop_checkpoint.py
PYTHONPATH=. .venv/bin/python evals/results/ta03-review-dfbadf9/probe_review.py
```

The same probe exposes `paused_commit(directory, target, deadline=True)` for reproducing
the two additional rows, with `target` set to `helper` and `agent`.

Original product files and implementation evidence were preserved. The review adds its
own report, probes, logs and compact results. Existing eight-mutant, isolation, five-seed
and deployed reports were inspected rather than repeated as complete campaigns.
