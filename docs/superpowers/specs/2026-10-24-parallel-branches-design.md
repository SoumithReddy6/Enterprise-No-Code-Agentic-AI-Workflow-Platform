# A5.3: parallel branches with explicit joins

**Status:** Revision 3 (2026-11-06). Revision 1 (`347c82f`) was reviewed in
`docs/reviews/a53-parallel-design-review-2026-10-25.md` (R01–R05). Revision 2 (`d0dfb6d`)
was reviewed in `docs/reviews/a53-design-r2-review-2026-11-06.md` (A53-R2-01 to R2-04), which
keeps the three decisions and asks for bounded corrections; after them, the review says to
proceed directly to tests-first Part 1. This revision makes those corrections. Decisions
made by the user on 2026-11-06: **token policy A** (the admission limit, extended to
branches), **Part 1 only** (backend first, then a decision), and **the run-scoped lock** for
TA08. No implementation has started.
**Baseline:** `36bac03` (main after PR #6; hosted CI run `42252633339`, 5/5 green). TA01–TA07
are closed; TA08 belongs to this step.
**Tier:** P1 (concurrency). Full design review before tests; failing tests, then
implementation, then an implementation review.

## Outcome

A workflow can run independent work at the same time and continue only when all of it is
done. Two steps can run in parallel only when neither needs the other's result.

Example: the Request reader has already read the issue. Then two branches run at once:
look up the customer in Jira, and search the knowledge base followed by an Agent that
summarises the articles (two steps in order inside one branch). A **Join** waits for both;
a Switch after the Join routes the ticket using both results. An Agent that needs the Jira
result *and* the articles belongs after the Join: validation refuses a branch node that
reads a sibling branch's output.

Part 1 delivers this through the API and the runtime. The editor does not offer the Join
until Part 2 (see "Delivery").

## Prior art

### Parallel branches (read 2026-10-25)

| System | Join | One branch fails | Partial failure allowed | Concurrency limit |
| --- | --- | --- | --- | --- |
| AWS Step Functions `Parallel` | Waits until all branches terminate; output is an array, one element per branch | The whole Parallel fails and all branches are stopped; running Lambda functions are not stopped | `Map` only: `ToleratedFailurePercentage` / `Count` | `Map`: `MaxConcurrency` |
| BPMN parallel gateway (Camunda) | Waits until each incoming flow is taken | (error handling outside the gateway) | Inclusive gateway (not parallel) | — |
| LangGraph | `add_edge([b, c], d)` runs `d` once after all; `defer=True` also exists | The whole superstep is transactional; with a checkpointer, successful nodes are saved and not repeated on resume | — | — |
| Argo Workflows DAG | A task runs when its dependencies finish | `failFast` (default): no new tasks start; running tasks finish, then the DAG fails | `failFast: false`; enhanced `depends` | — |
| Apache Airflow | `all_success` by default | Downstream becomes `upstream_failed` | Trigger rules (`all_done`, `none_failed_min_one_success`, …) | Pools |
| GitHub Actions matrix | `needs` | `fail-fast` cancels in-progress and queued jobs | `continue-on-error` | `max-parallel` |
| n8n Merge | Waits until data of all inputs is available | — | — | — |
| Temporal | Workflow code awaits activities | Activities may run more than once; make them idempotent | Workflow code decides | Worker settings |

What Relay takes from this:

1. **Wait-for-all joins with an explicit meeting point are the norm.** Step Functions also
   requires branches to be self-contained ("must not have a `Next` field that targets a
   field outside of that branch"), which is Relay's "branches end at one Join".
2. **Fail-fast is the default everywhere.** Relay fails the run on the first branch failure.
3. **Stopping a sibling does not undo its side effects.** This confirms that writes inside
   branches need their own design (A5.3b).
4. **Merging exclusive routes and joining parallel branches are different operations.**
   Relay keeps the two apart: exclusive merges as today, parallel branches only at a Join.
5. **Partial-failure tolerance and region-level error routes are later features.**
6. **A concurrency cap is standard.** Relay caps a fork at 8 branches.

Sources: [Step Functions Parallel](https://docs.aws.amazon.com/step-functions/latest/dg/state-parallel.html),
[Step Functions Distributed Map](https://docs.aws.amazon.com/step-functions/latest/dg/state-map-distributed.html),
[Camunda parallel gateways](https://docs.camunda.io/docs/components/modeler/bpmn/parallel-gateways/),
[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api),
[Argo DAG](https://argo-workflows.readthedocs.io/en/latest/walk-through/dag/),
[Airflow DAGs and trigger rules](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html),
[GitHub Actions matrix](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/running-variations-of-jobs-in-a-workflow),
[n8n Merge](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.merge/),
[Temporal activities](https://docs.temporal.io/activity-definition).

### Token limits under concurrency, and ordered state writes (read 2026-11-06)

| Source | Documented behavior | Adaptation to Relay |
| --- | --- | --- |
| [Claude Managed Agents session budgets](https://platform.claude.com/docs/en/managed-agents/budgets) | A multiagent session has one budget shared by all threads. Before each model request the platform checks consumed cost; the request that crosses the cap still completes. "Model spend past the cap is at most the cost of one model request for each thread." | Policy A: the same rule and the same bound, per branch. |
| [Azure APIM `llm-token-limit`](https://learn.microsoft.com/en-us/azure/api-management/llm-token-limit-policy) | Without prompt estimation, "concurrent or near-concurrent requests can temporarily exceed the configured token limit"; later requests are blocked. | The same admission model; concurrency overshoot is documented, not hidden. |
| [Amazon Bedrock token counting](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) | Deducts input tokens + `max_tokens` at the start, then settles. | Strict reservation (policy C). Not in A5.3. |
| [iron_cost](https://docs.rs/iron_cost), [liter-llm](https://docs.rs/crate/liter-llm/1.12.2/source/src/tower/budget.rs) (small libraries, weak evidence) | One reserves the maximum estimated cost with compare-and-swap; the other says its check is best-effort under concurrent load. | The same two styles exist in libraries; no change. |
| [LangGraph `INVALID_CONCURRENT_GRAPH_UPDATE`](https://docs.langchain.com/oss/python/langgraph/errors/INVALID_CONCURRENT_GRAPH_UPDATE) | Parallel nodes that write one state key need a reducer; an aggregator node after the fan-out is recommended. | Relay's `values` channel already merges by node id; the Join is the aggregator. |
| [PostgreSQL transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html) | Under Read Committed a second `UPDATE` waits, then applies its change to the updated row version. | Explains why `x = x + n` keeps every update and why Relay's whole-snapshot replace does not. Not chosen (see TA08). |
| [Temporal workflow message passing](https://docs.temporal.io/develop/python/workflows/message-passing#use-asynciolock-to-prevent-concurrent-handler-execution) | Concurrent handlers that share workflow state are serialized with `asyncio.Lock`. | The chosen TA08 fix: one asyncio lock per run attempt in the worker. It supports serialization only; freshness comes from the publication sequence below, and atomicity from the lease fence and the TA02 transaction. |
| [Python asyncio synchronization](https://docs.python.org/3.12/library/asyncio-sync.html#asyncio.Lock), [shielding](https://docs.python.org/3.12/library/asyncio-task.html#shielding-from-cancellation) | A lock admits one holder; a shielded inner task keeps running when its caller is cancelled. | The shielded writer, not the cancellable waiter, owns the lock until the commit thread ends. |

Not found: an official statement of LiteLLM, Dify or n8n behavior for one budget under
parallel calls. This design makes no claim about them.

## Facts that shape this design

Observed on LangGraph 1.2.11 (2026-10-24; reproduced by the review on the pinned version):

| Experiment | Observed result | Consequence |
| --- | --- | --- |
| Branch A raises while branch B is awaiting | B is **cancelled** mid-await, then A's error is raised | A failure in one branch cancels a sibling's in-flight call. Safe only for read-only work. |
| `add_edge(['a', 'b'], 'join')` | The join runs **once**, after both | A real wait-for-all join exists. |
| Two plain edges into one node, branches of unequal length | The target runs **twice** | Today's "several incoming edges" must never merge parallel branches. |

From the code at `36bac03`:

- `_validate_flow` refuses every node with more than one normal outgoing edge
  ("parallel branches are not supported yet", `compiler.py:162`). Forks are impossible today.
- `binding_guarantees` intersects over incoming edges ("runs after any one of them"). That
  is correct for exclusive routes and wrong after a join.
- The `values` state channel merges by node id (`merge_values`), so parallel branch outputs
  do not collide in LangGraph state.
- Inside one node no model calls run concurrently (no `gather` or task group in the
  runtimes), so **one branch has at most one model call in flight at a time**.
- `allocate_labels` and the action debit (`execution_policy._debit_action`) have no `await`
  inside, so they are atomic under asyncio. Citation labels stay unique under parallel
  branches; their numbers can differ from one run to the next.
- Only Agents with `role == 'memory'` read or write conversation memory
  (`agent_runtime.py:289`, `:478`). `MemoryService.write` locks and appends; no lost update
  was observed by the review.
- **TA08 on main (probe 2026-11-06).** The worker persists accounting on three paths
  (`persist_accounting`, `persist_spend`, `emit` in `worker.py:68–93`). Each captures its
  snapshot, then commits it in a thread. Nothing orders the commits. TA02 made a lower
  `tokens_spent` a refused write, so an out-of-order older snapshot now has two outcomes:
  - lower tokens: `AccountingUnavailable(STALE)`, and the run **fails for no real reason**;
  - equal tokens: the older snapshot is **accepted**, and the action counter goes back
    (3 → 2), so an action is silently refunded.

  Both reproduce on `reserve_accounting` and `record_accounting` with a delayed thread.
  Sequential execution cannot reach them; parallel branches can.

## Scope

**Part 1 (this design's delivery):** forks, the `join` node, validation, the binding rule
after a Join, compilation, the admission limit under parallel calls, the TA08 lock, the
memory-Agent refusal, docs. API and runtime, plus one frontend change: the editor's
`bindableOutputs` learns the Join union, so its binding choices match the backend
(A53-R2-04). The Join stays hidden from the palette.

**Later, decided after Part 1:** Part 2 (editor: Join in the palette, multi-edge sources,
issue messages, browser tests); Part 3 (deployed scenarios S22–S24, README).

**Out of A5.3:** writes, approvals and business decisions inside branches (A5.3b), optional
branches (A5.3c), nested forks, branches that end early, dynamic fan-out (For each keeps
that job), a run-wide concurrency setting, strict token reservation (policy C).

## Model

1. **Fork.** A non-control node with 2–8 normal outgoing flow edges starts parallel
   branches. Control nodes (`is_control`: Condition, Switch, Request reader and the legacy
   router, greeting cache and extraction check) never fork: their edges are routes, and only
   one runs. A fork node cannot use `on_error: route`.
2. **Branch.** A chain of one or more nodes from the fork to one Join. The region is
   **closed**: each branch node has exactly one incoming flow edge in total, from its
   expected predecessor (the fork or the previous node of the same branch), and exactly one
   outgoing flow edge, to the next node of the same branch or to the branch's Join. No edge
   from outside the region enters a branch (A53-R2-03): a route that entered the middle of a
   branch would run that branch alone, and the Join would never run.
3. **Join.** A new `join` node (category Control) with 2–8 incoming edges, one per branch
   of the same fork. It has no data ports, no settings, `on_error: fail` and no retry. It
   runs once, after every branch has finished, and emits ordinary running/success events.
   Nodes after the Join bind the branch outputs directly.
4. **Exclusive merges stay as they are.** A non-Join node may still have several incoming
   edges only when they cannot both run: different routes of one Condition or Switch, or a
   normal and an error edge. Validation refuses a merge whose paths start at a fork:
   "Parallel branches meet only at a Join."

## Validation rules

Every rule applies to the branch's **attachment closure**: the flow node plus its attached
tools, specialists (recursively) and loop bodies (`split_graph` deps).

| Rule | Error |
| --- | --- |
| Every fork's branches end at exactly one Join, and that Join receives only that fork's branches | "Branches from `fork` must all end at one Join." |
| A Join has 2–8 incoming edges, all from one fork | "`join`: a Join waits for the branches of one fork." |
| A Join keeps `on_error: fail` and no retry | "`join`: a Join cannot recover or retry." |
| A branch node has an incoming flow edge from outside its region, or an outgoing edge that leaves the region other than to its Join | "`node`: a parallel branch can be entered only from its fork and left only to its Join." |
| No Response, control node, Calculate, error route or nested fork inside a branch (a Calculate is a business check, so it belongs before the fork or after the Join) | "`node`: branches cannot stop, route, decide or fork in A5.3." |
| No external write in a branch's closure: write tools, Agents with attached write tools (any depth), For each with a write body | "`node`: external writes belong after the Join." |
| No memory-role Agent in a branch's closure (flow node, specialist or loop body) | "`node`: an Agent that writes memory cannot run in a parallel branch in A5.3." |
| A branch binds only its own nodes and nodes before the fork | (existing binding error) |
| 2–8 branches per fork | "A fork has 2–8 branches." |

Ordinary Agents in sibling branches are allowed even when they share the default
`memory_key`, because they never use it (R04).

## Binding rule

`binding_guarantees` gets one change: at a **Join**, the executed and available sets are the
**union** of the incoming branches (every branch ran), plus the fork's own sets. Everywhere
else the intersection stays. Inside a branch, a sibling branch's outputs are not available,
because it may not have run yet. A node with `on_error: continue` inside a branch contributes
nothing after the Join, as today. `bindable_outputs` uses the same function, so the editor
offers exactly these values when Part 2 lands.

## Why branches are read-only in A5.3

If one branch fails, LangGraph cancels its siblings. A sibling in the middle of an HTTP write
would be cancelled after the request left: Relay would correctly refuse to repeat it, but the
run would then be stuck on an uncertain write that no person caused. Supporting writes in
branches needs a different runtime: let siblings finish before pausing, persist several
pending approvals, and resume each branch from its own point (A5.3b).

Read-only branch work is safe to cancel: a cancelled read or model call changes nothing
outside Relay. A cancelled model call can still have used tokens the provider never reports.
TA06 records such a call as `unanswered`, not as zero.

## Token limit: the admission limit, extended to branches (policy A)

Relay keeps the TA05 contract. The run token limit is an **admission limit**: before each
model call, the reported spend so far is compared with the limit (`tokens_spent >= limit`
refuses). An admitted call is never aborted by the limit.

With branches, several calls can be admitted against the same reported spend. The bound
becomes, for an unchanged limit and the known reported total:

> The run can end above the limit by at most the calls that were admitted and not yet
> reported when the limit was crossed: **one call for each branch running at that moment**,
> so at most 8 for one fork. Outside a fork the TA05 one-call bound holds.

This is the bound Claude Managed Agents documents for multiagent sessions. It does not cover
consumption hidden by missing counts or HTTP retries inside one call, and one call's own size
is not bounded (unchanged from TA05).

Consequences:

- No reservation, so there is no reservation contention (R03 does not apply). A branch whose
  call is refused at admission raises the existing typed token refusal; it follows its
  node's `on_error` exactly as today (`fail` fails the run and cancels the siblings;
  `continue` lets the Join run with the run marked `truncated`, cause `budget`).
- The `token_limit {policy, limit, spent, over}` record from TA05 needs no change: it is
  computed from the durable spend on every terminal path.
- `docs/operations.md` **Overshoot**: replace "with sequential execution … at most the last
  admitted call" with the bound above, and name parallel branches.

Policy C (reserve a defensible input bound plus `max_tokens`, persist reservations under the
lease, settle, reconcile after a crash; R01, R03) stays named as not provided.

## Accounting order: the run-scoped lock (TA08, R02, A53-R2-01, R2-02)

**Two clocks.** The existing `AccountingDurability.generation` advances only when model usage
is accumulated (`registry.account_usage`); an action debit (`_debit_action`) changes the
counter, the remaining budget and the charged keys without advancing it. It therefore cannot
tell which of two action-only snapshots is newer (A53-R2-01: two actions ran, the
generation stayed 0, and a newest-by-generation rule saved one). The design keeps that
generation for its one job, acknowledging saved **spend**, and adds a second clock for
**freshness**:

- `publication_sequence`: a counter in the worker that advances once for every newly
  produced full accounting snapshot, including action-only changes.
- `spend_generation`: `AccountingDurability.generation` at the moment the snapshot was
  produced. Unchanged in meaning.

**Rule.** One `asyncio.Lock` per run attempt in `Worker._execute` orders every accounting
commit. A small publisher object replaces the worker's plain mirror dict. The three
persistence paths (`persist_accounting`, `persist_spend`, `emit` with accounting) and the
compiler's `accounting_sink` update go through it:

1. **Publish synchronously.** When a snapshot is produced, the publisher freezes the triple
   `(publication_sequence, spend_generation, deepcopy(snapshot))`, before any `await` or lock
   wait. Callers build the snapshot from `run_state` with no `await` in between, so the triple
   is exact. A publication keeps its sequence while it waits or is retried; an older snapshot
   never receives a fresh sequence later.
2. **Commit the newest.** Under the lock, the writer takes the publication with the highest
   `publication_sequence` (not its caller's argument) and commits its snapshot
   (`reserve_accounting`, `record_accounting`, or with the event in `worker_event`).
3. **Acknowledge only what was committed.** On a confirmed commit, the writer acknowledges
   that publication's frozen `spend_generation`, never the generation current when the thread
   returns. Spend accumulated during the commit stays pending until a snapshot that contains
   it is confirmed.
4. **The shielded writer owns the lock.** The lock is released only when the commit thread has
   finished, including when the awaiting branch is cancelled. `emit` already shields its
   write; the lock is acquired and released inside the shielded part, not by the cancellable
   waiter.

**What is coalesced, and what is not.** Only the accounting snapshot is coalesced: a commit
may carry a newer snapshot than its caller produced. Each event keeps its own identity and
body, invocation-scoped outcome usage (`usage_for`, TA03) and cancellation commit receipts
(`PersistedNodeCancellation`) are unchanged, and each event still commits in its own
transaction. No ledger, schema change or migration is added.

**Why it is enough.** Only the lease holder writes a run's accounting, and every write is
fenced. Within that one worker process the lock makes commits happen one at a time, and each
commits the newest publication, so each committed snapshot is at least as new as the one
before. A delayed older snapshot can no longer commit after a newer one, so neither STALE
failures nor action refunds can happen. TA02's `_set_accounting` check stays as the last line
of defense.

**Why not delta updates.** Writing `x = x + n` per field (the PostgreSQL pattern) would also
keep every update, but it changes the accounting storage format, the TA02 snapshot checks and
the resume path. The lock changes the worker's persistence code and the sink.

**Cost.** Accounting commits of one run are serialized. They are already serialized by the
job-row lock in the database; the lock fixes their order. Model calls still run concurrently.

## Runtime

- **Compilation.** Fork edges become ordinary edges; each Join becomes one list edge from
  the branches' last nodes (`add_edge([last nodes], join)`). LangGraph runs a fork's
  branches in the same superstep.
- **Failure.** A branch node that fails with `on_error: fail` fails the run, and the siblings
  are cancelled (safe, read-only). Short transient errors are retried inside the branch first
  (each node's own retry setting) while the siblings keep working. With `on_error: continue`
  the branch finishes with no outputs and the Join still runs.
- **Every branch's outcome is visible.** The run shows each branch node as succeeded, failed
  or **cancelled**. The run error names the first failure; when several branches fail at
  once, the others appear as failed or cancelled events, not as silent gaps.
- **Resume.** Unchanged per-node checkpoints. After a crash, branch nodes with a **saved
  success checkpoint** restore from it. A node that failed and recovered with `continue` has
  no checkpoint today, so it runs again (read-only; its tokens are counted again). Unfinished
  nodes run again. The Join restores or runs once. A response lost before any save is paid
  for again (the TA02 limit, unchanged).
- **Action budget, receipts, cancellation, leases.** Unchanged. Branches write distinct keys
  to the run's shared maps (`values`, usage by invocation, loop progress, agent frames).
- **Events.** Branch events interleave. Each event already names its node.

## Contract 2

Unchanged rules. A fork can follow the Request reader on its continue route; branch nodes may
bind the reader's checked text. Because branches hold no writes or business decisions, the
existing consequential-node rules apply after the Join, exactly as today.

## Editor in Part 1

The editor is **not** a supported way to build forks until Part 2, but it must not offer
wrong bindings for a workflow that contains one (A53-R2-04):

- **Hidden Join.** `paletteCategory` (`frontend/lib/workflow.ts`) is an explicit map, so a
  `join` type is not offered in the palette. No change.
- **Binding parity (one change).** The editor computes its own choices with `bindableOutputs`
  (`frontend/lib/workflow.ts:441`, used at `workflow-editor.tsx:891`), which intersects the
  arrivals. It gets the same Join union as the backend. The review probe showed that, without
  it, the editor offers `input.message` and `fork.text` after a Join and omits the branch
  outputs.
- **One shared contract.** A fork/Join case is added to `CASES` in
  `backend/tests/test_binding_contract.py` and regenerated into
  `frontend/tests/fixtures/bindable-outputs.json`; `frontend/tests/bindings.test.ts` already
  holds the frontend helper to that fixture. No new contract file.
- **Mechanical fixture update.** Registering `join` changes
  `frontend/tests/browser/request-nodes.json`, which `test_request_examples.py:34` compares
  with every registered definition. It is regenerated, not hand-edited.
- **Not in Part 1:** drawing forks, multi-edge sources, issue-list wording and browser tests
  for parallel workflows (Part 2). `docs/operations.md` states that forks are built through
  the API until then.

## Acceptance (Part 1)

Budget: **28 new collected backend cases and 17 mutants**, allocated below. HTTP is mocked
with `httpx.MockTransport` through `providers.client`; the real Worker and Store run. Every
concurrency test uses barriers (asyncio or threading events) at observable boundaries (a
publication recorded, a Store method entered, a commit finished), never a wall-clock threshold
or an immediate check of an unfinished task; an outer generous timeout is only a hang guard
(R05, A53-R2-02). Several shapes inside one validation case are one collected case; each
shape asserts its own message.

| ID | Cases | Case | Baseline (`36bac03`) expectation |
| --- | ---: | --- | --- |
| L01 | 1 | LangGraph pins, three asserts: a raising branch cancels its sibling; a list edge runs the join once; plain fan-in runs the target twice | Pass (control; fails if a LangGraph upgrade changes them) |
| V01 | 1 | Refused topologies: branches meet at a non-Join node; a branch ends at a Response; **a Condition route enters the middle of a branch** (the A53-R2-03 shape); a branch node leaves the region | Red by message |
| V02 | 1 | Accepted topologies: unequal branch lengths; two fork/Join regions in sequence; a Condition merge after a Join | Red (refused) |
| V03 | 1 | Response, Condition, Switch, Request reader, Calculate, an error route or a nested fork inside a branch: each refused | Red by message |
| V04 | 1 | A write in the closure (flow write tool; Agent with a write tool; a specialist two levels down with a write tool; For each with a write body): each refused | Red by message |
| V05 | 2 | (a) a memory-role Agent as a branch node, a specialist or a loop body: each refused; (b) two ordinary Agents with the same default `memory_key` in sibling branches: accepted | Red |
| V06 | 1 | 9 branches refused; 8 accepted | Red |
| V07 | 1 | A Join fed by two forks, with one edge, with `on_error` other than `fail`, or with a retry: each refused | Red (unknown node type) |
| B01 | 1 | After a Join every branch output validates and is listed by `bindable_outputs` | Red |
| B02 | 1 | Inside a branch, a sibling's output is refused; the fork's output is accepted | Red |
| B03 | 1 | A branch node with `on_error: continue` is not bindable after the Join (existing message) | Red |
| R01 | 1 | Concurrency: both branch providers signal "started" and wait until both started; the Join stays blocked until the last branch finishes, then runs once | Red |
| R02 | 1 | Unequal branch lengths at run time: the Join runs once, after both | Red |
| R03 | 1 | A branch fails with `on_error: fail`: the run fails naming it, the sibling's node shows `cancelled`, and the sibling sends no further request | Red |
| R04 | 1 | A branch node with `on_error: continue` fails: the Join and Response run; the run succeeds with the failure shown | Red |
| R05 | 1 | Limit 100; two branch calls of 80 are both admitted (spend 0 at admission); spend 160; `token_limit.over` 60; a model call after the Join is refused with the typed token refusal | Red |
| R06 | 1 | Contract 2: a fork on the Request reader's continue route; a branch binds the checked text; the run completes | Red |
| A01 | 1 | **Action-only order** (SQLite). Branch A's `reserve_accounting` is held inside the Store on a threading event. Branch B debits an action (equal tokens) and publishes; the test observes B's publication and that B has **not** entered the Store; then releases A. Both commit, in order A then B. The stored counter, remaining budget and charged keys are the newest (2, 8, both keys). No STALE failure | Red; with the fork allowed and the spend-generation rule, saves 1 action (M15) |
| A02 | 1 | **Spend and event order** (SQLite). The same schedule with `record_accounting` and `worker_event`: both events are saved with their own identities and non-overlapping invocation usage; final tokens are the sum. A usage update that arrives while A's commit runs stays pending (not acknowledged) until a snapshot containing it is confirmed | Red |
| A03 | 1 | A branch is cancelled while its commit is held: the lock stays held until that commit finishes, and the next commit enters the Store only after it | Red |
| A04 | 1 | **Stale queued snapshot.** While A holds the lock, B publishes and waits, then C publishes a newer snapshot and waits; C is cancelled before it acquires. The commit after A carries C's newer snapshot, not B's argument | Red |
| A05 | 2 | The A01 and the A02 schedule, each on PostgreSQL (CI `postgres-conformance`; locally a throwaway container on 55433; skipped without a URL, as the existing PostgreSQL rows) | Red |
| D01 | 2 | SIGKILL after branch A's checkpoint commit while branch B's call is outstanding, on SQLite and on PostgreSQL: the resumed run does not repeat A's call (request count), runs B again, runs the Join once, and the saved tokens include A once | Red |
| D02 | 2 | Eight branches commit checkpoints at the same moment: all eight are saved, on SQLite and on PostgreSQL | Red |
| **Total** | **28** | | |

**Frontend.** No new frontend test file: the fork/Join case added to the shared binding
fixture is checked by the existing `bindings.test.ts`, and the two regenerated fixtures are
checked by their existing tests. These add no collected backend cases.

**What "red on the baseline" means here.** A5.3 is a new feature: on `36bac03` every fork is
refused by validation, so most cases fail because the run or the expected message never
happens, and V07 fails on the unknown `join` type. That is a refusal by behavior, as in A5.2,
not a missing Python interface. The TA08 defect itself is proven on the baseline by the
probe in `evals/results/a53-design-r2-2026-11-06/` and by the review's generation probe in
`evals/results/a53-design-r2-review-d0dfb6d/`; in Part 1 by mutants M9–M11, M14 and M15.

No existing test may change, except a test that pins the old `compiler.py:162` message and
the two regenerated fixtures; each is listed in the evidence.

**Mutants** (each must be killed by its named test):

| Mutant | Planned oracle |
| --- | --- |
| M1 Join uses the intersection, not the union | B01 |
| M2 a Join may receive two forks' branches | V07 |
| M3 write detection ignores the attachment closure | V04 (specialist) |
| M4 memory refusal checks flow nodes only | V05a (specialist, loop body) |
| M5 refuse every sibling Agent pair that shares a `memory_key` | V05b |
| M6 branch cap 9 | V06 |
| M7 plain fan-in after a fork allowed | V01 |
| M8 the Join compiled as plain edges | R02 (Join runs twice) |
| M9 no lock | A01 |
| M10 lock, but commit the caller's snapshot | A04 |
| M11 lock released when the waiter is cancelled | A03 |
| M12 a branch may bind a sibling's output | B02 |
| M13 a control node allowed inside a branch | V03 |
| M14 acknowledge the generation current at commit time, not the committed publication's | A02 (pending spend reported as saved) |
| M15 choose the newest publication by `spend_generation`, not `publication_sequence` | A01 (one action saved) |
| M16 a branch node may have an incoming edge from outside its region | V01 (side entry) |
| M17 the frontend `bindableOutputs` intersects at a Join | `bindings.test.ts` (fork/Join fixture case) |

**Gates:** the full backend suite at `PYTHONHASHSEED` 0–4 in an isolation copy; frontend unit,
typecheck, lint, build and browser tests; the deployed scenario gate (24 rows, unchanged);
hosted CI on the exact commit. Evidence in `evals/results/a53-part1-<date>/`. Docs:
`docs/operations.md` (Overshoot bound, the Join, read-only branches, forks built through the
API until Part 2) and `docs/platform-reference.md` (the `join` node).

**Stop rule:** if the LangGraph pins (L01) do not hold, or D01 shows a repeated checkpointed
call, stop and report before changing the design.

## Review findings and answers

| Finding | Answer in this revision |
| --- | --- |
| R01 [P1] reserving `max_tokens` cannot enforce a total-token ceiling | The hard-ceiling claim is removed. Policy A states the guarantee (reported total; one call per running branch past the limit) and its exclusions. Policy C stays named as not provided. |
| R02 [P1] row locking does not order snapshots | The run-scoped lock, committing the newest publication (corrected in revision 3, A53-R2-01). Tests A01–A05 in Part 1. Re-probed on main: TA02 turned token loss into a STALE failure; equal-token action refunds remain. |
| R03 [P2] reservation contention | Does not apply: there is no reservation under policy A. A refused admission follows the node's `on_error`. |
| R04 [P2] the memory claim describes the wrong implementation | The rule now refuses memory-role Agents in a branch's closure and allows ordinary Agents; the "lost update" claim is removed. |
| R05 [P2] prove concurrency with barriers | R01 and A01–A03 use barriers; elapsed time is not an oracle. |
| A53-R2-01 [P1] the generation misses action changes | Two clocks: `publication_sequence` for freshness (every snapshot, actions included), `spend_generation` for acknowledgment. A01 asserts counter, remaining and charged keys; M15. |
| A53-R2-02 [P2] A01/A02 describe an impossible schedule | B publishes and waits behind the lock; the test checks B has not entered the Store, then releases A. A04 is M10's stale queued-snapshot oracle; A02 keeps M14's pending-spend check. |
| A53-R2-03 [P2] side entries into a branch | The region is closed (Model, item 2; a new validation row); the counterexample is in V01; M16. |
| A53-R2-04 [P2] hiding Join is not editor compatibility | Minimal binding parity in the frontend helper through the existing shared fixture; the registry fixture regeneration is listed; forks are API-built until Part 2. M17. |
| Additional cases | Unequal lengths, two regions in sequence and a merge after a Join (V02); recovered `continue` nodes on resume (Runtime, Resume); recursive closure (V04, V05); cancellation with accounting (A03). |

## Later steps (not A5.3)

| Step | What | Prior art |
| --- | --- | --- |
| A5.3b | Writes and approvals inside branches: drain running branches before pausing or failing, several pending approvals, per-branch resume, idempotency keys | Argo `failFast`, Temporal idempotency |
| A5.3c | Optional branches: the Join continues when a branch fails, and later nodes see an explicit "missing" marker; region-level error route | Step Functions `Catch`; Airflow trigger rules |
| — | Strict token reservation (policy C) | Amazon Bedrock |
| — | Nested forks | BPMN, Step Functions |

## Alternatives considered

- **Writes in branches now.** Rejected: the cancellation fact means it needs drain-before-pause,
  several pending approvals and per-branch resume. Planned as A5.3b.
- **An explicit Parallel (fork) node.** Possible, but every multi-edge source would then need
  one. An implicit fork plus a mandatory Join gives the same audit point with one new node.
- **Token policy C (strict reservation).** Rejected for A5.3: it needs a per-provider input
  bound, persisted reservations, settlement, crash reconciliation and a contention rule (R01,
  R03). Policy A matches TA05 and the documented Claude Managed Agents bound.
- **Delta accounting updates instead of the lock.** Rejected for now: a storage-format change
  for a problem the lock solves inside one worker.
- **LangGraph `Send` (dynamic map).** Not needed: For each already covers lists.

## Delivery

Lesson from A5.2: the feature must never be runnable before its refusals exist.

1. **Part 1 (this design):** validation, the binding rule, the `join` node, compilation, the
   TA08 lock, the frontend binding parity and the docs, in one commit. Forks stay refused
   until it lands.
2. **Decision point.** After the Part 1 review: Part 2 (editor, browser tests) and Part 3
   (S22–S24, README), or pause.

## Decisions (recorded 2026-11-06)

1. Read-only branches; writes in A5.3b.
2. **Policy A**: the admission limit, with the one-call-per-running-branch bound. Not strict
   reservation.
3. 2–8 branches per fork, one level (no nested forks).
4. Implicit fork plus a mandatory Join node.
5. **Part 1 only**, then decide.
6. **TA08 fix: one run-scoped asyncio lock** that commits the newest publication by
   `publication_sequence` and acknowledges only its frozen `spend_generation`.
7. **Editor in Part 1:** binding parity only (A53-R2-04, the review's first option); Join
   hidden; forks built through the API until Part 2.
