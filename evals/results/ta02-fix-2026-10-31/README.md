# TA02 fix evidence: reusable work and its accounting commit together

Plan: `docs/superpowers/plans/2026-10-31-token-accounting-durability.md` (with the four final
edits). Design: `docs/superpowers/specs/2026-10-31-token-accounting-durability-design.md`, rev 2.
Baseline application: HEAD `65de9f0`, identical to `f3e0923` under `backend/` and `scripts/`.

## What changed

| Area | Change |
|---|---|
| `backend/app/accounting_state.py` (new) | `AccountingUnavailable(ValueError)`, `zero_snapshot`, `checked_snapshot`, `accounting_trusted`, `AccountingDurability` (in-memory generations), `ensure_accounting_ready`, the shared `restored_tokens` and the six user-visible messages. No storage/approval imports. |
| `storage.py` | `create_run` sets `accounting_contract=1` and the zero snapshot in the creation transaction. `_set_accounting` is the one checked assignment (strict for contract runs, `restored_tokens` for older runs; a lower total raises). `worker_event(..., *, accounting=None)` assigns it in the same fenced transaction as the event, usage rows, loop item and checkpoint; a contract run's publishing event without a snapshot raises. `record_accounting`/`reserve_accounting` return `True` after commit; a lost fence on the fast save returns `False`. Contract runs read accounting from the column only. |
| `approvals.py` | `pause(..., *, accounting=None)` commits the supplied frozen snapshot (deferred key added once) with the frame, approval and lease release. Missing snapshot raises. `has_unstarted_approved_write`. |
| `execution_policy.py` | `AccountingUnavailable` added to `CONTROL_FLOW`. |
| `compiler.py` | Root emitter refreshes the accounting sink before every forward (nested and `Context.emit` included). `durability` carried in run state. `restored_tokens` re-exported. |
| `registry.py` | `account_usage` marks a new generation; `record_spend` re-raises control flow (lease loss); `call_model` awaits `ensure_accounting_ready` before every provider request. |
| `agent_runtime.py` | `finalize_grounded_answer` and the structured-only repair wrapper re-raise `CONTROL_FLOW` before their `ValueError` handling. |
| `worker.py` | Freezes snapshot + generation before each event thread and passes it to `worker_event`; acknowledges only that generation after a true commit; deleted the separate post-event accounting write. Fast save and reservation return and acknowledge their captured generation. Trust is decided at claim; an untrusted run with an unstarted approved write fails before any node. The approval catch passes the frozen snapshot and keeps the `AccountingUnavailable` message. |
| Tests | New `backend/tests/test_accounting_durability.py` (24 cases). Mechanical compatibility: 30 pytest `worker_event` wrappers and one `record_accounting` wrapper forward `**kwargs`; direct-call fixtures supply the run's snapshot. Non-mechanical fixture changes are listed below. |
| Harness | `ModelFixture(prompt_tokens=7, completion_tokens=3)`; milestone worker `--accounting-barrier {before_commit,after_commit}` and `--fail-fast-saves` (scenario marker required); four named rows; the two PostgreSQL rows appended to the existing `postgres-conformance` command. Scenario wrappers in `lab.py` and `request_review_worker.py` forward `**kwargs`. |
| Docs | `docs/operations.md` token-ceiling durability section. |

## Files

| File | Content |
|---|---|
| `baseline-65de9f0.txt`, `baseline-reasons.txt` | Final 24 tests on the unchanged application: 22 failed, 2 passed (the planned C controls). |
| `mutants.py`, `mutants.txt` | M1–M12 on fresh copies of the isolation base; all 12 planned kills. |
| `seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy (JUnit files kept in the scratch area, summarized here). |
| `isolation.txt` | Clean-export proof. |
| `process-rows.json` | The four harness rows (SQLite and throwaway PostgreSQL 17). |
| `production/report.json` | Deployed scenario gate. |

## Baseline (red) classification

Behavior failures (16): T01 (`the saved answer is ahead of its accounting`, accounting `None`),
T02 after-commit, T03 missing (pause succeeds from the database copy), T04 fast and reserve
(stale 10 overwrites 30), T06 (15 saved instead of 30), T07 (item saved with 0 tokens),
T08 failed/recovered (no flush before the repair; failed variant sends call 2), T10 model and
approved-write (model call and approved POST both proceed), T11 ×4 (malformed marked state
runs to success), T12 (`record_spend` swallows `LeaseLost`).

Interface-only failures (6): T03 valid, T04 event, T04 pause, T05 (`accounting=` keyword
absent), T09 ×2 (helper module absent). Per the plan these are not taken as proof of the
defect; mutants M3, M4, M8, M9 and M10 show those tests catch the behavior.

Controls passing on the baseline (2): T02 before-commit, T10 cached-and-tool.

## Deviations and test-defect fixes (recorded, not hidden)

1. **Pre-commit failure injection (T02/T03).** An exception raised from
   `ConnectionEvents.commit` returns the DBAPI connection to the pool with its transaction
   still open (reproduced: the row stayed visible on that connection and SQLite stayed
   locked). That is not a rollback. The pytest cases instead flush every write and fail in
   `SessionEvents.before_commit`, which is a genuine rollback. The process rows still use
   `ConnectionEvents.commit` exactly as planned, as a *hold* barrier followed by SIGKILL.
2. **Task 1 expected "9 passed".** After Task 1 alone, 8 passed: T02 after-commit is a
   Worker-level test that needs Task 2's snapshot handoff. Tasks 1–2 ship together, as the
   plan requires; all 24 pass after Task 2.
3. **T02 old state.** The first draft compared with the snapshot at creation; earlier events
   (input, m0 running) legitimately save the initial action budget. The test now reads the
   state just before the failing commit and asserts tokens 0.
4. **M6 survived the first mutant run for T11.** The storage guard independently refuses a
   malformed stored snapshot on the first event write, so T11 passed without the claim-time
   trust check. Following the R01-I01 lesson (no unproven "redundant" layer), T11 now also
   asserts that no node event is attempted: the refusal happens at claim. M6 then failed all
   four T11 cases. No case was added.
5. **New internal message.** `MISSING` (`This result was not saved because its token
   accounting was not supplied.`) for a contract run's publishing event without a snapshot.
   It indicates a programming error, not a user path; the six planned messages are unchanged.
6. **Non-mechanical fixture changes.** `test_loop_storage.py`: partial snapshots became
   canonical four-field snapshots; the two "legacy document" cases now build a real older
   run (marker removed, column empty), because a contract run correctly ignores a document
   copy; the pause case passes its in-memory snapshot. `test_the_row_and_its_event_commit_together_or_not_at_all`
   now supplies a snapshot, so it still fails for the JSON flush and not for the missing
   snapshot. All original assertions are kept.

## Not claimed

A crash after a provider response but before any durable save still loses that call's
tokens (at most one reported call between saves). Missing provider usage, provider-internal
retries, overlapping nested outcome metrics and parallel settlement stay with TA03, TA05,
TA06 and TA08. The original auditor probe (`token-accounting-review-2026-10-25/probe.py`)
asserts the old defect; it was not run or edited, and T01 is the fix oracle.
