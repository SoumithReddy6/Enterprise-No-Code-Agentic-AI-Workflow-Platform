# TA04 fix: a token-budget refusal marks the run incomplete (2026-10-30)

Plan: `docs/superpowers/plans/2026-10-30-typed-token-budget-refusal.md` (final after three
reviews, committed at `2679fce`). Application code baseline: `51ba164` (unchanged up to
`2679fce`, which added plan and review documents only).

## Change

- `backend/app/token_budget.py`: `TokenBudgetExhausted(ValueError)`.
- `backend/app/registry.py`: `llm_node` raises it. The message does not change.
- `backend/app/execution_policy.py`: the policy catches it next to `ActionBudgetExhausted`
  and returns `budget_exhausted=True`, with no retry. `ExecutionOutcome.budget_kind` is
  `'action'` or `'tokens'`. A token-refused attempt counts as executed only if, during that
  attempt, an action was charged or a model call was recorded.
- `backend/app/agent_runtime.py`:
  - `finalize_grounded_answer` and the structured-only repair re-raise the typed refusal.
  - D1 = B for free-text agents: `budget_abstention` (no model call; evidence listed
    uncited after the normal `verify_vector_sources` check; grounding `abstain: true`,
    `compliance: "budget_exhausted"`, truncated with the token reason). It is used when
    exhaustion is known at a step and when a final-answer repair is refused.
  - A structured agent (output schema, `extraction`, `classification`) raises the typed
    refusal at a step, with or without evidence (checked before the no-evidence branch),
    and when its repair is refused.
- `backend/app/iteration.py`: an item refused after it did work gets `budget_halt`
  (`tokens` or `action`) before admission, so it is saved in the same `run_loop_items` row
  and `loop_item` event. `compact()` keeps it and `COMPACT_BYTES` covers it (226 → 251
  bytes). One function, `budget_halt`, reads the halt from the admitted entry in fresh and
  replayed runs; the summary reason comes from the kind only. The loop journal
  `reason_code` follows `budget_kind`.
- `docs/operations.md`: the Token ceiling section, and the compact reserve (251 bytes).

## Additions and deviations from the plan

- **Refused repair with no evidence (addition).** A free-text agent can be grounded (a
  retrieval tool is attached) and hold no evidence. `budget_abstention` would then say that
  collected sources are listed when there are none. The plan's rule for the step site
  ("a free-text agent with no evidence keeps today's abstention") now applies at the repair
  site too, through one shared helper, `exhausted_abstention`. Test T9d, mutant M16.
- **T14b is D, not C.** The plan labels T14b a control. It cannot pass on `51ba164`,
  because B does not exist there. It is recorded as D.
- **Router test (T11b).** The closing checklist names "a router test"; the plan's test table
  has no row for it. T11b is that test.

## Results

| Check | Result |
| --- | --- |
| New tests on `51ba164` application code (`baseline-51ba164-app-code.txt`) | 37 defect tests (D) fail, 6 controls (C) pass |
| New tests on the fix | 43 of 43 pass |
| Mutants (`mutants.py`, `mutants.json`, fresh scratch copy each) | 16 of 16 killed, each by all of its planned tests; no existing test fails under any mutant |
| Backend `PYTHONHASHSEED` 0–4 | 1,894 passed each (1,851 before + 43 new) |
| Isolation: clean `HEAD` export + the commit files (`isolation.txt`) | 1,894 passed |
| Deployed scenario gate (`production/report.json`) | 24 of 24 rows passed; source unchanged during the run (`head` `2679fce` + working tree) |
| Revised probe (`probe_recovered_token_refusal.py`, `recovered-token-refusal.json`) | `continue` and `route`: `success`, `truncated: true`, cause `('m1','budget','confirmed')`, 1 provider call; `fail`: failed |

D tests: T1, T2, T5, T6, T7, T7b, T7c (both), T8, T9, T9b, T9c, T9d, T10, T10b, T10c, T11,
T11b, T14, T14b. C tests: T3, T4, T10d, T12, T15. T13 (the existing action-budget tests in
`test_budget_recovery.py` and `test_run_causes.py`) passes unchanged in the full suite; no
existing test was edited.

The historical probe in `evals/results/token-accounting-review-2026-10-25/` asserts the old
result (`truncated: false`). It was not run and not changed; its file hashes were the same
before and after the revised probe ran.

## Closing checklist (plan section 10)

1. Typed refusal through every model call and repair path: LLM (T1), Query (T8), Request
   reader (T11), Request router (T11b), Agent step (T10b), Agent grounded repair (T9b),
   Agent structured repair (T9).
2. The fallback keeps the source check and the output contract: T9, T9b, T10b, T14, T14b.
3. A compacted crash and resume gives the same results and spend as a fresh run: T7b, T7c.
4. A valid answer that ends exactly at the limit stays valid: T15.

## Limits

- Synthetic provider responses only; no live provider was called.
- B stops further calls once exhaustion is known. It does not make the ceiling strict: a
  call admitted under the ceiling can still finish above it (TA05).
- Not changed (out of scope): TA02, TA03, TA05, TA06, TA07, the monotonic accounting
  snapshot, the attached-agent string match, and the structured-agent prose abstention when
  only the action or step budget runs out.

`production/report.json` SHA-256: `e0355caa59033b9fa0d15d7913566960a183899655ae7cef87eb1d875c18e92b`.

Logs (`*.log`), `runs.json`, `provider-requests.json` and `production/switch/` stay local.
