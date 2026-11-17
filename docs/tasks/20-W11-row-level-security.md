# W11 — Row-level security

| | |
|---|---|
| Order | 20 of 29 (Phase 2) |
| Closes | S3 |
| Depends on | W08, W10 |
| Effort | M–L |
| Spec | `docs/specs/database-isolation-rls.md`. |

## Goal

PostgreSQL row-level security on every tenant table, with FORCE, an application role that is not the owner and not a superuser, and tests that connect as that role.

## Files to touch

Spec §5 (ten commits): `scripts/postgres_roles.sql`, `compose.yaml`, `.github/workflows/ci.yml`, `backend/app/storage.py` (`Scope`, `store.session`, listener, 32 call sites), `approvals.py`, `tool_service.py`, `agent_memory.py`, `operator_metrics.py`, `readiness.py`, `auth.py`, `auth_security.py`, `worker.py`, migration `rls_policies`, `backend/app/schema.py` (`RLS_TABLES`, `GLOBAL_TABLES`).

## Tests to write first

Spec §6.1 (L01–L11), §6.2 (C01–C07), §6.3, §6.4, §6.5 (S33). Mutants M01–M08: M01 must be killed by the application matrix **and** leave L02 and L06 passing.

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

Every PostgreSQL test connects as `relay_app` or `relay_worker`; the `app_connection` fixture fails (does not skip) when the role is the owner or a superuser.

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

I1: `set_config` runs in `after_begin`, before the fence. A fenced method with an invisible run raises `LeaseLost`, never returns an empty run.

## Done when

- Spec §9. The load harness is run again after commit 8 and the difference is recorded.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W11 (row-level security).
Read docs/tasks/20-W11-row-level-security.md first, then every file it links.
Check the five PostgreSQL facts in spec section 1 against the PostgreSQL 17 documentation before the first commit. Do commits 1 to 6 (no database change) in one session and 7 to 10 in a second session.

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
