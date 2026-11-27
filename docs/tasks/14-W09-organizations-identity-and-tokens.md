# W09 — Organizations, identity and tokens

| | |
|---|---|
| Order | 14 of 29 (Phase 2) |
| Closes | S1 (part), S5, S7 |
| Depends on | W08 |
| Effort | L |
| Spec | `docs/specs/multi-tenancy-core.md` §2.1–2.3, §2.7, §2.9–2.11; pull requests 1, 5, 6. |

## Goal

Organizations, memberships, the Principal, local passwords, service-account tokens and OIDC sign-in.

## Files to touch

Spec §5: PR 1 (steps 1–5), PR 5 (14–15), PR 6 (16–17). New: `backend/app/principal.py`, `permissions.py`, `oidc.py`; migrations `orgs_and_members`, `users_and_tokens`; `backend/app/auth.py`; `backend/requirements.txt` (`authlib`, pinned; `pip-audit`).

## Tests to write first

Spec §6.8 (`test_orgs_migration.py`, `test_principal.py`), §6.6 (`test_service_tokens.py`), §6.7 (`test_oidc.py`). Mutants M08–M10.

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

`pip-audit` after the dependency change (network: ask first).

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

No run row changes its `tenant_id` (`test_no_run_row_changes_its_tenant_id`). `auth.tenant` stays as a wrapper until W10, so no route changes here.

## Done when

- The three pull requests are merged one at a time, each with the gate passed. S11 and S12 pass unchanged.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W09 (organizations, identity and tokens).
Read docs/tasks/14-W09-organizations-identity-and-tokens.md first, then every file it links.
Do one pull request of the spec in one session: PR 1 first. Do not start PR 5 or PR 6 in the same session.

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
