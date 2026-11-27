# W02 — Split the two large functions

| | |
|---|---|
| Order | 15 of 29 (Phase 2 (with W10)) |
| Closes | Q2, Q3 |
| Depends on | W01, DEF-01-02 (they touch the same lines) |
| Effort | M |
| Spec | `docs/gap-audit.md` W02. |

## Goal

`Worker._execute` and `compile_workflow` are split so that the Principal can pass through them and the write path is one method.

## Files to touch

- `backend/app/worker.py:67-274` → class `RunPlatform` (one method for each action of `platform_resolver`, `worker.py:137-221`); the write path `worker.py:194-212` becomes `RunPlatform.send_write`
- `backend/app/compiler.py:564` → a `RunContext` dataclass in place of the 24 parameters; `compiler.py:634-698` (restore) and `:699-842` (execute) become module-level functions
- No test file changes except imports

## Tests to write first

No new behavior, so no new behavior test. Proof:

| Check | Asserts |
|---|---|
| Existing suite | Passes with import changes only (`git diff --stat backend/tests` shows no assertion change) |
| Existing mutants for the write path | Re-run the mutant sets of `evals/results/ta02-fix-2026-10-31/` and `evals/results/a61-part3-2026-11-16/mutants/` against the new code: every mutant that can still be applied is killed |
| `scripts/check_function_length.py` (new) | Longest function in `worker.py` and `compiler.py` before and after, printed |

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

The order "already sent? → check request → mark → send → settle" must be readable top to bottom in `send_write`, unchanged.

## Done when

- The longest function in each file is under 80 lines (record the numbers). No behavior change.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W02 (split the two large functions).
Read docs/tasks/15-W02-split-the-two-large-functions.md first, then every file it links.
Pure refactor. If a test needs a changed assertion, stop: the refactor changed behavior.

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
