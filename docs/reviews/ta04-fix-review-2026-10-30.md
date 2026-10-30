# TA04 implementation audit — 2026-10-30

Reviewed commit: `3deb964` (`Type the token-budget refusal so recovered nodes mark the run incomplete (TA04)`).
The application and original plan/evidence files were not changed by this review.

## Assessment

The main fix follows the final plan: token refusal is typed, repair wrappers preserve it,
recovered graph nodes report a confirmed budget cause, free-text B verifies sources,
structured Agents retain typed failures, and loop halt kinds survive compaction and replay.

**One TA04 correctness defect remains (R02).** Fix it before declaring TA04 closed.
There is also an **existing Response source-exposure defect (R01)** that the new T14
safety assertion misses. Baseline comparison confirms that R01 was not introduced by
TA04; it can be fixed separately, but the end-to-end source-exclusion claim is not yet true.

## R01 — P1: a rejected knowledge source still reaches the final Response

Locations: `backend/app/registry.py:214–219` (`response_node`) and
`backend/tests/test_token_budget_recovery.py:439–452` (nested T14 coverage).

The new `budget_abstention` source gate correctly rejects a removed document. The
specialist fails and its parent returns an abstention with empty `sources`. However,
retrieval already registered the source in shared `ctx.run['evidence']`. `response_node`
ignores the Agent's empty source set and calls `ground_answer` on that entire shared
registry. That returns all those passages, including uncited ones, without revalidating
their current availability.

Observed with real `llm_node`, synthetic provider HTTP and a controlled removal:

```text
specialist: failed, "Knowledge source was removed: doc"
parent Agent sources: []
final Response sources: [chunk1, text="Fact 1", cited=false]
```

I also reproduced it through the real Worker and temporary management/search databases:
create and ingest a document, retrieve it, remove it during the specialist's final model
call, refuse repair at the token ceiling, and finish the run. The run succeeds and its
Response checkpoint includes the removed document. The authoritative verifier rejects
that same returned source.

The new nested T14 checks success events only for `agent` and `spec0`, then checks the
parent Agent's sources. It does not inspect `out`, which is where the source reappears.

**Fix direction:** enforce source validity at the final Response boundary before publishing
its text/sources and success checkpoint; fail closed if those sources are no longer valid.
Also ensure rejected evidence cannot be republished from shared run state by another path.
Do not rely on `cited: false` or registry membership as proof of current validity.

**Regression:** assert the final Response and its persisted success output, not only the
intermediate Agent. Cover source removal during a nested call and during both budget
fallback sites. Failing before Response publication is an acceptable closed outcome.

**Scope:** the same output exposure reproduced on `51ba164` application code. That baseline
did not reach the new budget source gate, but still returned the removed source from the
same shared registry. The source check added by TA04 is useful and correct; a separate
older boundary remains unprotected. This is not a second new TA04 regression.

## R02 — P2: approved work on resume is counted as “did not run”

Location: `backend/app/execution_policy.py:240–247`, especially
`executed=debited or _model_calls(run)>calls`. Consequent loop branch:
`backend/app/iteration.py:330–336`.

The new detector equates work with a new budget debit or a newly recorded model call.
An approved action can execute during frame replay using a reservation made before the
approval pause. It requires neither a new debit nor a new model call. The enclosing
Agent's policy also has `charge_first_attempt=False`, so its local `debited` stays false.

Reproduced through the real Worker, approval records, SQLite and HTTP mock transport:

1. A structured Agent makes its planning call: 8 + 2 tokens, reaching the limit of 10.
2. It requests a POST; the action is reserved and the run pauses for approval. No send.
3. Approve and claim the run again. The frame replays, sends exactly one POST and records
   the completed approval result.
4. The next Agent step raises `TokenBudgetExhausted` before another model call.
5. The policy reports `attempts=0`, so the run reason is **"agent did not run"**.

The same case in a loop has a more concrete consequence:

```text
approved deliveries: 1
provider calls: 1 total, 0 after resume
approval status: completed; saved result: "sent"
loop results: []
loop summary: processed=0, failed=0
body event: not_run
```

Because `_run_item` sees `attempts=0`, it returns no entry. The item that performed the
write never gets a failed result or the new durable `budget_halt` marker. This violates
the plan's rule for an item that did work before its token refusal.

There was **no duplicate send or extra model spend** in these probes. The defect is the
new attempt classification and its use in durable item progress.

**Fix direction:** distinguish actual invocation work from its budget reservation. Track
work performed by the attempt, including approved frame replay and nested execution,
without charging the reservation again. Keep a cache-only replay distinct from a real
external execution. Merely switching to action-counter deltas will still miss an action
whose reservation was already held before approval.

**Regressions:** the graph case must say "stopped after 1 attempt(s)"; the loop case must
record one processed failed item with `budget_halt: tokens`. Action counters stay at one
for the graph write, and two for the loop's Jira read plus write. Preserve the one-provider
call total and the single POST. Include a subsequent crash/resume and a cache-only control
so this cannot be fixed by double charging or treating every attempted operation as work.

## The four implementation notes

All four are reasonable and reflected in the implementation/evidence:

1. `exhausted_abstention` handles an empty evidence set truthfully at the step and refused
   repair sites, retaining truncation and the budget reason. Structured token refusal is
   checked before this branch. The existing action/step-only structured abstention issue
   remains explicitly separate.
2. T14b must be a D test: the source-checked B result does not exist on the baseline.
3. T11b fills the router repair-path coverage named by the plan's closing checklist.
4. The runtime compact reserve is **251 bytes**, and the operations documentation agrees.

## Verification

- Independently ran the new file plus relevant existing suites: **126 passed** (all 43 new
  tests included), with one unrelated deprecation warning.
- Added three independent red regressions outside normal test discovery: **3 failed** for
  the expected assertions: removed source in final Response; approved graph work reported
  as not run; approved loop work omitted from processed results.
- Real temporary KB/Worker storage was used for R01. Real Worker/approval records and HTTP
  request serialization were used for both R02 cases. No user workspace data or external
  service was modified.
- Production evidence: all **24/24** row statuses are passed; `source_files_changed` is
  empty; every saved source hash matches the reviewed checkout. Report SHA256 matches
  `e0355caa59033b9fa0d15d7913566960a183899655ae7cef87eb1d875c18e92b`.
- Inspected the mutation evidence: **16 recorded kills**, every expected killer present,
  no unrelated recorded test failure. Those mutations were not independently rerun.
- The reported five complete 1,894-test runs and isolation result were not rerun by this
  audit. Hosted CI, PostgreSQL, frontend and the deployed campaign were not rerun.
- Original implementation/evidence hashes were preserved. New artifacts:
  `evals/results/ta04-fix-review-3deb964/`.

## Recommendation

Fix R02 before closing TA04. Track R01 as a separate security correction and expand T14's
assertions to the final Response; do not describe the current source check as an
end-to-end exclusion guarantee. Neither finding requires taking on TA02, TA03 or strict
token reservation in this change.
