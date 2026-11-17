# W18 — Retention and deletion

| | |
|---|---|
| Order | 25 of 29 (Phase 3) |
| Closes | O2 |
| Depends on | W08, W13 |
| Effort | M |
| Spec | `docs/gap-audit.md` W18. |

## Goal

Per-organization retention with a leased purge job; run deletion and organization deletion by cascade.

## Files to touch

- `organizations.retention_days` (NULL = keep), a purge loop in the worker (`worker.py:287-290` pattern, at most once a minute, batch 200 runs, a `pg_try_advisory_lock` so one worker purges at a time)
- `backend/app/storage.py`: `purge_runs(tenant, before, limit)`, `delete_run`, `delete_organization`
- `backend/app/main.py`: `DELETE /api/runs/{id}` (`runs:control`), `DELETE /api/organization` (`org:manage`)
- Session and OIDC-state purge on the same timer

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_purge_removes_exactly_the_finished_runs_older_than_the_limit` | |
| `test_a_run_with_an_unsettled_write_is_never_purged` | I7 |
| `test_an_active_or_waiting_run_is_never_purged` | |
| `test_deleting_an_organization_leaves_no_row_in_any_tenant_table` | The table list comes from `schema.RLS_TABLES`, so a new table cannot be forgotten |
| `test_the_audit_log_records_the_deletion_and_survives_a_run_delete` | |
| `test_one_orphan_job_cannot_exist_after_a_run_delete` | The foreign key of W08 (DEF-07 class) |
| `test_purge_is_batched_and_does_not_hold_one_long_transaction` | |

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

I7: never purge an unsettled write. Purge and delete take the job-row lock first, like every API writer.

## Done when

- Rows and bytes removed for each batch and the purge time for 10,000 runs are measured.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W18 (retention and deletion).
Read docs/tasks/25-W18-retention-and-deletion.md first, then every file it links.
Do not add table partitioning; note it as the next step.

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
