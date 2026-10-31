# R01 implementation: Response source boundary (2026-10-31)

Plan: [revision 3](../../../docs/superpowers/plans/2026-10-30-response-source-boundary.md),
approved in the [revision-3 review](../../../docs/reviews/r01-plan-r3-review-2026-10-31.md), with its
U23/U24 clarification applied. Base commit: `9801c46` (application code identical to `812fefd`).

## What changed

| File | Change |
|---|---|
| `backend/app/platform_nodes.py` | `source_list()` (strict container), `published_batches()` (exact de-duplication, at most 120 per batch, no repeated id in a batch), `reused_dependencies()`, `verify_published()` (refusal is `UnsafeExecutionError`; control flow propagates unchanged). |
| `backend/app/registry.py` | `response_node` checks the listed sources plus the saved sources of reused loop items before it returns. Text and the listed sources are unchanged. |
| `backend/app/compiler.py` | `saved_dependencies` reach the run state; a restored `for_each` checkpoint records its item owners in `reused_owners`. |
| `backend/app/iteration.py` | An item reused from saved progress records its owner in `reused_owners`. |
| `backend/app/platform_validation.py` | `kb_errors` checks a saved Response and the dependencies of loop items reused from a checkpoint **or** from `loop_progress`; every saved container goes through `source_list`. |
| `backend/app/worker.py` | Preflight passes `loop_progress`; `compile_workflow` gets `saved_dependencies`; a Response value without `text` is a named failure (`<id>: the Response produced no answer.`). |
| `backend/app/main.py` | The resume endpoint passes `loop_progress` to `kb_errors`. |
| `docs/operations.md` | New section "Response source boundary" (plan 5e). |

## Tests first

`backend/tests/test_response_source_boundary.py`: 51 tests (U1–U24).

| Run | Result | File |
|---|---|---|
| New tests on unchanged application code, first version | 41 failed, 9 passed | `baseline-812fefd-app-code.txt` |
| New tests on unchanged application code, after strengthening (below) | 42 failed, 9 passed | `baseline-812fefd-app-code-strengthened.txt` |

The 9 baseline passes are the C tests (U9 ×3, U11, U13, U17, U18) and U12 `query-null`/`query-7`,
which the baseline already refuses (`null` and `7` decode, then raise `TypeError` when iterated). Every D
failure was read: each fails because the run publishes, gives 202 instead of 422, or gives
"Run failed unexpectedly.", not because of an import or fixture error.

## Mutants (`mutants.py`, `mutants.txt`)

Each mutant is applied to a fresh copy of `git archive HEAD` plus the changed files, under a
scratch directory; the working tree is never mutated. The runner requires every planned test
to fail.

The first run showed three planned tests too weak. The mutants were caught only by other tests:

| Mutant | Weak test | Cause | Change |
|---|---|---|---|
| N2 (check cited sources only) | U3 | Its only source was cited, so a cited-only check still refused. | Two passages; the answer cites S1; only the uncited S2 is removed. |
| N3 (no de-duplication) | U13 | A C test that only checked the labels; the real verifier passes both copies. | Records the checked ids and asserts no record is checked twice. Still passes on the baseline (zero checks). |
| N13 (endpoint drops `loop_progress`) | U8 | It used U5's state (checkpointed loop), which needs no progress. | Parametrized over U5's and U6's states (`loop`, `item`), as the plan's U8 row ("both resume entry points … for U5's state") and its mutant table (N13 → U8) together require. |

After the change: **all 15 mutants are caught by their planned tests** (`ALL PLANNED KILLS: True`).

## Existing tests updated (7)

| Test | Change | Planned? |
|---|---|---|
| `test_agent_grounding::test_invented_citations_are_marked_and_cited_sources_reported` | The Response checks the same list a second time: `verified == [[…]]*2`. | Yes |
| `test_agent_grounding::test_response_rewrites_citation_when_run_has_no_evidence` | The stub context gets `platform=None, node_id='out'`; a real `Context` always has both. | No |
| `test_loop_event_bounds::test_events_emitted_through_the_context_carry_it_too` | Its fake platform answers `verify_vector_sources`. | Yes |
| `test_provenance_foundation::test_grounding_answer_does_not_launder_a_model_guess` | The typed passage has no knowledge-source fields and the run has no verifier, so the Response refuses (R02-P2). The draft provenance (`GUESSED`) and the `would_review` decision are still asserted, from the events. | No |
| `test_token_budget_recovery::test_t10c…` | `len(verified)` 2 → 3 (two abstentions, then the Response). | Yes |
| `test_token_budget_recovery::test_t14_a_removed_source_fails_a_nested_specialist_budget_abstention` | Asserts the final Response refusal; the `spec0` failure and the empty parent sources stay asserted. | Yes |
| `test_token_budget_recovery::test_t14b…[step, repair]` | `len(verified)` 1 → 2; the abstention's check `['chunk1']` stays first. | Yes |

The two unplanned changes do not relax a guarantee: one supplies attributes a real context
always has; the other is the fail-closed behavior the plan requires (production already fails
id-less passages at the gateway with `KeyError`).

## Deviations from the plan

- `validate_cached` does not receive `loop_progress`. A restored loop checkpoint is already
  covered by the loop-key rule, and an item reused from progress is checked by preflight and at
  the Response; passing progress would only repeat checks.
- U4 is implemented with `on_error: route` to a fallback Response, which is refused under the
  fallback's own id.
- U12 `query-null` and `query-7` already refuse on the baseline (see above).
- U3, U8 and U13 strengthened as described under Mutants.

## Results

| Check | Result | File |
|---|---|---|
| Backend `PYTHONHASHSEED` 0–4 | 1,949 passed each (1,898 + 51 new) | `seeds.txt` |
| Isolation: `git archive 9801c46` + the 14 changed files, `git init` + commit | 1,949 passed; imports from the copy | `isolation.txt` |
| Mutants N1–N15 | all caught by their planned tests | `mutants.txt` |
| Revision-3 review probe (`probe_plan.py`, all 11 real cases + 2 shape probes) on the isolation copy | exit 0: every assert holds, including the mixed partial case (2 provider calls, refused at `out`) | `auditor/r01-plan-r3-review-9801c46.json` |
| Revision-1 and revision-2 review probes, each case run separately (`auditor/probe_cases.py`) | every case holds except the asserts that demonstrated a defect, which now fail as intended (table below) | `auditor/*-cases.json` |
| TA04 auditor regressions (`test_real_source_boundary.py`, `test_replayed_attempt.py`), copies run on the isolation copy | 3 passed; the source-boundary regression fails on unchanged application code | `auditor/ta04-regressions.txt` |
| Deployed scenario gate (`python -m scripts.check_production_scenarios`) | 24 of 24 rows passed | `production/report.json` |

The revision-1 and revision-2 probes assert the defects they found, so on a correct fix those
asserts must fail:

| Probe case | The probe asserted (defect) | Now |
|---|---|---|
| r1 `shapes` | a Response with sources and no verifier publishes (R02-P2) | `out: knowledge source verification is unavailable. No answer was published.` |
| r1 `fresh_continue` | `on_error: continue` fails as "Run failed unexpectedly." (R03-P2) | the named `out:` refusal |
| r1 `cached_loop` | a restored loop publishes after its document is removed (R01-P1) | `each:0: saved knowledge sources are unavailable or removed.` |
| r2 `cached_loop_after_reuse` | a removal after the resume check still publishes (R2-01) | the `out:` refusal at publication |

All other r1/r2 cases hold unchanged. The auditor's saved evidence was not written: the probes
print to standard output, the regressions ran from copies, and the SHA-256 of every auditor
`probe-results.json`, `mutants.json` and TA04 JSON file is unchanged after the runs.

`production/report.json` SHA-256: `12e7b6d76f5e6bfd890a7e5b590a517a08b795dd562cf91f71190c455c54f1a8`.

## Residual

The window between the last check and the commit of the Response
([CWE-367](https://cwe.mitre.org/data/definitions/367.html)) remains. After a resumed loop,
reused item text can show `[unsupported reference]` (plan section 9, out of scope); its sources
are still checked.

Logs (`*.log`), `runs.json`, `provider-requests.json` and `production/switch/` stay local.
