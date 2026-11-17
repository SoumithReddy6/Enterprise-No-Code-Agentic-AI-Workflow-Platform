# W10 — Permissions on every route

| | |
|---|---|
| Order | 16 of 29 (Phase 2) |
| Closes | S1, S3 (application level) |
| Depends on | W09 (PR 1), W02 |
| Effort | M–L |
| Spec | `docs/specs/multi-tenancy-core.md` §2.4, §2.5; pull request 2. |

## Goal

Every route declares one permission; a generated role × route × organization matrix proves it; a route with no rule fails CI.

## Files to touch

Spec §5 PR 2 (steps 6–8): `backend/app/auth.py`, `main.py`, `tool_api.py`, `kb_gateway.py`, `backend/tests/authz_matrix.json`, `docs/permissions.md` (generated).

## Tests to write first

Spec §6.1 (`test_authz_routes.py`, 3 tests) and §6.2 (`test_authz_matrix.py`, 4 tests). Mutants M01, M02, M11.

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

A resource of another organization answers 404, never 403.

## Done when

- `grep -rn "Depends(tenant)\|auth.tenant\|auth.operator" backend/app` returns nothing. The matrix cell count is recorded in `docs/resume-evidence.md`.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W10 (permissions on every route).
Read docs/tasks/16-W10-permissions-on-every-route.md first, then every file it links.
The route map in the spec is final. A route that is not in the map is a question for the user, not a guess.

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
