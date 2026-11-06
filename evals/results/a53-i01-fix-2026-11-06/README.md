# A53-I01 correction evidence: the save barrier rechecks pending spend after every save

Finding: `docs/reviews/a53-part1-review-2026-11-06.md`, A53-I01 [P1], with the reviewer's
reproduction in `evals/results/a53-part1-review-2ad952a/` (not changed). Reviewed commit and
baseline: `2ad952a` (A5.3 Part 1). No design round, as the review states.

## The defect

`ensure_accounting_ready` runs before every model request. It saved the pending spend once,
acknowledged the generation it captured and returned. With parallel branches, another branch
can report usage while that save is in flight. The newer generation correctly stayed pending
(M14 in Part 1 proves that), but the barrier returned anyway, so a further model call (an
Agent's structured repair) started while known spend was not durable. Before A5.3 only one
call ran at a time, so this was not reachable.

## What changed

| Area | Change |
|---|---|
| `accounting_state.py` | `ensure_accounting_ready` repeats capture → strict save → acknowledge while `durability.pending` is true. A failed or refused save still raises `AccountingUnavailable(PENDING)`; cancellation and lease loss (`CONTROL_FLOW`) still propagate; only the captured, committed generation is acknowledged. |
| Tests | `test_parallel_accounting.py`: A06a and A06b (below). No existing test changed. |
| Docs | `docs/operations.md` **Parallel branches → Budgets**: the save before a model call covers spend another branch reports during it. `README.md`: counts. |

## Tests

Both use the reviewer's schedule with the real Worker, Store, policy and Agent repair; only
the provider responses and Store timing/failures are controlled. A's first answer (5 tokens)
needs a repair, and its fast save fails (a TA02 outage), so the barrier before the repair
must save it. That strict save is held while B reports 5 more (generation 2). Then A's save is
released, and the next save carrying 10 tokens is held.

| Case | Oracle |
|---|---|
| A06a | While the newer save is held, the repair is not dispatched (bounded wait that must expire). At dispatch: `pending` false, generation 2, acknowledged 2, stored tokens 10. Final 15, run success. |
| A06b | The newer saves fail. The repair is never dispatched (A called once); the run fails with the `PENDING` message. |

## Files

| File | Content |
|---|---|
| `baseline-2ad952a.txt` | A06a and A06b on a clean archive of `2ad952a`: both fail by behavior (A06a: the repair was dispatched while the newer save was held; A06b: the repair was dispatched after the refused save, A called twice). |
| `isolate.py`, `isolation.txt` | Clean-export proof: git archive `2ad952a` + the 2 changed code/test files. `README.md` and `docs/operations.md` were edited after the copy was built; no test reads them. |
| `mutants.py`, `mutants.txt` | M18–M20 on fresh copies of the isolation base; all planned kills. |
| `seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy, PostgreSQL cases included. |
| `process-rows.json` | Storage-script rows: the TA02 accounting rows and D01 on SQLite and PostgreSQL, plus the CI postgres-conformance list (throwaway PostgreSQL 17 on 127.0.0.1:55433). |
| `production/report.json` | Deployed scenario gate. |

## Mutants

| Mutant | Planned oracle | Also failed |
|---|---|---|
| M18 one save and return (the `2ad952a` behavior) | A06a, A06b | |
| M19 a failed follow-up save lets the call proceed | A06b | |
| M20 the loop acknowledges the current generation, not the saved one | A06a | A06b |

## Gate results

| Gate | Result |
|---|---|
| A06a, A06b on `2ad952a` | 2 failed by behavior |
| Mutants M18–M20 | all planned kills; 10 cases collected each time |
| Full backend suite, PYTHONHASHSEED 0–4, isolation copy `a4d65fe` | 2,049 passed at every seed (2,047 + 2), PostgreSQL included |
| Storage-script rows | 10 of 10: TA02 accounting before/after commit and D01 on SQLite and PostgreSQL, plus migrations, four workers, stale lease and the SIGKILL loop; no source file changed during the run |
| Deployed scenario gate | 24 of 24 rows passed; `source_files_changed` empty |
| Frontend | not rerun: no frontend file changed |
| Hosted CI | to be recorded on the correction commit |
