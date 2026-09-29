# Loop storage and budget audit — 2026-09-29

Verdict: the A–C redesign and action-budget loop behavior are structurally sound in the audited SQLite/single-worker scope. No replay, fencing, atomicity, migration-head, or campaign-provenance defect was found. One non-blocking storage-contract mismatch remains: `MAX_RESULT_BYTES` bounds retained values, not the complete serialized result when mandatory metadata alone crosses the threshold.

Audited commit: `4b1cb9438b3d17ee7536180e8e139e2538399aab`. No production code was changed during this audit.

## Verified guarantees

- A loop item row and its `loop_item` event commit in one fenced transaction. A failed flush or lost lease preserves neither.
- Rows are keyed by `(run_id, node_id, item_index)` and repeated progress updates the same item. Table progress wins over legacy document progress, including write-settlement and resume readers.
- Accounting uses `runs.accounting`, falls back to legacy document accounting, and approval deferral writes to the same authority used by resume.
- Fresh and restored entries use the same value-admission, failure-count, size, stop, and budget-exhaustion path.
- Event previews do not alter the durable row or loop checkpoint. Approval payloads and write outcomes remain whole.
- Budget refusal before an external call produces `not_run`, stops the loop, and marks it incomplete without inventing an item failure. A real failed attempt whose retry is refused remains a failure.
- Revision 0003 upgrades from 0002, preserves legacy JSON progress by dual read, has model/schema parity, and supports the repository's documented structural downgrade test.
- The performance method clearly measures bound SQL parameter bytes on SQLite rather than disk/WAL amplification. Its before/after figures and limitations are recorded in the evidence directory.

## L01 — P3: the named whole-result limit is not a hard whole-result limit

Location: `backend/app/iteration.py`, `ResultBudget`.

Values are dropped once adding a value-bearing entry would cross `MAX_RESULT_BYTES`. Entries whose value is already empty—failed outcomes—and mandatory metadata retained after dropping are always admitted. Therefore `budget.size` and the actual serialized results can exceed the limit.

Targeted proof with a 300-byte limit: three failed entries with 180-byte errors serialize to 720 bytes. With the production 1,000,000-byte limit, 1,000 success entries carrying the grounded contract's 1,000-character reason serialize to 1,090,694 bytes, 90,694 bytes over the nominal bound; 82 values were dropped. This is not the former unbounded 64 MB progress problem, and current node/action limits reduce typical exposure, but the class docstring and commit statement that the complete serialized list fits 1 MB are stronger than the implementation.

Choose one explicit contract:

1. Rename/document this as a **retained-value budget**, with bounded outcome metadata allowed in addition; or
2. Enforce a strict serialized-output cap by bounding error/truncation metadata and defining a summary representation once even metadata cannot fit.

Do not drop status/index information merely to meet the number. This finding does not block the readiness-test work.

## Downgrade probe — expected behavior, not a defect

A targeted probe showed that downgrading 0003 to 0002 discards table-only loop progress and column accounting. This matches `docs/operations.md`: downgrades are tested structurally but are explicitly not promised lossless; populated rollback requires restoring a compatible backup. The probe is retained as evidence but is not filed as a product bug.

## Verification evidence

- Full working-tree backend suite: **688 passed**, exit 0, four dependency deprecation warnings; `backend.xml` retained.
- Exact `git archive HEAD` checkout, focused loop storage/completeness/schema suites: **76 passed**, exit 0; `committed-focused.xml` retained.
- New boundary probes: two expected failures—one demonstrates L01, one demonstrates documented lossy downgrade behavior; `focused.xml` retained.
- Saved production report: **24/24 stages passed**, no source files changed during execution, no non-passing rows. Every recorded source SHA-256 still matches the current audited files. The audit did not rerun Ollama/live services.
- `git diff --check`: passed.

Artifacts: `evals/results/loop-storage-audit-2026-09-29/`. Existing performance evidence: `evals/results/loop-storage-2026-09-29/`.

## Scope limits

The storage measurements and atomicity probes use SQLite and one active worker. They do not establish PostgreSQL query plans, WAL volume, two-worker contention, crash durability below the database transaction boundary, or long-term retention/cleanup. `run_loop_items` currently has no cleanup path because Relay has no run-deletion feature; add cascade/retention behavior when run deletion is introduced.

Proceed to the readiness flake. Track L01 as an explicit contract/documentation decision, then include PostgreSQL and concurrent-worker storage checks in the milestone audit.
