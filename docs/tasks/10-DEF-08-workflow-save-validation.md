# DEF-08 — Workflow save validation

| | |
|---|---|
| Order | 10 of 29 (Phase 1: defect fix, Low) |
| Closes | DEF-08 |
| Depends on | nothing |
| Effort | S |
| Spec | `docs/defects.md` DEF-08. |

## Goal

A saved workflow cannot hold a setting over its declared limit, and the API has a request size limit.

## Files to touch

- `backend/app/main.py:81-88,121-133` (`safe_draft`: validate each node config with its model; keep the generic message that hides credential hints)
- `backend/app/main.py` (a middleware that refuses a body over 26 MB with 413, before parsing)

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_def08_a_saved_workflow_cannot_hold_a_setting_over_its_declared_limit` | Exists; remove `xfail` |
| `test_a_draft_with_an_incomplete_but_valid_config_still_saves` | A workflow that fails graph validation (no response node) but has valid node settings saves: drafts must stay possible |
| `test_an_oversized_request_body_is_refused_before_parsing` | 413 for a 27 MB body on `/api/workflows`, `/api/validate`, `/api/runs` |
| `test_existing_saved_workflows_still_load` | Every file in `examples/` saves |

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

## Invariants

None of I1–I8.

## Done when

- The defect test passes with no `xfail`.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item DEF-08 (workflow save validation).
Read docs/tasks/10-DEF-08-workflow-save-validation.md first, then every file it links.
Do not make save run the full graph validation: an incomplete draft must still save.

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
