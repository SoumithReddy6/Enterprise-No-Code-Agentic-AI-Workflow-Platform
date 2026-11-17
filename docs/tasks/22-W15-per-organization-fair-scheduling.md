# W15 — Per-organization fair scheduling

| | |
|---|---|
| Order | 22 of 29 (Phase 2) |
| Closes | C7, S10 |
| Depends on | W07, W09 |
| Effort | M |
| Spec | `docs/specs/concurrency.md` §2.3 step 1 (the pick) and case d; change-list commit 7; checks `postgres-fair-queue`, scenario S36. |

## Goal

The claim serves the least recently served organization first and respects a concurrency cap for each organization; submission has a queued-run limit.

## Files to touch

- `backend/app/storage.py:669-686` (the pick query; `last_served_at` update)
- `backend/app/main.py:155-161` (429 when the organization has more than `RELAY_TENANT_MAX_QUEUED_RUNS`, default 1,000, queued runs)
- `scripts/check_milestone_storage.py`, `scripts/scenarios/`

## Tests to write first

Spec §6.2: `test_claim_serves_the_least_recently_served_tenant_first`, `test_claim_respects_the_tenant_concurrency_cap`; §6.4 `postgres-fair-queue`; plus `test_submission_over_the_queue_limit_returns_429`. Mutants M08, M09.

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

The running count is computed in the query, never kept in a counter.

## Done when

- Organization B's p95 queue wait with and without the fair pick, under a 1,000-run backlog of organization A, is measured with the load harness and recorded.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W15 (per-organization fair scheduling).
Read docs/tasks/22-W15-per-organization-fair-scheduling.md first, then every file it links.
The table scheduler_tenants exists after card W07. Add no migration unless the cap setting needs one.

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
