# A5.3 revised design review — 2026-11-06

Reviewed commit: `d0dfb6dfcb1f5aa13b0c0bce4e4a00f369e4072d`.
Design: `docs/superpowers/specs/2026-10-24-parallel-branches-design.md`, revision 2.
Evidence: `evals/results/a53-design-r2-review-d0dfb6d/`.

## Verdict

Request changes before implementation. Keep the three selected decisions: policy A, backend Part 1, and a run-attempt accounting lock. The main defect is the clock used to select the newest snapshot. Three smaller clarifications are needed in the concurrency tests, graph boundary and editor compatibility contract.

The design is already committed, despite the handoff saying it is not. This review changes no application code or design text. Its executable probes model the proposed behavior using current components; they are not evidence that an implemented parallel Relay feature already has these bugs. Relay still refuses forks today.

| ID | Severity | Finding | Required revision |
| --- | --- | --- | --- |
| A53-R2-01 | P1 | Existing accounting generation does not advance for action-only changes | Specify a clock for every full snapshot publication, separate from the model-spend acknowledgement generation |
| A53-R2-02 | P2 | A01/A02 require a second commit while the first holds the mutex | Make B publish and wait; release A before expecting B's commit |
| A53-R2-03 | P2 | “Inside the region” does not explicitly exclude an outside edge into a branch | Require each branch node's total incoming edges to come only from its expected predecessor |
| A53-R2-04 | P2 | Hiding Join does not give the existing editor the new binding semantics | State an API-only editing boundary, or add minimal Join binding parity; list the mechanical registry-fixture change |

## A53-R2-01: snapshot freshness needs an action-aware clock

Design lines 226–234 select a snapshot only when its generation is newer, then acknowledge the committed generation. The latter acknowledgement rule is correct, but the former cannot use the existing generation as written.

`AccountingDurability.generation` starts at zero. `registry.account_usage` advances it (`backend/app/registry.py:192`); `_debit_action` changes the action counter, remaining budget and charged keys without advancing it (`backend/app/execution_policy.py:194–209`). Thus two action-only operations produce two distinct snapshots at generation zero.

The probe calls the real `execute_with_policy` and `Store.reserve_accounting`, applying the draft's strict-generation selection in a small persistence callback:

| Policy | Runtime actions | Stored actions | Stored remaining, from 10 | Model generation |
| --- | ---: | ---: | ---: | ---: |
| Accept only a newer existing generation | 2 | 1 | 9 | 0 |
| Order all snapshot publications | 2 | 2 | 8 | 0 |

This is a deterministic action refund under the literal proposed selection rule. It needs no provider call and no concurrency. If “generation” means a new full-accounting clock instead, define it explicitly; the current class is not that clock.

Smallest correction while keeping the worker-scoped serializer:

1. Synchronously publish a frozen triple `(publication_sequence, spend_generation, snapshot)` before any await or lock wait.
2. Advance `publication_sequence` for every newly produced full accounting snapshot, including action-only changes. Preserve it when an already-published request waits or is retried; an older snapshot must not receive a fresh sequence later.
3. Under the mutex, choose the newest publication and commit its snapshot.
4. Acknowledge only that frozen publication's `spend_generation` after a confirmed commit. Never acknowledge whatever generation happens to be current when the thread returns.

A unified version is also possible, but then every relevant mutation must advance it, including action/deferred updates, and the implementation change map must include those producers. Do not silently reuse the model-only generation.

Strengthen A01 with action-only, equal-token snapshots and assertions on the counter, remaining budget and charged keys. Preserve TA03: coalescing the accounting snapshot must not coalesce event identities, invocation-scoped outcome usage or cancellation commit receipts. A02 can check both events and their non-overlapping usage rows without adding a ledger.

## A53-R2-02: fix the test schedule, not the lock

At design line 313, A's database commit is held while B “reserves and commits.” A02 reuses this schedule. With the proposed mutex, B cannot commit until A finishes and releases the lock. Waiting for B's commit before releasing A deadlocks the correct implementation.

The lock probe confirms the intended schedule: B publishes while A holds the mutex, B has not committed, A is released, and the commit order is A then B. This matches Python's documented exclusive lock behavior. [Python 3.12 synchronization primitives](https://docs.python.org/3.12/library/asyncio-sync.html#asyncio.Lock).

Rewrite the cases to observe B's publication/save request and its wait behind the serializer, assert that it has not entered the database commit, release A, then await both commits and check the final snapshot. Use observable barriers at those boundaries; merely checking an unfinished task immediately after scheduling it does not prove serialization.

Give M10 an explicit stale queued-snapshot oracle. A mutex plus a caller snapshot does not necessarily fail if the test only feeds fresh snapshots in FIFO order. Retain M14's independent check that a newer usage update arriving during a commit remains pending until its own snapshot is confirmed.

The cancellation ownership rule in the draft is sound. A shield protects the inner task while its caller can still receive cancellation; therefore the protected writer must own the mutex through thread completion, rather than the cancelled waiter owning it. [Python 3.12 shielding from cancellation](https://docs.python.org/3.12/library/asyncio-task.html#shielding-from-cancellation).

## A53-R2-03: make the fork region closed to side entries

Design lines 138–139 require one incoming and outgoing edge “inside the region.” State explicitly that a branch node cannot also have an incoming edge from outside that region. Otherwise this shape fits an internal-edge-only interpretation:

```text
Input → Condition.true → Fork → A ─┐
                              → B ─┴→ Join → Response
        Condition.false ──────→ A
```

On the actual pinned LangGraph, the true route runs A, B, Join and Response. The false route runs A only. The graph then returns normally without running Join or Response, because B was never scheduled. This is a raw LangGraph topology probe, not a claim that current Relay validation accepts the shape.

Require every branch node's total flow predecessors to equal its expected predecessor, with no outside entry, and require every branch to exit only to its designated Join. Keep the existing same-fork rule for the Join itself. Add this counterexample to the existing topology cases; no new routing feature is needed.

## A53-R2-04: distinguish a hidden palette entry from editor support

Design lines 282–285 correctly say the fixed palette map hides Join. However, the editor calls its own `bindableOutputs` (`frontend/components/workflow-editor.tsx:891`), and that function still intersects the arrivals (`frontend/lib/workflow.ts:441`). It does not know that a Join guarantees all branches ran.

The probe imports that real frontend helper with the proposed fork/A/B/Join graph. After the Join it offers `input.message` and `fork.text`; `a.text` and `b.text` are missing. This contradicts the new backend binding rule even if the canvas can draw an API-created Join.

Choose and state one bounded Part 1 contract:

- Add only the Join binding calculation and a shared binding fixture, leaving Join hidden and the full editor work in Part 2; or
- Keep Part 1 strictly backend/API-only, explicitly document that editing these workflows is unsupported until Part 2, and gate that editing path if API-created graphs are exposed in the editor.

Do not imply full editor compatibility merely because the node renders. The probe proves missing binding options, not data loss.

Also list the mechanical update to `frontend/tests/browser/request-nodes.json`: `backend/tests/test_request_examples.py:34` compares it with every registered definition. Registering Join changes this fixture even with no editor feature changes. If binding support is included, extend the existing `bindable-outputs.json` parity fixture and its backend/frontend tests rather than creating another contract.

## Comparison with published systems

Policy A is supported by the cited prior art. Claude Managed Agents checks its shared budget between model requests and permits the in-flight request on each thread to finish. Its documented model-spend overrun is bounded by one request per thread. That supports Relay's selected admission policy, rather than requiring strict reservation. Claude measures list-price dollars; Relay measures provider-reported known tokens, so the analogy does not establish bounds for unreported usage or retry billing. The draft already records those exclusions. [Claude session budgets](https://platform.claude.com/docs/en/managed-agents/budgets).

The lock is also a reasonable adaptation: Temporal's Python documentation explicitly uses `asyncio.Lock` to serialize concurrent handlers that share state. That supports serialization, not the draft's unproven freshness clock or database atomicity; Relay's lease fence and TA02 transaction still supply those guarantees. Prefer this direct source over the asyncio event-loop blog for the lock claim. [Temporal workflow message passing](https://docs.temporal.io/develop/python/workflows/message-passing#use-asynciolock-to-prevent-concurrent-handler-execution).

Keep read-only branches, memory-agent refusal, explicit wait-for-all joins, bounded fork width and the existing cancellation receipts. They are proportionate to Part 1. No ledger, service, schema migration, historical backfill or strict reservation is needed for these review corrections.

## Verification and limits

- `probe_design.py`: exit 0, deterministic generation/control results, mutex order, and both LangGraph side-entry paths.
- `probe_editor.mjs`: exit 0, actual frontend binding helper and hidden palette result.
- Current baseline: `test_compiler`, `test_accounting_durability`, `test_usage_attribution`, `test_execution_policy`, `test_request_examples`: **87 passed**.
- Thirteen input hashes are recorded in `preserved-inputs.json` and rechecked at review completion. Original October evidence and builder evidence were not executed in place or edited.
- No full suite, frontend build, PostgreSQL process gate, hosted CI check or implementation mutation campaign was performed for this design review. The probes do not substitute for the design's eventual SQLite/PostgreSQL crash and cancellation gates.

After these revisions, proceed directly to the bounded tests-first Part 1 implementation. Make the collected-case allocation explicit so PostgreSQL and topology variants fit the promised 28-case budget; do not add a separate planning round solely for that allocation.
