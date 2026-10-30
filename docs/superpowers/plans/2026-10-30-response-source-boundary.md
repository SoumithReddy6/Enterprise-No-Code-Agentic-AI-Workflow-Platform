# R01: the final Response must not publish a source that is no longer valid

> Finding: R01 (P1) in [TA04 implementation audit](../../reviews/ta04-fix-review-2026-10-30.md),
> confirmed open in [the R02 re-check](../../reviews/ta04-r02-recheck-2026-10-30.md).
> Exists since at least `51ba164`. Baseline for this plan: `812fefd` (TA04 closed, hosted CI green).
> Status: draft for review.

**Goal:** a Response publishes its text and sources only after every source it lists has
passed the authoritative source check, fresh or restored from a checkpoint. If any listed
source is no longer valid, the Response fails closed: no success event, no checkpoint and
no run output for it.

**One rule:** *every publication of a knowledge source is checked at the moment it is
published.* Query and Agent already follow this rule. The Response does not.

## 1. The defect in Relay

| Location | What happens today |
|---|---|
| `registry.py:214–219` (`response_node`) | Lists **every** passage in the shared run registry (`ctx.run['evidence']`), cited or not, through `ground_answer`. No source check. |
| `evidence_registry.py` (`remember_evidence`) | Retrieval adds each passage to the shared registry when it is retrieved. A later failed check does not remove it. |
| `platform_validation.py:17–35` (`kb_errors`) | On restore, re-checks Retrieve and Query checkpoints and the `vector_dependencies` of owners that are checkpoint keys. A restored **Response** checkpoint is not checked. A loop item records its dependencies under `each:<index>`, which is never a checkpoint key, so those are not re-checked either. |

The audit reproduced R01 with a real temporary knowledge base and Worker. A specialist
retrieves a document; the document is removed during its last model call; the specialist's
source check fails; the parent Agent returns empty sources; the Response still lists the
removed document (`evals/results/ta04-fix-review-3deb964/test_real_source_boundary.py`).
The same exposure reproduces on `51ba164`. On the baseline, an Agent or Query whose own
check passed, followed by a removal before the Response, publishes the source in the same way.

## 2. What Relay already has (proven, CI-tested)

| Piece | Location | Behavior |
|---|---|---|
| Query post-generation check | `platform_nodes.py:52` | Verifies every retrieved passage, cited or not; fails the node if one is gone. |
| Agent post-answer check | `agent_runtime.py` (after `finalize_grounded_answer`) and `budget_abstention` | Verifies every listed source; fails closed. |
| Restore check | `worker.validate_cached` → `kb_errors` | Refuses to reuse a checkpoint whose saved sources are gone: "Start a new run with current evidence." |
| Authoritative verifier | `kb_gateway.verify` → index `_verify` + management `verify` | Exact metadata match, document still present, version still active. At most 120 sources per call; 20 per index call; **a repeated `id` in one call is refused** ("Duplicate source"). |

The fix reuses the verifier and the restore seam. It adds no new source of truth.

## 3. Trusted sources and what each one proves

| Source | Statement | Rule for Relay |
|---|---|---|
| [Saltzer and Schroeder, *The Protection of Information in Computer Systems*](https://web.mit.edu/Saltzer/www/publications/protection/Basic.html) | Complete mediation: "Every access to every object must be checked for authority." Fail-safe defaults: "Base access decisions on permission rather than exclusion." | The Response is an access path to source content, so it must check. A source is published because it passed the check now, not because no check rejected it. |
| [OWASP Top 10 for LLM Applications 2025, LLM08](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/) | Recommends "permission-aware vector and embedding stores". | Relay's verifier is that permission and availability authority. It must sit on the output path, not only on retrieval. |
| [CWE-367: Time-of-check Time-of-use race](https://cwe.mitre.org/data/definitions/367.html) | A check made earlier can be invalid at the time of use. | A check made by an upstream Agent or Query does not cover the Response. The Response checks just before it publishes; the remaining window is stated as a limit (section 8). |

## 4. Decision D1: what the Response does when a listed source is invalid

| Option | Behavior | Cost |
|---|---|---|
| **A. Fail closed (recommended)** | Check all listed sources; on any failure the Response node fails with a clear message. Nothing is published. | A valid answer (for example an abstention) is lost when an unrelated, uncited source was removed. The user must start a new run. |
| B. Drop and continue | Check each source alone; drop invalid uncited ones; fail only if a cited one is invalid. | One verifier call per source (up to 300 × 2 calls); a new behavior with no precedent in Relay; the published list would silently differ from what the run used. |

**Recommendation: A.** It is the rule Query and Agent already apply (any listed source,
cited or not), so one rule covers every publisher. The audit accepts it ("Failing before
Response publication is an acceptable closed outcome"). B can be a later product change.

## 5. Changes

### 5a. One check helper (`backend/app/platform_nodes.py`)

Two functions next to the existing `canonical()` (line 28):

- `published_batches(sources)` (pure): canonical form (drop `citation` and `cited`);
  de-duplicate exact copies (the same chunk retrieved twice has two labels and identical
  canonical metadata); split the rest into batches of at most 120 in which no `id` repeats.
  The fresh path (5b) and the restore path (5c) both use it, so they cannot drift.
- `async def verify_published(ctx, node_id, sources)`: calls
  `ctx.platform('verify_vector_sources', batch)` for each batch. No call when the list is
  empty or `ctx.platform` is `None`.
- Errors: `CONTROL_FLOW` members (`execution_policy.py:22`: pauses, cancellation,
  `LeaseLost`, …) propagate unchanged.
  `KeyError`, `ValueError` and `TypeError` from the verifier become one `ValueError`:
  `"{node_id}: a listed knowledge source is unavailable or was removed. No answer was published. Start a new run with current evidence."`

### 5b. The fresh Response (`backend/app/registry.py`, `response_node`)

After `ground_answer`, call `verify_published(ctx, sources)` before returning. The text, the
source list and the `cited` flags do not change. Under `on_error` `fail` the run fails;
under `continue`/`route` the node recovers with no outputs (existing compiler path), so
nothing is published either way.

### 5c. The restored Response (`backend/app/platform_validation.py`, `kb_errors`)

When `checkpoints` holds a `response` node, verify its saved `sources` through
`services.verify` for each `published_batches()` batch. On failure, add the existing restore error for that node, so
the resumed run is refused before anything is reused. The loop owners `each:<index>` are
not checkpoint keys; this check covers what they fed into the Response.

### 5d. Docs (`docs/operations.md`)

One passage: the Response checks every listed source before it publishes, and fails closed.

## 6. What does not change

- Which sources a Response lists, their order and their `cited` flags.
- The Agent and Query checks, their error texts, and the evidence registry.
- Run history: events already stored (for example a retrieval's own success event) keep
  what was retrieved at that time. See section 8.

## 7. Tests (write first)

D fails on `812fefd` and passes after the fix; C passes on both. Model calls use the real
`llm_node` with a substitute provider. Real-KB tests use the `services` fixture and
`populated()`, with removal through `remove_document`.

| # | Kind | Test |
|---|---|---|
| U1 | D | In memory, nested: the specialist's check fails (T14 nested scenario). The final Response publishes nothing: no `out` success event, `out` not in the values, and the run fails with the Response message. |
| U2 | D | Real KB and Worker (the audit's scenario): the document is removed during the specialist's last call. The run fails closed; the `out` checkpoint is absent; the run output holds no source of the removed document. |
| U3 | D | Removal **after** an upstream check passed: an Agent's answer verifies, then the document is removed before the Response. The Response fails closed. Also with a Query node upstream. |
| U4 | D | Restored Response: real KB, a loop with a Query body, crash after the `out` success event (its checkpoint is saved), remove the document, resume. The resume is refused with the restore error; the restored source is not republished. |
| U5 | C | Unchanged sources, fresh and restored: the run succeeds; the Response lists the same sources with the same `cited` flags; each distinct source is checked once per Response. |
| U6 | C | Duplicates: the same chunk retrieved twice (two labels) with the real verifier. The Response succeeds (the probe without de-duplication failed with "Duplicate source"). |
| U7 | C | More than 120 distinct listed sources: every verifier call has at most 120, and none repeats an `id`. |
| U8 | C | No evidence: the Response makes no verifier call. |
| U9 | C | Control flow: `LeaseLost` and cancellation raised by the verifier during the Response check propagate unchanged. |
| U10 | D | Budget fallback sites (step, refused repair) with a removed source: the Agent fails (T14) **and** no Response is published. |

### Existing tests that must change, and why

| Test | Change |
|---|---|
| `test_token_budget_recovery.py::test_t14_a_removed_source_fails_a_nested_specialist_budget_abstention` | Assert the final Response publishes nothing (it now fails closed) instead of a successful run. |
| `test_token_budget_recovery.py::test_t10c…`, `test_t14b…` | Their verify-call counts include the Response check (one more call). The agent-side "exactly once" stays asserted on the Agent's own call. |
| `test_agent_grounding.py::test_invented_citations_are_marked_and_cited_sources_reported` | `verified` gets the Response check as a second entry. |
| `test_loop_event_bounds.py::test_events_emitted_through_the_context_carry_it_too` | Its test platform needs a `verify_vector_sources` branch. |

The planning probe (`evals/results/r01-plan-2026-10-30/prototype-run.txt`) found exactly
these, plus the three T7b/T7c cases that de-duplication (5a) fixes.

## 8. Limits and out of scope

- **Check-to-publish window.** The check runs just before the Response returns. A removal in
  the milliseconds between the check and the checkpoint commit is not caught (CWE-367).
  This is the same window as the Query and Agent checks.
- **Run history.** Finished runs, and intermediate events such as a retrieval's own success
  event, keep the passages retrieved at that time. Removing a document does not rewrite
  stored runs. Whether deletion must propagate to stored run history is a data-retention
  decision for its own step.
- **Option B** (drop invalid uncited sources) is not built.
- TA02, TA03, TA05–TA07 and the accounting snapshot are not changed.

## 9. Mutants (scratch copies only)

| Mutant | Must be caught by |
|---|---|
| N1: remove the Response check | U1, U2, U3 |
| N2: check only cited sources | U1, U3 |
| N3: no de-duplication | U6 |
| N4: one call for all sources (no batching) | U7 |
| N5: restored Response not checked (5c removed) | U4 |
| N6: a catch-all that turns `LeaseLost` into the Response error | U9 |
| N7: check before `ground_answer` (on the registry, not the published list) | U5 |

## 10. Closing checklist

R01 is closed when each line has a passing test:

1. A fresh Response never publishes a source that fails the check, after a nested failure
   (U1, U2), after an upstream check passed (U3), and at the budget fallback sites (U10).
2. A restored Response is checked before reuse (U4).
3. Unchanged, duplicated and large source lists still publish exactly as before (U5–U8).
4. Control flow is never converted into a source failure (U9).

Then: backend `PYTHONHASHSEED` 0–4, the isolation copy, the deployed scenario gate, and
hosted CI on the pushed commit. The audit's `test_real_source_boundary.py` passes.

**Size:** one commit. About 40 changed lines in three application files
(`platform_nodes.py`, `registry.py`, `platform_validation.py`), plus tests and docs.
