# TA03 fix evidence: each outcome reports only its own usage

Design (approved, P2 tier): `docs/superpowers/specs/2026-11-02-token-outcome-attribution-design.md`.
Diagnostic evidence: `evals/results/ta03-design-2026-11-02/`. Baseline application: HEAD `6317dad`.
No separate implementation plan: §6 of the design is the test budget, gate list and stop rule.

## What changed

| Area | Change |
|---|---|
| `execution_policy.py` | `with_own_usage(event, run, identity)` attaches `usage_for` (this invocation only) or nothing. `PersistedNodeCancellation` moved here and now names the `invocation` whose outcome committed; `outcome_saved(exc, identity)` matches it. |
| `compiler.py` | Root cancellation, unsafe-execution and uncertain-write events use the node's own usage instead of the checkpoint owner's inclusive total. A child's receipt no longer suppresses the root's own cancellation report; the root still normalizes to the base `CancelledError`. An accounting refusal after the node's own paid work publishes one failed outcome. `PersistedNodeCancellation` is re-exported at its old path. |
| `agent_runtime.py` | The attached-target boundary reports a cancelled child's own usage before re-raising, unless that child's outcome is already saved. Unsafe-execution and accounting refusals publish the child's failed outcome when it has usage, then keep their control flow. |
| `iteration.py` | The loop-item boundary does the same for cancellation, and for unsafe, uncertain and accounting refusals. A cancelled item is not appended, retried or made reusable. |
| `worker.py` | After a confirmed commit observed together with cancellation, the receipt now covers transient (nested) and paused outcomes too, and carries `invocation_id`, or `node_id` for a root node. A false fence acknowledgment still raises plain cancellation. |
| Tests | New `backend/tests/test_usage_attribution.py` (13 collected cases; budget 16). One existing selector narrowed, below. |
| Docs | `docs/operations.md`: an **Attribution** paragraph under operator metrics. |

## Files

| File | Content |
|---|---|
| `baseline-6317dad.txt` | The 13 tests on the unchanged application: 7 failed, 6 passed (the planned controls). |
| `mutants.py`, `mutants.txt` | M1–M8 on fresh copies of the isolation base; all 8 planned kills. |
| `seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy: 1,987 passed at each seed. |
| `isolation.txt` | Clean-export proof (git archive `6317dad` + the 8 changed files). |
| `production/report.json` | Deployed scenario gate: 24 of 24 rows passed, no source file changed during the run. |

## Baseline (red) classification

All 7 failures are metric or attribution mismatches. None is a missing interface: the
module imports only existing application names.

| Test | Baseline result | Fixed result |
|---|---|---|
| U01 two completed specialist calls (second model), parent cancelled | 90 tokens, 6 calls; `fixture-b` 60/4 | 60, 4; `fixture` 30/2, `fixture-b` 30/2 |
| U02 cancelled inside a grandchild with known usage | total 45 correct, but all 45 on `agent` | 15 each on `agent`, `helper`, `grand` |
| U04 loop item cancelled after its response reported usage | 15 tokens (the item's 15 was lost) | 30 |
| U05[child] cancellation while a child's outcome commit is acknowledged | 45 | 30 |
| U08[unsafe], U08[uncertain] refusal after three paid responses | 60 | 45 |
| U08[accounting] nested pending-save refusal after paid work | 0 (no usage row at all) | 30 |

Controls passing on the baseline (6): U03[agent], U03[tool] (a child with no usage inherits
none), U05[root] (a saved root checkpoint is reused, no second report), U06 (fence refusal),
U07 (item outcome saved before its progress), U09 (approval pause/resume, two models).

Two results are an **undercount** on the baseline, not a double count: U04 (a cancelled loop
item's usage is stored under `each:<index>`, but the loop node's cancellation read `each`) and
U08[accounting] (the refusal passed every boundary without an outcome). Both are fixed by the
same rule.

## Mutants

| Mutant | Planned oracle |
|---|---|
| M1 parent cancellation reports the inclusive total | U01, U02 |
| M2 an active specialist unwinds without reporting | U02 |
| M3 an active loop item unwinds without reporting | U04 |
| M4 exceptional failures report the inclusive total | U08 unsafe, uncertain |
| M5 an already-saved child outcome is reported again | U05 child |
| M6 any saved descendant outcome suppresses the parent | U05 child |
| M7 an invocation with no usage inherits its checkpoint owner's total | U03 agent, tool |
| M8 a resumed attempt starts from the saved per-invocation usage | U07, U09 |

## Deviations and test-defect fixes (recorded, not hidden)

1. **Three test defects fixed before the recorded baseline.** The first baseline run showed:
   LangGraph surfaces cancellation as `NodeCancelledError` at `graph.ainvoke` (U03); the
   pending-save barrier flushes through `reserve_accounting`, not `record_accounting`, so the
   first U08[accounting] outage never refused anything and the request reached the stub
   provider; and `loop_progress` is absent on an interrupted run before its first item (U07).
   The recorded baseline is the corrected module.
2. **One existing test selector narrowed.** `test_operations.py::test_cancelled_agent_preserves_reported_token_usage`
   took the first `cancelled` event. The cancelled `calc` tool now reports its own outcome
   first, without usage. The selector now picks the agent's event; the original assertion
   (13/7 on the agent) is unchanged, and an assertion was added that the tool carries no
   usage. It passes on the baseline too.
3. **New failed outcomes only when the invocation has usage.** For nested and loop-item
   unsafe/accounting refusals, and a root accounting refusal, a failed outcome is published
   only when that invocation spent something. This keeps event sequences unchanged where no
   usage exists, including the claim-time and legacy refusals TA02 tests check. Cancellation
   always publishes a `cancelled` outcome, as the root already did.
4. **U03 runs at compile level.** It uses the same `usage_rows` function the Store uses for
   metrics. The other 12 cases use the real Worker and Store.
5. **Receipt scope.** `not_run` (an item refused by a budget before any attempt) is not in
   the receipt list. If cancellation lands during that exact save, the item can also publish
   a `cancelled` status. That event carries no usage, so no metric changes.

## Not claimed

Usage is still incomplete for an unreported provider response, a SIGKILL before durable
publication, a failed or unconfirmed commit, and writes refused after lease loss; nothing
is estimated to hide those gaps. Saved runs are not rewritten: an older run's parent
cancellation or failure event can still repeat its children's usage. There is still no
separate record for each provider call; TA06 can count calls with no usage through the same
per-invocation counters. Strict reservation, provider cache categories and concurrent
settlement stay with TA05, TA07 and TA08.
