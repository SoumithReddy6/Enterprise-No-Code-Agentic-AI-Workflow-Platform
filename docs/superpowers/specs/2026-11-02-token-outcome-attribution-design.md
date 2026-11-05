# TA03: non-overlapping usage on invocation outcomes

**Status:** approved for tests-first implementation after the November 3 review.
**Date:** 2026-11-02.
**Baseline:** `74ad48a630694d597dbef2ef80fbdf9eb1ca6804` (TA02 implemented and reviewed).
**Scope decision:** the user selected correcting new-run behavior and explicitly excluded
historical metrics. No historical rewrite, backfill, migration or run deletion is included.
The user may delete old runs separately; this work does not do so.
**Builder check:** a fresh read-only reviewer reported "nothing important left" before
handover. This is planning-readiness feedback, not an implementation or correctness proof.

## 1. Problem and goal

The cumulative accounting snapshot and the usage on an outcome answer different questions:

- Snapshot: all known usage in the run, for durable budget enforcement.
- Outcome: only the usage incurred by that invocation during this execution attempt.

The compiler's cancellation and two exceptional-failure emitters publish the checkpoint
owner's inclusive total. A specialist's usage can therefore appear in its own event and
again in the parent's event. SQL sums both, although the authoritative budget is correct.

Observed: parent spends 15, specialist spends 15 and succeeds, parent is cancelled during
its next request. Accounting saves 30; metrics report 45 and three measured calls instead
of 30 and two. The interrupted request's unknown consumption is not treated as zero.

Changing only the compiler cancellation event is unsafe. If cancellation instead occurs
while a specialist is active, that specialist currently unwinds without an outcome. The
cancellation-only counterfactual reports 15 rather than the 30 known tokens. Both problems
must be fixed together. Evidence: `evals/results/ta03-design-2026-11-02/`.

**Goal:** published outcome usage forms a non-overlapping partition of known usage for
cooperative execution and cancellation while Relay owns the lease and can save outcomes.
Nested invocations publish their own usage; parents do not republish descendants' usage.
No new model calls, changes to action charging, or changes to business decisions result.

## 2. Primary sources and adaptation

| Published source | Documented behavior | Adaptation to Relay |
|---|---|---|
| [Genkit OpenTelemetry conventions](https://genkit.dev/docs/observability/otel-genai-semantic-conventions/) | Model operations carry their own spans and response-reported usage. | Attribute consumption to the operation that incurred it. Reuse Relay's existing invocation counters; do not install Genkit or OpenTelemetry. |
| [LangSmith cost tracking](https://docs.langchain.com/langsmith/cost-tracking) | The trace tree presents child usage, parent aggregates and the trace total. | A parent rollup is a view, not additional consumption to add to its children. |
| [OpenTelemetry GenAI observability example](https://opentelemetry.io/blog/2026/genai-observability/) | Agent traces contain model/tool spans, with token attributes on model calls. | Preserve the distinction between orchestration structure and measured model consumption. The evolving convention names are not a dependency of this correction. |

These references support the attribution principle. Relay's outcome-based adaptation and
its cancellation/commit mechanics are our design, not a claim about those systems' internals.
Invocation totals may aggregate several model calls, including an explicit repair. They
are not a new physical-call ledger and do not resolve provider-internal retry accounting.

## 3. Approaches

| Approach | Trade-off | Decision |
|---|---|---|
| Consistent invocation outcomes, including cancellation unwind | Fits current counters, event storage and SQL; requires completing nested lifecycle handling | Selected focused approach |
| Separate durable records for every physical provider request | Stronger foundation for future reconciliation and exact call identity; expands schema, lifecycle and durability work | Deferred |
| Replace only the parent's cancellation total | Small edit, but drops known child usage while the child is active | Rejected by the diagnostic counterfactual |

No repair of previously stored events or `run_token_usage` rows is attempted. Existing
definitions continue to describe reported usage, not invoice totals or estimated unknowns.

TA06 can use the existing per-invocation counters to record calls that return no usage
report while keeping their token consumption unknown. A durable record for each provider
call remains a separate decision.

## 4. Required rules

1. Use `execution_policy.usage_for(run, identity)` for outcome usage. Never fall back to
   the checkpoint owner's `run['usage']` for an event.
2. Apply the rule to success, failure, pause and cancellation at all three invocation
   boundaries: graph node, agent-attached target and loop-item body.
3. A frame with known usage publishes it when cancellation unwinds through that frame.
   A frame with no model usage emits no inherited usage. Parent cancellation does not
   discard an active child's known usage or inherit a completed child's usage.
4. One acknowledged terminal or paused outcome per invocation per worker attempt carries
   that invocation's accumulated usage. Retry notices carry no usage. Cached checkpoints,
   completed loop items and stored write results add no model usage when replayed.
5. Acknowledgment lost to task cancellation is distinct from failure to commit. A terminal
   event saved by the shielded Worker is not counted again as cancellation of that same
   invocation. Its parent is a different invocation and must still report its own usage.
6. Provider/model breakdowns must also be disjoint; a correct grand total alone is not
   sufficient. Two calls to the same specialist are distinct invocation identities.
7. TA02's snapshot is still authoritative. Capture it at every emitted outcome and retain
   the atomic outcome/accounting commit, exact-generation acknowledgment and monotonic guard.
8. Preserve control flow. Approval still pauses; an unsafe execution, accounting refusal,
   lease loss or uncertain write never becomes a tool observation, retry or recovery.
   Bookkeeping never grants permission or starts more model calls.

## 5. Proposed implementation behavior

### 5.1 Shared usage construction

Use one small outcome-construction helper in `execution_policy.py` to attach a detached
`usage_for` value, or omit usage when none exists. Callers still own their outcomes; the
retry policy continues to emit retry metadata only. The helper does not charge, persist,
reset counters, subtract child totals or infer calls from event text.

Use it at existing outcome emitters in `compiler.py`, `agent_runtime.py` and `iteration.py`.
Normal outcomes must keep their current meaning. Remove the compiler's inclusive reads
from cancellation and the unsafe/uncertain failure paths. Snapshot code retains its
cumulative reads; this is not a global replacement of `run['usage']`.

### 5.2 Complete nested cancellation and refusal unwind

Add cancellation handling before the attached-target control-flow catch and at the loop
body boundary. Each active invocation publishes its own cancelled usage, then re-raises.
Cancellation is not an item failure: do not append a completed item, continue the loop,
retry the child, produce a fallback, or make the interrupted result reusable.

For usage-bearing failure/refusal paths, use the same invocation rule at every boundary.
An unsafe execution, accounting refusal or uncertain write after known spend can publish
a failed outcome while the lease/storage allow it, then propagates with its existing
control-flow semantics. Do not introduce a failed outcome at claim-time legacy/malformed
accounting checks, which run before any node. A save refused by the lease fence surfaces
as `asyncio.CancelledError`. Unwinding frames may attempt their own outcome saves, and
the same fence rejects those attempts before they can persist usage or accounting.
Keep the existing exception for a refused save.

Approval handlers retain frame capture and pause semantics; their own usage is exclusive.
The same counters are never published as both a pause and a cancellation of the same
invocation after its pause event has already committed.

### 5.3 Cancellation after an outcome commit

Extend the existing `PersistedNodeCancellation` mechanism into an internal receipt naming
the invocation whose outcome committed. The Worker can derive the identity from an attached
event's `invocation_id`, or the graph event's `node_id` for a root invocation. No identity
is supplied by a model, and no public event field is required solely for this receipt.

Use this receipt for cancellation observed after any saved usage-bearing outcome, including
transient child success/failure/cancellation and paused outcomes. Catchers suppress another
usage-bearing outcome only when the receipt matches their own invocation. A child receipt
does not suppress the parent's own cancellation report. At the graph boundary, normalize
back to the base `asyncio.CancelledError` as required by the existing timeout behavior.

The receipt means the storage thread returned a confirmed successful write. It must not
be generated for a false fence acknowledgment, a failed write, or an unconfirmed commit.
It does not mean a transient specialist result became a graph checkpoint. Preserve the
existing root-checkpoint cancellation guarantee and the pending-spend barrier.

The builder may move the class to a shared module to avoid circular imports, retaining
the existing compiler import as a re-export for compatibility. Helper/class names and
minor local factoring are builder decisions; the identity matching rule is required.

### 5.4 Resume, SQL and public behavior

Keep `Store._event`, `RunTokenRecord` and SQL aggregation unchanged. Their inputs become
disjoint, rather than attempting to deduplicate by node name, model name or token amount.
Keep per-attempt usage empty at compile time while restoring the cumulative baseline.

An invocation resumed after approval may keep its logical id, but only new model work in
that worker attempt is reported. Saved specialist/frame/write results do not manufacture
new usage. An interrupted unsaved model operation that executes again is new work.

A loop body's usage-bearing outcome and its `loop_item` progress row commit separately.
If cancellation lands after the outcome commits and before progress commits, resume runs
the item again. Report usage from both executions because both made provider calls.

No schema migration, historical marker, metrics backfill, new store or new service is
needed. The UI may show a nested cancelled outcome it previously omitted; existing
statuses and identifiers remain valid.

## 6. Verification and stop rule

Budget: at most **16 new collected cases**, including parametrization, and **8 mutants**.
Reuse existing operation/approval/TA01/TA02 fixtures and real Worker/Store. Synthetic
provider responses supply usage; no live provider charges or new testing framework.

Use the P2 review tier. Start with failing tests from this design, implement the fix,
then conduct one implementation review. This section supplies the implementation's test
budget, verification gates and stop rule.

Required coverage, folded into that budget:

- Original reproduction: completed specialist plus cancelled parent, totals 30 not 45.
- Cancel inside an active specialist after known spend; no missing child usage.
- Cancel before a child reports any usage; do not inherit parent usage or estimate unknowns.
- Cancelled response with known usage, including a loop body and a deeper specialist.
- Cancellation held at a child outcome commit and a root outcome commit; each acknowledged
  usage appears once, and saved root checkpoints remain reusable.
- A fenced save refusal during nested work leaves the durable usage rows and saved
  `tokens_spent` at their pre-refusal values, even if unwinding frames attempt saves.
- Cancel between a loop body's outcome commit and its progress commit, then resume. Verify
  the item runs again and metrics include both executions' measured calls and tokens once.
- Unsafe/uncertain exceptional failures and a nested accounting refusal after paid work:
  exact usage, original stop semantics, no fallback or further provider request.
- Approval pause/resume, repeated specialist calls and at least two models; verify each
  provider/model row, measured-call count and cumulative saved budget.
- Normal success, retry metadata, cached checkpoint/loop replay and a non-model tool
  remain correct. Reuse existing tests where they already prove the requirement.

Baseline defect tests must fail for the intended metric mismatch; missing new interfaces
are not accepted as proof. Controls retain existing expectations. Mutants must cover:
parent-inclusive cancellation; omitted child cancellation; omitted loop cancellation;
inclusive exceptional failure; ignoring an already-saved child outcome; suppressing a
parent because a different child's outcome committed; inherited usage on a non-model
invocation; usage added again on replay or retry metadata.

Run focused tests, full backend seeds 0–4, clean-copy isolation, the existing deployed
scenario gate, and five hosted CI jobs on the pushed commit. Preserve TA02's 24 tests and
the existing deadline/event-commit cancellation tests. Use controlled event barriers,
not short elapsed-time assertions. No new SIGKILL harness or live-model campaign is
required for this cooperative cancellation attribution fix.

TA03 closes when these bounded tests and mutants pass, the original and active-child
reproductions agree with known response usage, per-model totals agree, TA02 is unchanged,
and hosted CI is green. No historical-data work is a closing condition.

## 7. Limits

This does not guarantee telemetry for an unreported provider response, SIGKILL before
durable publication, failed/unconfirmed database commit, or writes rejected after lease
loss. It must not duplicate or estimate usage to hide those gaps. The authoritative
snapshot and outcome metrics still serve different purposes; metrics are not a recovery
ledger. Unknown usage, provider-internal retries, strict token reservations, provider
cache categories and concurrent settlement stay in their separate accounting findings.

## 8. Expected change surface

`execution_policy.py` (exclusive outcome helper/shared cancellation receipt if needed),
`compiler.py` (graph outcomes and receipt matching), `agent_runtime.py` (attached outcomes),
`iteration.py` (item outcomes), `worker.py` (confirmed-commit receipt), a focused test module,
targeted existing cancellation tests only if an internal interface changes, and
`docs/operations.md`. No provider adapter, token-budget rule, business authorization,
approval persistence transaction, storage schema, SQL metric query or frontend change.
