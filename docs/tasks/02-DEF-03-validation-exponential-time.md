# DEF-03 — Validation exponential time

| | |
|---|---|
| Order | 2 of 29 (before Phase 0: defect fix, High) |
| Closes | DEF-03 |
| Depends on | nothing |
| Effort | S |
| Spec | `docs/defects.md` DEF-03 (design in the suggested fix). |

## Goal

Workflow validation must finish in time linear in nodes and bindings. Today a 42-node workflow does not finish (`docs/defects.md` DEF-03).

## Files to touch

- `backend/app/compiler.py:478-502` (`unchecked_model_input`, `calls_model`)
- `backend/app/provenance.py:536-570` (`static_label`: same per-path `seen` pattern; measure it first)
- `backend/app/main.py:112-118,156-158,186-190` (run validation with `asyncio.to_thread`)
- `backend/tests/defects/test_def03_validation_exponential.py` → `backend/tests/test_validation_cost.py`

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_validation_of_a_42_node_workflow_finishes_in_ten_seconds` | Exists; remove `xfail` |
| `test_unchecked_model_input_visits_each_node_once` | A call counter on the walk: at most `nodes × inputs` visits for the chain of 40 with doubled bindings |
| `test_the_trust_rule_gives_the_same_answers_as_before` | For every workflow in `examples/` and every fixture used by `test_request_contract_validation.py` and `test_switch.py`: the error list is equal to the list from the parent commit (generate the expected lists on the parent commit and commit them as a fixture) |
| `test_a_diamond_with_a_model_on_one_side_is_still_refused` | Memoization must not hide a path: node D reads B and C; only C comes from a model; a Switch on D is refused |
| `test_validate_route_does_not_block_the_event_loop` | While a slow validation runs in a thread (patched to sleep 1 s), `GET /api/health` answers in under 200 ms |
| `test_static_label_cost_is_linear` | The same chain: at most `nodes × inputs × 4` calls |

Mutants: memo key without `stop_at_sources`; a global visited set shared between two top-level calls (must be killed by the diamond test).

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

No runtime invariant is touched. The rule that must not change: validation and runtime share one trust rule (`compiler.py:505-511`); runtime calls the same function.

## Done when

- The defect test passes with no `xfail`; n = 100 validates in under 1 s (record the measured time).
- `backend/tests/defects/` count goes down by one.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item DEF-03 (validation takes exponential time).
Read docs/tasks/02-DEF-03-validation-exponential-time.md first, then every file it links.
Keep the meaning of the trust walk exactly. A memo may cache only complete answers: a node that is on the current path must not be cached as False.

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
