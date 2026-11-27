# W01 — Python tooling baseline

| | |
|---|---|
| Order | 5 of 29 (Phase 0) |
| Closes | Q1, Q5 |
| Depends on | nothing |
| Effort | M |
| Spec | `docs/gap-audit.md` W01. |

## Goal

ruff format, ruff lint, strict mypy on the execution core, and coverage, all in CI.

## Files to touch

- `pyproject.toml` (new): ruff (line length 120, target py312), mypy (strict for `backend/app/storage.py`, `worker.py`, `approvals.py`, `accounting_state.py`, `execution_policy.py`; others `ignore_errors`), coverage
- `backend/requirements.txt` → runtime only; `backend/requirements-dev.txt` (pytest, pytest-asyncio, pytest-cov, ruff, mypy)
- `.github/workflows/ci.yml`
- `scripts/check_format_is_behavior_free.py` (new)
- Every `backend/app/*.py` (format only, in **one** commit with no other change)

## Tests to write first

| Check | Asserts |
|---|---|
| `scripts/check_format_is_behavior_free.py <parent> <commit>` | For each Python file: `ast.dump(ast.parse(old)) == ast.dump(ast.parse(new))`. Run it on the format commit; it must print 0 differences |
| CI step `ruff format --check` | Fails on the parent of the format commit |
| CI step `ruff check` | Fails before the lint-fix commits |
| CI step `mypy` | Fails before the type commits; record the error count before and after |
| CI step `pytest --cov=backend/app --cov-branch` | Reports line and branch coverage; no threshold in this item |

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
.venv/bin/ruff format --check backend scripts && .venv/bin/ruff check backend scripts && .venv/bin/mypy
```

## Invariants

All of I1–I8: the format commit must change no behavior. That is what the AST script proves. Never mix a logic change into it.

## Done when

- Commits in this order: (1) config and dev requirements, (2) format only, (3..n) lint fixes by rule, (n+1..) types for the five core files, (last) CI steps.
- Coverage numbers (total and for each of the five core files) are in `docs/resume-evidence.md`.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W01 (Python tooling baseline).
Read docs/tasks/05-W01-python-tooling-baseline.md first, then every file it links.
The format commit must be alone and proven by the AST script. Open pull requests of other cards must be merged or rebased before it.

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
