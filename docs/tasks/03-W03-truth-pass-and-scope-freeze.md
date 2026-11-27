# W03 — Truth pass and scope freeze

| | |
|---|---|
| Order | 3 of 29 (Phase 0) |
| Closes | §2.2 claims, Q4, X4, X5, E6 |
| Depends on | nothing |
| Effort | S |
| Spec | `docs/gap-audit.md` §2.2 (the 11 claims) and W03. |

## Goal

Every claim in the README and the two evidence documents agrees with the code, and the roadmap is the plan of `docs/gap-audit.md` §8.

## Files to touch

- `README.md` (lines 3, 11-13, 313, 318, 390, 404, 425, 432, 440-480)
- `docs/status-report.md:47`, `docs/resume-evidence.md:25,67-68`
- `docs/design.md` (new: move README §2 there)
- `scripts/check_readme_claims.py` (new), `.github/workflows/ci.yml`
- `frontend/lib/workflow.ts:412-442` (take the request reader out of the palette)
- Delete `:memory:.ses`, `probe-results.json` at the root
- Do **not** delete the legacy nodes or the request reader (`docs/gap-audit.md` §9, point 2)

## Tests to write first

`backend/tests/test_readme_claims.py`:

| Test name | Asserts |
|---|---|
| `test_backend_test_count_in_readme_matches_collection` | The number in the README equals `pytest --collect-only` |
| `test_scenario_count_in_readme_matches_the_gate` | Every place that states a scenario count gives the number of S-rows in `scripts/check_production_scenarios.py` and `scripts/scenarios/` (fails today: `README.md:425` says 27) |
| `test_ci_job_count_matches_the_workflow` | Jobs in `ci.yml` |
| `test_stack_line_separates_live_and_fixture_tested` | The stack row names OpenAI, Claude, Elasticsearch, Pinecone only under "adapter, tested with fixtures" until evidence exists |

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

No source behavior changes except the palette entry.

## Done when

- All 11 rows of §2.2 are corrected or deleted; the README's first screen has the diagram and four measured numbers, each with its file.
- The known defects in `docs/defects.md` that are still open are named in the README's limits section.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W03 (truth pass and scope freeze).
Read docs/tasks/03-W03-truth-pass-and-scope-freeze.md first, then every file it links.
This is a documentation item. Change no runtime behavior. Do not remove any node type.

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
