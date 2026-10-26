# A5.3: parallel branches with explicit joins

**Status:** Draft for review (revised 2026-10-25: corrected example, prior art, branch
visibility); no implementation started.
**Baseline:** `bbbb8d6` (A5.2 closed; hosted CI run `40138113161`, 5/5 green).

## Outcome

A workflow can run independent work at the same time and continue only when all of it is
done. Two steps can run in parallel only when neither needs the other's result.

Example: the Request reader has already read the issue. Then two branches run at once:
look up the customer in Jira, and search the knowledge base followed by an Agent that
summarises the articles (two steps in order inside one branch). A **Join** waits for both;
a Switch after the Join routes the ticket using both results. An Agent that needs the Jira
result *and* the articles belongs after the Join: validation refuses a branch node that
reads a sibling branch's output, and the editor does not offer it.

## Prior art (read 2026-10-25)

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
3. **Stopping a sibling does not undo its side effects.** Step Functions states that
   running Lambda functions cannot be stopped; Argo lets running tasks finish before
   failing; Temporal requires idempotent activities. This confirms that writes inside
   branches need their own design (A5.3b), with Argo's "drain, then fail or pause" and
   idempotency keys.
4. **Merging exclusive routes and joining parallel branches are different operations.**
   Airflow warns that a join after a branch operator needs a non-default trigger rule, and
   n8n removed a v0 behaviour where an If plus a Merge ran both branches. Relay keeps the two
   apart: exclusive merges as today, parallel branches only at a Join.
5. **Partial-failure tolerance and region-level error routes are later features**
   (Step Functions `Catch` and tolerated failures, Airflow trigger rules). Not in v1.
6. **A concurrency cap is standard** (`MaxConcurrency`, `max-parallel`): Relay caps a fork at
   8 branches.

Sources: [Step Functions Parallel](https://docs.aws.amazon.com/step-functions/latest/dg/state-parallel.html),
[Step Functions Distributed Map](https://docs.aws.amazon.com/step-functions/latest/dg/state-map-distributed.html),
[Camunda parallel gateways](https://docs.camunda.io/docs/components/modeler/bpmn/parallel-gateways/),
[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/use-graph-api),
[Argo DAG](https://argo-workflows.readthedocs.io/en/latest/walk-through/dag/),
[Airflow DAGs and trigger rules](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html),
[GitHub Actions matrix](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/running-variations-of-jobs-in-a-workflow),
[n8n Merge](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.merge/),
[Temporal activities](https://docs.temporal.io/activity-definition).

## Facts that shape this design (observed on LangGraph 1.2.11, 2026-10-24)

| Experiment | Observed result | Consequence |
| --- | --- | --- |
| Branch A raises while branch B is awaiting | B is **cancelled** mid-await, then A's error is raised | A pause or failure in one branch would cancel a sibling's write after it was sent: an uncertain write. |
| `add_edge(['a', 'b'], 'join')` | The join runs **once**, after both | A real wait-for-all join exists. |
| Two plain edges into one node, branches of unequal length | The target runs **twice** | Today's "several incoming edges" must never merge parallel branches. |

From the code:

- The binding rule (`compiler.binding_guarantees`) intersects over incoming edges: "runs
  after any one of them". That is correct for exclusive routes and wrong after a join.
- Checkpoint writes (`Store.worker_event`) lock the job row in the same transaction before
  reading the run record. This should serialise concurrent writes on SQLite and
  PostgreSQL. **Not yet proven**: it is an acceptance test below.
- An Agent reads its memory at the start and writes it at the end
  (`agent_runtime`, `memory_read` / `memory_write`). Two parallel Agents with one memory key
  lose an update.
- The token ceiling is checked before each model call (`token_budget`). Parallel calls can
  all pass the check and then all spend.
- The action budget debit (`execution_policy._debit_action`) has no `await` inside, so it
  is atomic under asyncio. It stays correct.

## Scope (v1)

In: forks, an explicit Join node, the binding rule after a join, read-only work in branches,
token reservation for parallel model calls, the editor, deployed scenarios.

Out: writes, approvals and business decisions inside branches (see "Why branches are
read-only"), nested forks, branches that end early, dynamic fan-out (map over a list; the
For each node keeps that job), and a run-wide concurrency setting.

## Model

1. **Fork.** A node with 2–8 normal outgoing edges starts parallel branches. Control nodes
   (Condition, Switch, Request reader) never fork: their edges are routes, and only one
   runs.
2. **Branch.** A chain of one or more nodes from the fork to one Join. Each branch node has
   exactly one incoming and one outgoing flow edge inside the region.
3. **Join.** A new `join` node with 2–8 incoming edges, one per branch of the same fork. It
   has no data ports; it runs once, after every branch has finished
   (`add_edge([last nodes], join)`). Nodes after the Join bind the branch outputs directly.
4. **Exclusive merges stay as they are.** A non-Join node may still have several incoming
   edges only when they cannot both run: different routes of one Condition or Switch, or a
   normal and an error edge. Validation refuses a merge whose paths start at a fork:
   "Parallel branches meet only at a Join."

## Validation rules

| Rule | Error |
| --- | --- |
| Every fork's branches end at exactly one Join, and that Join receives only that fork's branches | "Branches from `fork` must all end at one Join." |
| No Response, Condition, Switch, Calculate check, error route or nested fork inside a branch | "`node`: branches cannot stop, route or fork in A5.3." |
| No external write inside a branch: write tools, nodes with attached write tools, For each with a write body, any node that needs approval | "`node`: external writes belong after the Join." |
| Agents in sibling branches use different memory keys | "`a` and `b` share memory key `k`; parallel Agents need their own memory." |
| A branch binds only its own nodes and nodes before the fork | (existing binding error) |
| 2–8 branches per fork | "A fork has 2–8 branches." |

## Binding rule

`binding_guarantees` gets one change: at a **Join**, the executed and available sets are the
**union** of the incoming branches (every branch ran), plus the fork's own sets. Everywhere
else the intersection stays. Inside a branch, a sibling branch's outputs are not available,
because it may not have run yet. A node with `on_error: continue` inside a branch contributes
nothing after the Join, as today.

## Why branches are read-only in v1

The first experiment decides this. If one branch pauses for approval or fails, LangGraph
cancels its siblings. A sibling in the middle of an HTTP write would be cancelled after the
request left: Relay would correctly refuse to repeat it, but the run would then be stuck on
an uncertain write that no person caused. Supporting writes in branches needs a different
runtime: let siblings finish before pausing, persist several pending approvals, and resume
each branch from its own point. That is a separate step (A5.3b) with its own design.

Read-only branch work is safe to cancel: a cancelled read or model call changes nothing
outside Relay. One known cost: a cancelled model call may have used tokens that the
provider never reports. That is true of run cancellation today and is documented.

## Runtime

- **Compilation.** Fork edges become ordinary edges; each Join becomes one list edge from the
  branches' last nodes. LangGraph runs a fork's branches in the same step.
- **Failure.** A branch node that fails with `on_error: fail` fails the run, and the siblings
  are cancelled (safe, read-only). Short transient errors are retried inside the branch
  first (each node's own retry setting) while the siblings keep working. With
  `on_error: continue` the branch finishes with no outputs and the Join still runs.
- **Every branch's outcome is visible.** The run shows each branch node as succeeded,
  failed or **cancelled**, so a reviewer sees which branches stopped because of another
  branch's failure. The run error names the first failure; when several branches fail at
  once, the others appear as failed or cancelled events, not as silent gaps.
- **Resume.** Unchanged per-node checkpoints. After a crash, finished branch nodes restore
  from their checkpoints; unfinished ones run again. The Join restores or runs once.
- **Token ceiling.** Each model call reserves its `max_tokens` before it starts; the check is
  `spent + reserved + max_tokens <= ceiling`. On completion the reservation becomes the
  reported spend. Reservations live only in memory: after a crash, only persisted spend
  counts. This keeps the ceiling exact for parallel calls (option B below).
- **Action budget, receipts, cancellation, leases.** Unchanged. Branches write distinct keys
  to the run's shared maps (`eligible_outputs`, usage by invocation).
- **Events.** Branch events interleave. Each event already names its node; the Join emits
  ordinary running/success events.

## Contract 2

Unchanged rules. A fork can follow the Request reader on its continue route; branch nodes
may bind the reader's checked text. Because branches hold no writes or business decisions,
the existing consequential-node rules apply after the Join, exactly as today.

## Editor

- Palette: **Join** under Control.
- A node may have several outgoing connections when it is a fork; validation explains every
  rule above in the issue list, located on the node.
- The binding dropdown after a Join offers the branch outputs (the editor already uses the
  backend's `bindable_outputs`).
- No automatic layout. A Join card shows how many branches it waits for.

## Acceptance

1. LangGraph behaviour: the three experiments above become tests that fail if a LangGraph
   upgrade changes them.
2. Validation: every rule in the table has a refusing test and an accepting one; plain
   fan-in after a fork is refused; exclusive merges after Condition/Switch still validate.
3. Binding: after a Join, every branch output is bindable; inside a branch, a sibling's is
   not; `on_error: continue` outputs stay unavailable.
4. Runtime: branches run concurrently (measured: total time below the sum of branch times);
   the Join runs exactly once; a failing branch fails the run with no sibling side effect,
   and every sibling appears in the run as succeeded, failed or cancelled.
5. Durability: kill the worker after one branch's checkpoint; the resumed run does not
   repeat that branch's model call or read, and the Join runs once. Concurrent checkpoint
   writes from parallel branches lose none, on SQLite and on PostgreSQL.
6. Token ceiling: parallel model calls never exceed the ceiling; with a ceiling that fits
   one call, exactly one branch runs and the other fails before calling the model.
7. Memory: two sibling Agents with one memory key are refused.
8. Deployed scenarios: S22 three parallel reads, a Join, then a checked write after the
   Join (one send); S23 worker killed between branches; S24 a write inside a branch and a
   plain fan-in refused, with no run created.
9. Backend seeds 0–4, frontend, browser, scenario gate, hosted CI on the exact commit.

## Later steps (not A5.3)

| Step | What | Prior art |
| --- | --- | --- |
| A5.3b | Writes and approvals inside branches: drain running branches before pausing or failing, several pending approvals, per-branch resume, idempotency keys | Argo `failFast`, Temporal idempotency |
| A5.3c | Optional branches: the Join continues when a branch fails, and later nodes see an explicit "missing" marker; region-level error route | Step Functions `Catch`, tolerated failures; Airflow trigger rules |
| — | Nested forks | BPMN, Step Functions (branches are full state machines) |

## Alternatives considered

- **Writes in branches now.** Rejected for v1: the cancellation fact means it needs
  drain-before-pause, several pending approvals and per-branch resume. Planned as A5.3b.
- **An explicit Parallel (fork) node.** Possible, but every multi-edge source would then
  need one. An implicit fork plus a mandatory Join gives the same audit point (the Join)
  with one new node.
- **Token ceiling option A: allow a bounded overshoot** (at most branches − 1 calls), and
  document it. Simpler, but the roadmap promises budgets that stay correct.
- **LangGraph `Send` (dynamic map).** Not needed: For each already covers lists.

## Delivery sequence after approval

Lesson from A5.2: the feature must never be runnable before its refusals exist.

1. Validation, binding rule, Join node, compilation and the token reservation, in one
   commit (forks stay refused until this lands).
2. Editor: Join palette entry, multi-edge sources, issue messages, browser tests.
3. Deployed scenarios S22–S24, concurrency and durability evidence, docs.

## Decisions for review

1. Read-only branches in v1, writes in a later A5.3b.
2. Token reservation (option B) instead of a documented overshoot (option A).
3. 2–8 branches per fork, one level (no nested forks).
4. Implicit fork plus a mandatory Join node, instead of an explicit Parallel node.
