# W07 — Claim rules, poison runs, graceful stop

| | |
|---|---|
| Order | 8 of 29 (Phase 1) |
| Closes | C3, C5, C6, DEF-04, DEF-05, DEF-07 |
| Depends on | W05, W06 |
| Effort | M |
| Spec | `docs/specs/concurrency.md` §2.3 (cases a–d, without the fair pick), §2.4; change-list commits 5, 6, 8, 9, 10. |

## Goal

The claim counts attempts and stops a poison run, finishes a cancelled run, skips an orphan job, backs off when idle, and the worker stops cleanly on SIGTERM. Compare-and-set is measured against SKIP LOCKED.

## Files to touch

- Migration `<next>_job_attempts` (`attempts`, `tenant_id`, index; create `scheduler_tenants` here too, unused until W15)
- `backend/app/storage.py:112-119,472-479,669-686,866-881`
- `backend/app/approvals.py:167`
- `backend/app/worker.py:276-316`
- `scripts/check_milestone_storage.py`, `scripts/scenarios/milestone_worker.py`
- `backend/tests/test_claim_rules.py` (new); move the DEF-04, DEF-05, DEF-07 tests out of `defects/`

## Tests to write first

Spec §6.2 (first 6 tests and `test_claim_strategy_setting`), §6.3 (`test_sigterm_releases_runs_in_flight`, `test_idle_worker_backs_off_to_two_seconds`), §6.4 (`postgres-poison-run`, `sigterm-release`, extended `postgres-four-workers`). Mutants M05–M07, M10.

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

A poison stop never clears a write marker (I7).

## Done when

- DEF-04, DEF-05, DEF-07 tests pass with no `xfail`.
- Claim p50/p95/p99 for `cas` and `skip_locked` at 4, 16 and 32 workers are saved with the commit hash.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W07 (claim rules, poison runs, graceful stop).
Read docs/tasks/08-W07-claim-rules-poison-runs-graceful-stop.md first, then every file it links.
Leave the fair pick (order by last_served_at and the cap) to card W15. Create the scheduler_tenants table now so that W15 needs no migration.

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
