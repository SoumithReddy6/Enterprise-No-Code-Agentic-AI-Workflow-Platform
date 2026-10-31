# TA02: durable accounting for reusable results

**Status:** proposed design for approval; no application changes.
**Baseline:** `f3e09238de9eb822713d50f2e5a3170ef25dc5f0` (R01 closed).
**Chosen approach:** the user selected atomic result/accounting commits, keeping TA02
separate from a new per-model-call ledger. The user also selected blocking further model
calls for unverified older runs while retaining their saved results. The full design below
is still proposed for approval; those two choices do not imply approval of every detail.

## 1. Problem and goal

The current Worker writes the answer/checkpoint and cumulative accounting in separate
transactions. Accounting writes are best-effort so an already-paid answer is retained.
If those writes fail and the worker stops after the checkpoint commit, resume reuses the
answer but restores an empty accounting baseline. More model spending is then allowed.

The original auditor reproduction still holds on the baseline:

```text
limit: 15 tokens
first response: 15 tokens; checkpoint saved; accounting writes fail
interruption immediately after the checkpoint commit
resume: first answer reused, second response costs 15 more tokens
final known provider usage: 30; saved accounting: 15; status: success
```

Evidence: `evals/results/ta02-design-2026-10-31/reproduction.json`. The probe uses real
provider adapters, Worker and SQLite, with synthetic HTTP responses and a commit barrier.
It calls only the original probe's TA02 function; original evidence is not overwritten.

**Goal:** when Relay durably saves work that can be reused, its known cumulative usage is
durable in that same commit. Resume neither replenishes that allowance nor repeats a saved
paid answer. If a trustworthy baseline cannot be established, new model spending stops.

This is a correctness fix for known usage, not a claim of billing-grade completeness or a
strict upper bound on provider consumption.

## 2. Published sources and adaptation

These are documented product contracts and reference architectures. They do not establish
how a company implements every internal component, or certify Relay's implementation.

| Primary source | Relevant behavior | What Relay takes |
|---|---|---|
| [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html) | Related state and event records commit together; delivery can duplicate and needs idempotent handling. | Apply the same-transaction principle to result/progress and accounting. Both already live in Relay's database, so no broker, outbox service or AWS dependency is needed. |
| [PostgreSQL 17 transactions](https://www.postgresql.org/docs/17/tutorial-transactions.html), [SQLite atomic commit](https://www.sqlite.org/atomiccommit.html) | A transaction commits its changes together or rolls them back. SQLite describes process/crash recovery and configuration assumptions. | Use the existing SQLAlchemy transaction under the job fence; verify the behavior on both databases. |
| [Stripe usage reporting](https://docs.stripe.com/billing/subscriptions/usage-based/recording-usage-api) | Usage events support identifiers/idempotency; aggregate summaries are asynchronous. | Keep replay distinct from new work. Do not use a delayed reporting aggregate to grant allowance. TA02 retains cumulative snapshots rather than adding a billing integration. |
| [Microsoft event sourcing](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing) | Events can be authoritative and rebuild projections, but the architecture carries substantial complexity. | A dedicated call ledger is a legitimate later option. Do not turn today's overlapping outcome metrics into such a ledger by assumption. |
| [Temporal Activity execution](https://docs.temporal.io/activity-execution) | Completed Activity results are recorded; interrupted executions may retry. | Preserve recorded results; distinguish an unsaved execution from replay of committed work. This does not make a remote provider call transactional with Relay's database. |

Relay pins LangGraph 1.2.11 for orchestration and uses its own Store for durable checkpoints,
leases and approvals. Switching checkpointers or adopting Temporal is outside this fix.

## 3. Approaches considered

| Approach | Benefit | Cost/limitation | Decision |
|---|---|---|---|
| Atomic reusable-state + cumulative-accounting commit | Fixes the demonstrated gap in the existing transaction; preserves current result shape | Requires complete snapshot capture at all relevant event boundaries and an explicit old-run policy | **Selected by the user** |
| Dedicated per-physical-call ledger | Better foundation for later attribution, unknown-call reconciliation and concurrency | New schema, call identities, lifecycle states and reporting changes | Deferred |
| Rebuild the baseline by summing `run_token_usage` | Reuses existing reporting rows | TA03 already demonstrates overlapping nested cancellation attribution; rows are outcome reports, not deduplicated physical calls | Rejected as an authoritative recovery rule |

## 4. Required invariants

1. A committed reusable node result, loop item or approval frame is never ahead of its
   known cumulative accounting.
2. The stored `tokens_spent` is the previous trusted baseline plus this attempt's known
   usage. It is not the sum of cumulative snapshots or arbitrary outcome events.
3. Replaying a saved result sends no provider request and adds no usage. Re-executing work
   that was not saved is a new request; this design cannot promise remote exactly-once calls.
4. Node-event usage remains scoped to the current attempt. No cumulative bookkeeping is
   added to public event payloads or counted a second time in operator metrics.
5. A failed immediate usage write may be repaired by the atomic result commit. It must
   not force a completed paid answer to be discarded merely because the separate fast write failed.
6. Further model requests require any pending known usage to have an acknowledged durable
   copy first. A false/absent fence acknowledgment cannot be treated as a successful save.
7. Existing leases, cancellation shielding, action reservations and approval deferrals
   retain their guarantees. Bookkeeping never charges an action or sends a write.
8. The current soft token threshold stays soft. A valid last response that reaches/passes
   the threshold remains valid; TA04's typed refusal handles later calls as before.

## 5. Proposed write path

```text
provider responds → accumulate known usage in shared run state
                     ↓
              try immediate persistence
                     ↓
    capture immutable cumulative snapshot at an event boundary
                     ↓
        one existing fenced database transaction:
        event + usage rows + checkpoint/item + accounting
                     ↓
        acknowledge commit → result is reusable
```

### Capture authoritative state before emission

Use `execution_policy.accounting_snapshot(run_state)`, not `event['usage']` and not the
previewed stored event. Refresh the caller's accounting sink before every graph/nested
event emission. This includes success, failure, pause, cancellation, loop-item progress
and cached events; a known cancelled response can update memory without `record_spend`.
Centralize this at the compiler's root emitter, including both nested emit arguments and
`Context.emit`, rather than adding refreshes only to normal terminal outcomes.

Freeze a copy before handing it to a background database thread. A cached/free event must
preserve the restored baseline; an empty reporting dictionary must never reset it to zero.
Keep bookkeeping separate from the public event dictionary.

### One fenced transaction

Extend the existing storage boundary conceptually as
`Store.worker_event(id, owner, event, *, accounting=None)`. The Worker supplies the captured
snapshot. Inside its current transaction, validate the fence, store event/metrics, update
the checkpoint or item row, and assign `RunRecord.accounting` before one commit. A supplied
snapshot is validated; `None` means preserve existing accounting, never erase it.

Remove the separate post-event accounting write in `Worker.emit`: it is no longer the
durability mechanism. Immediate post-response persistence remains a useful early save.
Approval publication must retain the same invariant: the paused-node boundary saves the
current snapshot before the approval/frame transaction reads and extends the action budget.

No nested commit or separate Session may write the checkpoint and accounting independently.
The existing loop compaction and usage-row creation stay in this transaction too.

### Acknowledgment and another model call

Keep track in memory of which snapshot version has an acknowledged durable copy. A failed
fast save leaves known usage pending. The current answer may finish through the atomic
outcome commit. Before another model request (including structured/grounded repair, agent
planning and query generation), first persist the pending snapshot through a strict fenced
write. That operation does not reserve or charge another action.

If that write cannot be acknowledged, raise a dedicated `AccountingUnavailable(ValueError)`
control-flow error, so the Worker preserves its explanatory message. It is not
`TokenBudgetExhausted`: usage is not established as exhausted. Retry and
`on_error` recovery must not convert it into another provider call. Free paths such as
cached outputs, deterministic work and abstention need no new provider request.

Only a confirmed commit clears the pending version. A local generation number or equivalent
is sufficient; this is not a persistent call identity or a ledger. Existing silent returns
on lost fences cannot acknowledge persistence. `LeaseLost` and cancellation propagate with
their existing control-flow semantics rather than being swallowed as an ordinary outage.
Cancellation of an `asyncio.to_thread` await does not prove its transaction rolled back:
keep the existing shield/settlement handling and acknowledge the exact frozen version.

## 6. Restore and older runs (accepted policy)

Create new runs with an internal `run.data['accounting_contract'] = 1` marker and an explicit zero-token
snapshot in the same creation transaction. Use the existing JSON fields; no new table or
Alembic column is needed. Keep the marker outside the reconstructed accounting snapshot,
whose helper currently drops fields it does not own. Only creation establishes this
initial zero baseline.

On worker restore, the marker plus a well-formed snapshot identifies the new contract.
Do not repair missing/invalid trusted accounting to zero. Reject a boolean, negative or
non-integer total. Preserve the existing legacy fallback for reading history, but do not
treat it as proof that further spending is safe.

For an older interrupted run without that guarantee, **refuse further model requests**,
while retaining its saved answers and permitting fully cached/free completion.
Neither a cached event nor a new zero snapshot may promote the old run to trusted. No
automatic sum of reporting rows, manual offset, or provider reconciliation is included.
The failure must explain that past usage cannot be verified and saved results remain;
it must not automatically restart the workflow or repeat its external actions.

This is deliberately conservative even when an old snapshot looks plausible. The user
selected this policy explicitly; it is not a claim that every old run is corrupt.
In-memory compiler-only executions retain their non-durable behavior; production Worker
execution carries the trust decision to the shared model-call boundary.

## 7. Boundaries and non-goals

- A crash after a provider response but before **any** durable acknowledgment can still
  lose that response/usage. TA02 closes saved-work-without-accounting, not that external
  call/database boundary. Unknown calls and strict reservations remain TA05/TA06 work.
- The pending-save barrier limits further requests during a known persistence outage;
  it does not make an already-started request or provider-internal retry free or bounded.
- No new call ledger, Redis, broker, billing service, provider cost estimates or full
  event-sourcing architecture. TA03 metrics and TA07 provider cache categories stay separate.
- Current execution is sequential; parallel branches remain refused. This does not close
  TA08's concurrent stale-snapshot problem or supply A5.3 reservation/settlement semantics.
- A total database outage cannot preserve a new checkpoint. Never claim otherwise:
  either the atomic transaction committed or that result is not recorded as reusable.

## 8. Acceptance tests

Write defect tests first against `f3e0923`; controls must pin existing behavior. Use real
Worker/Store and fixture providers, with barriers rather than elapsed-time assertions.

| Test group | Required evidence |
|---|---|
| Original TA02 outage + interruption | First 15-token answer/checkpoint and 15-token accounting commit together despite failed fast saves; resume reuses it; second provider call is refused at limit 15; one provider call total |
| Transaction rollback and post-commit crash | With fast saves disabled, a failure before commit leaves neither new checkpoint nor its accounting update; after commit both survive. Do not assert replay suppression for a response never committed |
| Loop item and compact loop checkpoints | Item progress and matching cumulative accounting commit together; replay never repeats the saved item or re-adds its tokens |
| Agent approval pause/repeated resume | Paused-frame publication cannot outrun accounting; defer/replay does not charge the same action again or reset tokens |
| TA01 rejection and known-usage cancellation | Rejected answers still count; cancellation captures memory-only known usage before emission. Nested accounting remains exact; do not repair TA03 by summing its overlapping outcome rows |
| Pending save before repair/planning | No second provider request if the pending snapshot still cannot be saved; recovery of that write permits exactly the intended next request |
| Saved paid answer and free completion | Failed fast save does not discard a completed answer when atomic commit works; cached/deterministic paths add zero provider calls |
| Fatal durability error, lost lease | No retry/recovery under fail/continue/route starts a new request; stale owner writes neither state nor accounting; cancellation remains cancellation |
| New/legacy/malformed accounting | New zero baseline is explicit; unverified legacy or missing/invalid baseline never grants new model spend; free cached completion does not bless legacy state |
| Compatibility controls | Known threshold/TA04 behavior unchanged; final valid response stays complete; event shapes, metrics attribution, action ceilings and replay IDs unchanged |

Run transactional crash tests on SQLite and an isolated PostgreSQL 17 schema. Include
separate-process termination after commit, with provider requests observed by a fixture
server outside the killed worker. Before-commit tests establish atomic rollback, not
exactly-once behavior of an uncommitted remote call.

Mutations must independently catch: separate checkpoint/accounting commits; missing loop
snapshot; missing pause/cancel snapshot refresh; replay added to the baseline; lost-fence
return treated as success; pending save ignored before repair; unknown legacy promoted to
zero; durability error made recoverable. Positive assertions must require the expected
record/call/checkpoint to exist, avoiding vacuous passes.

## 9. Delivery and implementation boundaries

One coordinated implementation should cover snapshot capture, transactional commit,
acknowledgment/restore guards and their tests before enabling the new run contract. Do not
ship a marker that promises atomic durability before all its write paths satisfy it.

Expected responsibilities: `storage.py` (creation contract and atomic fenced writes),
`worker.py` (snapshot handoff/acknowledgment), `compiler.py` and `iteration.py` (all emission
and reuse boundaries), `registry.py` (immediate persistence and pre-call barrier),
`execution_policy.py` (nonrecoverable durability cause), and a small accounting helper if
validation/state handling needs one. Keep the existing accounting snapshot as the source
of cumulative counts; do not add a second calculator.

The implementation plan follows approval of this design. It must specify signatures,
all test wrappers affected by the optional storage argument, failure messages, exact
defect/control tests, mutations, isolation proof and the SQLite/PostgreSQL checks. Closing
gates remain the backend seed campaign, relevant scenario campaign and hosted CI. No code
or dependency installation is authorized by this document alone.
