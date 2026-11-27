# W08 — Data integrity

| | |
|---|---|
| Order | 12 of 29 (Phase 2) |
| Closes | D1, D2, D5 |
| Depends on | W07 |
| Effort | M |
| Spec | `docs/gap-audit.md` W08. |

## Goal

Every run child table has `tenant_id`, a composite foreign key to its run, and state columns have CHECK constraints.

## Files to touch

- Migration `<next>_child_tenant_and_keys`: `tenant_id` on `run_loop_items`, `run_action_results`, `run_token_usage`, `run_guard_decisions`, `run_admission_metrics` (backfill from `runs`; `execution_jobs.tenant_id` exists after W07); `UNIQUE (id, tenant_id)` on `runs`; `FOREIGN KEY (run_id, tenant_id) REFERENCES runs (id, tenant_id) ON DELETE CASCADE` on those tables and on `run_events`, `run_approvals`, `execution_jobs` (PostgreSQL: `NOT VALID`, then `VALIDATE CONSTRAINT`); `CHECK` on `runs.status`, `execution_jobs.status` (queued, running, awaiting_approval, success, failed, cancelled), `run_approvals.status` (pending, approved, executing, completed, rejected, expired, cancelled)
- `backend/app/storage.py:54-67,99-119,531-548,730-734,840-841`; `backend/app/operator_metrics.py:9-50` (models and every insert set `tenant_id`)
- Do **not** touch `write_nodes`, `mark_write`, `settle_write`, `check_resume_writes` (card W24)

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_every_run_child_row_carries_its_runs_tenant` | After one full run with a loop, an approval and usage: each child table's rows have the run's `tenant_id` |
| `test_a_child_row_for_another_tenant_is_refused` | Parametrized over the child tables: an insert with a wrong `tenant_id` raises an integrity error (PostgreSQL; SQLite with `PRAGMA foreign_keys=ON` in the test) |
| `test_an_unknown_status_is_refused` | `UPDATE runs SET status='done'` raises |
| `test_deleting_a_run_removes_its_children` | Owner-level `DELETE FROM runs` leaves 0 rows in each child table |
| `test_every_status_the_code_writes_is_allowed` | An `ast` scan of string literals assigned to `.status` in `storage.py` and `approvals.py` is a subset of the CHECK lists |
| `populated-downgrade` (extend) | The chain up, down and up keeps row counts |

## Commands to verify

```bash
# 1. New tests fail on the parent commit (show the output), then pass.
# 2. Full backend suite under five hash seeds:
for s in 0 1 2 3 4; do PYTHONHASHSEED=$s .venv/bin/python -m pytest backend/tests -q || break; done
# 3. Deployed scenario gate (about 5 minutes, local only):
.venv/bin/python -u -m scripts.check_production_scenarios --all --out <scratch>
# 4. Frontend, when a frontend or API contract file changed:
(cd frontend && npm test && npm run typecheck && npm run lint && npm run build)
```
```bash
# PostgreSQL checks. Use a throwaway container, never the user's database. Ask first: more than 5 minutes.
.venv/bin/python -u -m scripts.check_milestone_storage --out <scratch> --checks \
  postgres-migrations postgres-four-workers postgres-stale-lease postgres-sigkill-loop \
  postgres-accounting-before-commit postgres-accounting-after-commit postgres-parallel-sigkill \
  postgres-repeat-sigkill populated-downgrade
```

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

No behavior change. SQLite does not enforce foreign keys unless the pragma is on: decide nothing new; keep the engine setting as it is and enforce in PostgreSQL.

## Done when

- Count of foreign keys and CHECK constraints before and after is recorded; migration time on a database with 10,000 runs is measured.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W08 (data integrity).
Read docs/tasks/12-W08-data-integrity.md first, then every file it links.
This card adds columns and constraints only. The write-intent markers are card W24 and must not change here.

Rules for this session:
- Read the card, the linked spec and the cited code first. Do not make design decisions that the spec already made. If the spec and the code disagree, stop and ask.
- Write the tests first. Show that each fails on the parent commit for the reason the card states.
- Never weaken an existing invariant (I1-I8 in docs/gap-audit.md section 6) or an existing test to make a new test pass. If they conflict, stop and ask.
- Run mutants only in scratch copies, with an M00 no-change control and a guard that fails when no test ran.
- Ask before any run longer than 5 minutes, any network access, or any API key use. Use a throwaway PostgreSQL container, never the user's database or .data directory. Never use git stash.
- Give exact git add and git commit commands; no Co-Authored-By line; the user commits.
- After the gate passes: update the status table in docs/gap-audit.md section 11, add only verified numbers to docs/resume-evidence.md, and remove the xfail marker of a defect test only in the commit that fixes it.
- Write replies in about 80% Simplified Technical English.
```
