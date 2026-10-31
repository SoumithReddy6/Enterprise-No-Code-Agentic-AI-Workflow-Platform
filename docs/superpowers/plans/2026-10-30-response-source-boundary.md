# R01: a run must not publish an answer whose knowledge source is no longer valid

> Finding: R01 (P1) in [TA04 implementation audit](../../reviews/ta04-fix-review-2026-10-30.md),
> confirmed open in [the R02 re-check](../../reviews/ta04-r02-recheck-2026-10-30.md).
> Exists since at least `51ba164`. Baseline for this plan: `812fefd` (TA04 closed, hosted CI green).
> Revision 3, after [the revision-2 review of `e50fa65`](../../reviews/r01-plan-r2-review-2026-10-31.md)
> (D1, D2, D3 kept; one P1 gap and one test correction, section 12). Revision 2 followed
> [the plan review of `65a7774`](../../reviews/r01-plan-review-2026-10-30.md) (section 11).
> Status: approved for implementation ([revision-3 review](../../reviews/r01-plan-r3-review-2026-10-31.md)); U23/U24 clarified as that review asks.

**Goal:** a run publishes a Response only after every knowledge source behind it has
passed the authoritative source check now. This applies to a fresh Response, a restored
Response, and a Response built on reused loop work. If a source is no longer valid, the run
fails closed with a named refusal: no Response success event, no Response checkpoint and no
run output.

**One rule:** *every publication or reuse of a knowledge source is checked when it is
published or reused.* Query and Agent already check at publication. The Response does not,
and the restore check skips loop items.

## 1. The defect in Relay

| Location | What happens today |
|---|---|
| `registry.py:214–219` (`response_node`) | Lists **every** passage in the shared run registry (`ctx.run['evidence']`), cited or not, through `ground_answer`. No source check. |
| `evidence_registry.py:31` (`remember_evidence`) | Retrieval adds each passage to the shared registry. A later failed check does not remove it. |
| `platform_validation.py:17–35` (`kb_errors`) | On restore, checks Retrieve and Query checkpoints and the `vector_dependencies` of owners that are checkpoint keys. It does not check a restored **Response** checkpoint. A loop item records its dependencies under `<loop id>:<index>` (`iteration.item_owner`), which is never a checkpoint key, so reused loop items are not checked. |
| `worker.py:109`, `main.py:189` | The restore check gets the checkpoints but not `loop_progress`, so it cannot see which loop items a resume will reuse. |

Three paths publish a removed document's content:

1. **Nested rejection** (the audit's real-KB reproduction,
   `evals/results/ta04-fix-review-3deb964/test_real_source_boundary.py`): the specialist's
   check fails, the parent Agent returns no sources, and the Response still lists the
   removed document from the shared registry.
2. **Upstream check passed, removal before the Response:** the source stays in the shared
   registry and the Response publishes it.
3. **Reused loop work** (plan review, R01-P1): a crash after the loop checkpoint and before
   the Response; the document is removed; on resume the loop restores its answer text with
   no source list, the Response sees `sources: []` and publishes the old answer. No source
   check runs. The dependency is still saved under `<loop id>:0`.
4. **Reused loop work, removal after the resume check** (revision-2 review, R2-01): the
   resume check passes, the loop is reused, a later node commits, and then the document is
   removed before the Response. A restored loop has `results`, `failed` and `summary` but
   no `sources` port, so `evidence_from_outputs` (`agent_runtime.py:165`) registers nothing.
   The Response sees an empty list, makes no check and publishes the old answer. A valid
   empty presentation list does not prove that the answer has no knowledge dependency.

## 2. What Relay already has (proven, CI-tested)

| Piece | Location | Behavior |
|---|---|---|
| Query post-generation check | `platform_nodes.py:52` | Verifies every retrieved passage, cited or not; fails the node if one is gone. |
| Agent post-answer check | `agent_runtime.py:146`, `:454` | Verifies every listed source; fails closed. |
| Restore check | `worker.validate_cached` and preflight → `kb_errors` | Refuses to reuse a checkpoint whose saved sources are gone: "Start a new run with current evidence." |
| Saved dependencies | `storage.record_vector_dependencies` | Each owner's verified sources, merged by `id`, at most 120. Cleared when a non-loop owner starts again (`storage.py:670–673`). |
| Refusal as control flow | `request_safety.UnsafeExecutionError` (in `CONTROL_FLOW`) | "Refusal is control flow, never retryable/recoverable." The compiler emits a named failed event (`compiler.py:659`) and the run fails with the message. Restore refusals already use it ("cannot be restored"). |
| Authoritative verifier | `kb_gateway.verify` → index `_verify` + management `verify` | Exact metadata match, document present, version active. At most 120 sources per call; a repeated `id` in one call is refused ("Duplicate source"). |

The fix reuses the verifier, the saved dependencies, the restore seam and the refusal class.
It adds no new store, schema or model call.

## 3. Trusted sources and what each one proves

| Source | Statement | Rule for Relay |
|---|---|---|
| [Saltzer and Schroeder, *The Protection of Information in Computer Systems*](https://web.mit.edu/Saltzer/www/publications/protection/Basic.html) | Complete mediation: "Every access to every object must be checked for authority." The paper applies it to recovery as well as normal operation, and warns that remembered authority checks must be updated when authority changes. Fail-safe defaults: "Base access decisions on permission rather than exclusion." | The Response is an access path to source content. Reused loop work is a remembered check, so it is checked again on resume. A malformed or missing source list is not permission; it is refused. |
| [OWASP Top 10 for LLM Applications 2025, LLM08](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/) | Recommends "permission-aware vector and embedding stores". | Relay's verifier is that authority. It is not enough to check presentation metadata: reused answer text needs the check of its saved dependencies. |
| [CWE-367: Time-of-check Time-of-use race](https://cwe.mitre.org/data/definitions/367.html) | A check made earlier can be invalid at the time of use. | An upstream check does not cover the Response. The remaining window is stated in section 9. A document removed while the worker is stopped is **not** this window; the resume check covers it. |

## 4. Decisions

### D1: what happens when a listed source is invalid — **A, fail closed (accepted)**

The Response checks all listed sources; on any failure nothing is published. Option B (drop
invalid uncited sources) is not built: the verifier does not say which source failed, and no
Relay publisher does it.

### D2: which reused loop work is checked on resume — **all reused items (conservative)**

An owner `<loop id>:<index>` is reused when the loop node is a checkpoint key, or when
`loop_progress[<loop id>]` holds that index. Every reused owner's saved dependencies are
checked, whether or not the Response will read that item. A narrower "only items the
Response consumed" rule needs a mapping from Response text to items that Relay does not
have. Cost: a resume is refused if any reused item used a removed document. The user starts
a new run, which is the existing restore message.

The same set is checked **twice**: at resume (preflight and loop reuse, 5c) and again at
each Response publication (5f). The resume check stops reuse early; the publication check
covers a removal after the resume check. A successful earlier check is a remembered
result, so it does not replace the check at publication.

### D3: the Response's `on_error` setting — **the refusal ignores it**

Today a Response accepts `on_error` `continue` and `route` and both behave the same as
`fail`, because a Response cannot fail (probe, `response-on-error-probe.txt`). R01 adds the
first real Response failure. Options:

| Option | Behavior | Cost |
|---|---|---|
| **a. Refusal (recommended)** | The source failure raises `UnsafeExecutionError`. It is control flow, so retry and recovery do not apply under any `on_error`. The run fails with the named message. | `continue` and `route` on a Response stay accepted and have no effect on this failure. Documented. |
| b. Validation: Response must use `fail` | Reject `continue`/`route` on a Response at save and run time. | Saved API workflows that run correctly today are refused. The editor has no `on_error` control (`frontend/lib/workflow.ts:11` is a type only), so only API or imported workflows are affected, but they would break for a setting that did nothing. |
| c. Recover with no output | `continue` returns `{}`; the worker must handle a Response without `text`. | A run "succeeds" with no answer and no named failure. Not fail closed. |

**Recommendation: a.** It follows Relay's refusal precedent and breaks no saved workflow.
Also, the worker's output assembly (`worker.py:204`) reads `['text']` from every Response in
the values. A Response value without `text` then raises `KeyError` and the run reports "Run
failed unexpectedly." With (a) the refusal never reaches that line. As a guard for any other
recovered Response, the assembly raises a named `ValueError` instead
(`"{id}: the Response produced no answer."`). The validator's acceptance of `route` with no
error edge on a terminal node is a separate validator issue (section 9).

## 5. Changes

### 5a. One strict source contract (`backend/app/platform_nodes.py`)

Next to the existing `canonical()` (line 28):

- `source_list(value)`: the only accepted container is a list of objects, given as a list or
  as a JSON string that decodes to a list. Every entry must be an object with a non-empty
  string `id`. Anything else (`"{}"`, `null`, a string, a number, a non-object entry, a
  missing `id`) raises `ValueError('Saved knowledge sources are malformed.')`. A valid empty
  list is the only input that needs no check.
- `published_batches(sources)` (pure): canonical form (drop `citation` and `cited`);
  remove **exact** copies only (the same chunk under two labels has identical canonical
  metadata); split the rest into batches of at most 120 in which no `id` repeats. Two
  entries with the same `id` and **different** metadata both stay and go to different
  batches, so each is checked.
- `async def verify_published(ctx, node_id, sources)`:
  - an empty list: return, no call;
  - non-empty and `ctx.platform is None`: raise
    `UnsafeExecutionError(f'{node_id}: knowledge source verification is unavailable. No answer was published.')`;
  - otherwise call `ctx.platform('verify_vector_sources', batch)` for each batch;
  - `CONTROL_FLOW` members are re-raised first and unchanged (`UnsafeExecutionError` is a
    `ValueError`, so the order matters); `KeyError`, `ValueError` and `TypeError` from the
    verifier become
    `UnsafeExecutionError(f'{node_id}: a knowledge source behind this answer is unavailable or was removed. No answer was published. Start a new run with current evidence.')`.

### 5b. The fresh Response (`backend/app/registry.py`, `response_node`)

After `ground_answer`, call `verify_published(ctx, ctx.node_id, sources + reused)` and then
return. `sources` is the displayed list; `reused` is the saved dependencies of every reused
loop item in this attempt (5f), through `source_list()`. Both go through the same
canonical form, exact de-duplication and batches. The text, the displayed source list, the
order and the `cited` flags do not change: `reused` is checked, not displayed.

### 5c. Restore (`backend/app/platform_validation.py`, `kb_errors`)

New optional argument `progress` (the run's `loop_progress`). All saved source containers
go through `source_list()` before any batching or empty shortcut.

1. Retrieve and Query checkpoints: as today, but through `source_list()` (closes the same
   `"{}"`-as-empty weakness on this branch).
2. **New:** a `response` checkpoint: its saved `sources` through `source_list()`, then
   `services.verify` for each `published_batches()` batch.
3. Dependencies: an owner is checked when it is a checkpoint key (today) **or** it is a
   reused loop item (D2): `<loop id>:<index>` where `<loop id>` is a `for_each` node of this
   workflow and either `<loop id>` is a checkpoint key or `str(index)` is in
   `progress[<loop id>]`. The saved list goes through `source_list()` and the existing
   120 limit (the store already merges by `id`).

A failure adds the existing restore error for that node or owner. The resume is refused
before any work is reused.

### 5d. Callers pass the loop progress

- `worker.py:109` (preflight before the graph runs): pass `run.get('loop_progress')`.
- `worker.py:200` (`validate_cached`, when a cached node is reused): pass it too, so a
  restored loop checks its items again at the moment of reuse.
- `main.py:189` (the resume endpoint): pass `previous.get('loop_progress')`. Both run
  documents come from `storage._document`, which merges `run_loop_items` into
  `loop_progress`.
- `worker.py:202` (`compile_workflow`): pass `saved_dependencies=run.get('vector_dependencies')` (5f).
- `worker.py:204`: the named error for a Response value without `text` (D3).

### 5e. Docs (`docs/operations.md`)

One passage: the Response checks every listed source, and the saved sources behind reused
loop work, before it publishes; a resume checks the saved sources of every reused node and
loop item; the refusal fails the run under any `on_error`; a resumed loop's citations can
show as `[unsupported reference]` (section 9).

### 5f. Reused dependencies reach the publication check (`compiler.py`, `iteration.py`)

No new store, schema, model call or citation parsing. The run already saves each loop
item's verified sources under `<loop id>:<index>` (`storage.record_vector_dependencies`).

- `compile_workflow(..., saved_dependencies=None)`: the run's `vector_dependencies` as
  loaded at claim, kept in the run state. A reused item does not run again in this attempt,
  so its saved list cannot change during the attempt.
- Two reuse points record the owner in a run-state set `reused_owners`:
  1. the compiler's cached path, when a `for_each` checkpoint is restored (after
     `validate_cached`): every saved owner `<loop id>:<integer>`;
  2. `iteration.for_each_node`, when an index from `completed_indices` is reused:
     `item_owner(ctx, index)`.
- The Response reads the saved lists of `reused_owners` (5b). An owner with no saved list
  adds nothing; a malformed list is refused by `source_list()`.
- Fresh loop items in the same attempt are not in `reused_owners`: their passages enter the
  shared registry and the displayed list, as today.

## 6. What does not change

- Which sources a Response lists, their order and their `cited` flags.
- The Agent and Query checks, their error texts, and the evidence registry.
- The dependency store and its 120 limit; no schema change.
- Run history: events already stored keep what was retrieved at that time (section 9).

## 7. Tests (write first)

D fails on `812fefd` and passes after the fix; C passes on both. Model calls use the real
`llm_node` with a substitute provider. Real-KB tests use the `services` fixture and
`populated()`, removal through `remove_document`, and the real Worker with SQLite. An
unrelated document keeps the knowledge base searchable after a removal.

**Fresh Response**

| # | Kind | Test |
|---|---|---|
| U1 | D | In memory, nested: the specialist's check fails (T14 nested scenario). No `out` success event, `out` not in the values, the run fails with the Response refusal. |
| U2 | D | Real KB and Worker (the audit's scenario): the document is removed during the specialist's last call. Run status `failed` with the refusal; no `out` checkpoint; empty run output. |
| U3 | D | Removal after an upstream check passed (shared run evidence): an Agent's answer verifies, then the document is removed before the Response. Refusal. Also with a Query upstream. |
| U4 | D | Budget fallback sites (step, refused repair) with a removed source: the Agent fails (T14) and no Response is published. |

**Restore and reused loop work**

| # | Kind | Test |
|---|---|---|
| U5 | D | Real KB, Worker: `Input → Jira list → For each (Query body) → Prompt → Response`. Crash after the **loop** checkpoint, before Prompt/Response. Remove the document. Resume is refused with the restore error naming `<loop id>:0`; no `out` event; no new model call (provider calls unchanged). This is the review's reproduction. |
| U6 | D | As U5, but crash after item 0's progress row and before the loop checkpoint (partial item progress). Resume is refused naming `<loop id>:0`; item 0 is not reused. |
| U7 | D | Crash after the `out` success event (Response checkpoint saved), remove the document, resume. Refused with the restore error; the saved source is not republished. |
| U8 | D | Both resume entry points: the resume endpoint (`main.py`) returns 422 with the error, and the worker preflight fails the run, for U5's state. |
| U9 | C | Unchanged-source control for U5, U6 and U7: no removal; resume succeeds; no model call repeated. It does **not** assert the same output and sources as an uninterrupted run: on the baseline a resumed loop's `[S1]` becomes `[unsupported reference]` and the Response lists no source (section 9). The test records that difference. |
| U22 | D | The R2-01 sequence (real KB, Worker): crash after the loop checkpoint; resume with the document present (resume checks pass, the loop is reused); the Prompt commits its success event; then the document is removed, before the Response runs. Run `failed` with the named refusal; no `out` success event or checkpoint; empty run output; provider calls stay 1 across both attempts. |
| U23 | D | As U22, but the first attempt stops after item 0's progress row and before the loop checkpoint. On resume, item 0 is reused from progress and item 1 runs fresh. Items 0 and 1 retrieve **different** documents, so item 1's fresh evidence cannot cover item 0's; the removed document belongs to item 0. Same refusal, no `out` checkpoint, empty output; provider calls are **2** in total (one per item), and item 0 is not repeated. |
| U24 | D | Unchanged-document control for U22 and U23: the run succeeds with no repeated model call (1 and 2 provider calls), and a Response verifier call contains item 0's document specifically (the baseline makes no Response call). |

**Contract**

| # | Kind | Test |
|---|---|---|
| U10 | D | Non-empty sources and no verifier (`platform_resolver=None`, evidence in the registry): refusal "verification is unavailable". |
| U11 | C | Empty source list and no verifier: the Response succeeds, no call. |
| U12 | D | Malformed restored containers for a Response checkpoint, a Query checkpoint and a loop item dependency: `"{}"`, `null`, a string, a number, a list with a non-object entry, an entry without `id`. Each gives a restore error; none is treated as zero sources. |
| U13 | C | Exact duplicates: the same chunk retrieved twice (two labels), real verifier. Succeeds; the published list keeps both labels. |
| U14 | D | Same `id`, different metadata (one record's `text` changed), real verifier, both orders: both records are checked and the run is refused. Kills N7. |
| U15 | D | More than 120 distinct listed sources: every call has at most 120 and no repeated `id`, **and** the union of the batches equals the distinct canonical set (complete coverage). |
| U16 | D | The Response makes exactly one check per distinct source (the baseline makes none). |
| U17 | C | Fresh success output unchanged: same text, sources and `cited` flags as the baseline for the same run. |
| U18 | C | No evidence: no verifier call. |
| U19 | D | Control flow: `LeaseLost` and cancellation raised by the verifier during the Response check propagate unchanged. |

**Recovery policy (D3)**

| # | Kind | Test |
|---|---|---|
| U20 | D | U3's removal with the Response at `on_error` `fail`, `continue` and `route`: each run is `failed` with the named refusal (never "Run failed unexpectedly"); no `out` value, checkpoint or output; no retry. |
| U21 | D | A Response value without `text` (a recovered Response forced by a substitute handler under `continue`): the run fails with "out: the Response produced no answer.", not "Run failed unexpectedly." |

### Existing tests that must change, and why

| Test | Change |
|---|---|
| `test_token_budget_recovery.py::test_t14_a_removed_source_fails_a_nested_specialist_budget_abstention` | Assert the final Response publishes nothing (it now fails closed) instead of a successful run. |
| `test_token_budget_recovery.py::test_t10c…`, `test_t14b…` | Their verify-call counts include the Response check. The Agent's "exactly once" stays asserted on the Agent's own call. |
| `test_agent_grounding.py::test_invented_citations_are_marked_and_cited_sources_reported` | `verified` gets the Response check as a second entry. |
| `test_loop_event_bounds.py::test_events_emitted_through_the_context_carry_it_too` | Its test platform needs a `verify_vector_sources` branch. |

The planning probe (`prototype-run.txt`) found these, plus the three T7b/T7c cases that exact
de-duplication fixes. The strict contract (5a) can find more tests that hold evidence with
no verifier; each one found is listed with its reason in the evidence README.

## 8. Mutants (scratch copies only)

| Mutant | Must be caught by |
|---|---|
| N1: remove the Response check | U1, U2, U3, U16 |
| N2: check only cited sources | U1, U3 |
| N3: no de-duplication | U13 |
| N4: one call for all sources (no batching) | U15 |
| N5: restored Response checkpoint not checked | U7 |
| N6: a catch-all that turns `LeaseLost` into the refusal | U19 |
| N7: de-duplicate by `id` alone | U14 |
| N8: reused loop items not checked (owner must be a checkpoint key, today's rule) | U5, U6 |
| N9: only checkpointed loops checked (`progress` ignored) | U6 |
| N10: skip the check when there is no verifier | U10 |
| N11: lenient container (`"{}"` decodes to zero sources) | U12 |
| N12: the refusal raised as a plain `ValueError` (recoverable) | U20 |
| N13: resume endpoint does not pass `loop_progress` | U8 |
| N14: reused dependencies checked only at resume (preflight and loop reuse), not at the Response | U22, U23, U24 |
| N15: only a restored loop checkpoint records `reused_owners` (an item reused from progress does not) | U23 |

## 9. Limits and out of scope

- **Check-to-publish window.** The check runs before the Response returns. A removal after a
  source's batch was checked and before the checkpoint commits is not caught (CWE-367). The
  window includes the remaining batches and the checkpoint write, so no time bound is
  claimed. Query and Agent have the same window. Paths 3 and 4 in section 1 are not this
  window: the resume check covers path 3, and the publication check of reused dependencies
  (5f) covers path 4.
- **Citations of reused loop results (existing behavior).** A resumed loop's result text
  keeps `[S1]`, but its passage is not restored into the shared registry, so the Response
  shows `[unsupported reference]` and lists no source. The answer is not wrong in its
  source authority (5f checks it); it is less well cited. Restoring evidence and labels for
  reused loop results is a separate change. R01 does not depend on it.
- **D2 cost.** A resume can be refused for a removed source in a reused item that the
  Response does not read.
- **Run history.** Finished runs and intermediate events (for example a retrieval's own
  success event) keep the passages retrieved at that time. Whether removal must propagate to
  stored runs is a data-retention decision for its own step.
- **Validator:** `on_error: route` on a terminal Response is accepted although no error edge
  is allowed. D3 makes it irrelevant for this refusal; the validator rule is a separate fix.
- TA02, TA03, TA05–TA07 and the accounting snapshot are not changed.

## 10. Closing checklist

R01 is closed when each line has a passing test:

1. A fresh Response never publishes a source that fails the check: nested failure (U1, U2),
   upstream check passed (U3), budget fallback sites (U4).
2. A resume never reuses work whose saved sources fail the check: after a loop checkpoint
   (U5), after partial item progress (U6), after a Response checkpoint (U7), at both entry
   points (U8); with the unchanged-source control (U9).
3. A Response built on reused loop work checks the reused items' saved sources at
   publication, after a full loop checkpoint (U22) and after partial progress (U23); with
   the unchanged-document control (U24).
4. Missing authority and malformed containers fail closed (U10, U12); a valid empty list
   needs no verifier (U11).
5. Duplicates, conflicting metadata and large lists are checked completely (U13–U16);
   unchanged runs publish exactly as before (U17, U18).
6. Control flow is never converted into a source failure (U19); the refusal fails the run by
   name under every `on_error` (U20, U21).

Then: backend `PYTHONHASHSEED` 0–4, the isolation copy, the deployed scenario gate, and
hosted CI on the pushed commit. The audit's `test_real_source_boundary.py` and the review's
four candidate scenarios (`evals/results/r01-plan-review-65a7774/`) and the revision-2
review's scenarios (`evals/results/r01-plan-r2-review-e50fa65/`) pass or close as stated.

**Size:** one commit. Seven application files (`platform_nodes.py`, `registry.py`,
`platform_validation.py`, `worker.py`, `main.py`, `compiler.py`, `iteration.py`), about 90
changed lines, plus tests and docs.

## 11. Changes from revision 1 (`65a7774`)

| Review item | Change |
|---|---|
| R01-P1: reused loop work published with no check | D2; 5c item 3; 5d; tests U5, U6, U8, U9; mutants N8, N9, N13. |
| R02-P2: no verifier and malformed containers | 5a `source_list()` and the no-verifier refusal; 5c applies it to every restore branch; tests U10–U12; mutants N10, N11. |
| R03-P2: `continue` gave "Run failed unexpectedly"; `route` claim | D3 (refusal ignores `on_error`, named guard at `worker.py:204`); the unsupported route claim is removed; tests U20, U21; mutant N12. |
| N7 equivalent | Replaced by `id`-only de-duplication (N7) with the conflicting-metadata test U14. |
| D/C labels | The new-call assertions are D (U15, U16, U19); U15 asserts complete coverage. |
| "Milliseconds" | Removed; the window is described without a time bound. |

## 12. Changes from revision 2 (`e50fa65`)

| Review item | Change |
|---|---|
| R2-01 (P1): reused-loop dependencies never reach the publication check | Section 1 path 4; D2 checks the same set at resume **and** at publication; 5b checks displayed sources plus reused dependencies; new 5f carries the saved dependencies and the reused owners into the run state; 5d passes them from the worker; tests U22–U24; mutants N14, N15. |
| U9 exact replay equality fails on the baseline | U9 narrowed to completion with no repeated model call; the citation difference is recorded in U9 and in section 9 as existing behavior, separate from R01. |
| Refusal text | "a listed knowledge source" becomes "a knowledge source behind this answer", because the check now covers dependencies that are not displayed. |
