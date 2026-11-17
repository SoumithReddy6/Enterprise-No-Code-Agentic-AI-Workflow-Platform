# W06 — Database-clock leases and heartbeat

| | |
|---|---|
| Order | 7 of 29 (Phase 1) |
| Closes | C1, C2, part of C4, DEF-09 |
| Depends on | W05 |
| Effort | S–M |
| Spec | `docs/specs/concurrency.md` §2.1, §2.2; change-list commits 1–4. |

## Goal

Lease time comes only from the database clock; the heartbeat is one statement at lease/3; every exit path of the supervisor loop stops the graph task (DEF-09).

## Files to touch

- `backend/app/storage.py:669-701`
- `backend/app/readiness.py:25-30`
- `backend/app/worker.py:242-274`
- `backend/app/main.py:176-184`
- `backend/tests/test_lease_clock.py`, `test_worker_supervision.py` (new)
- `backend/tests/defects/test_def09_heartbeat_error_orphans_task.py` (remove `xfail`, move)

## Tests to write first

Spec §6.1 (5 tests) and §6.3 (`test_a_heartbeat_error_cancels_the_graph_task`, `test_heartbeat_interval_is_a_third_of_the_lease`). Mutants M01–M04, M11.

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

Named rows: `postgres-stale-lease` (5 refusals), S15, S16, S24, S28 (recovery time equals the lease).

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

The risk is I1: the fence keeps its shape; only its clock expression changes.

## Done when

- Spec §10 items for commits 1–4. Heartbeat commits/s for each run before and after, from the load harness, are in `docs/resume-evidence.md`.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W06 (database-clock leases and heartbeat).
Read docs/tasks/07-W06-database-clock-leases-and-heartbeat.md first, then every file it links.
Do only commits 1 to 4 of the spec change list. Rerun the load harness cells w3 and w20 at 4 and 16 workers before and after.

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
