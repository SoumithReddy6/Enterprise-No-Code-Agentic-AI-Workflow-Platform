# A6: bounded loops (Repeat), and the plan for subgraphs

**Status:** Revision 3 (2026-11-09). Revision 1 (`1d97cb9`) and revision 2 (`84b6930`) were
reviewed. Decision 2a is confirmed. Revision 3 applies the four narrow changes from the
second review (see "Review of revision 2"). No implementation has started.
**Baseline:** `3b89a3e` (same tree as `main` `758f869`, after PR #9; main CI run
`42527293067`, 5/5 green). A5.3 is complete.
**Probe:** `evals/results/a6-probe-2026-11-09/`.
**Tier:** P1 (new control flow, checkpoints, accounting). Full design review before tests;
then failing tests, implementation and an implementation review, as in A5.3.

## Outcome

An author can make a step try again until its result is good enough, with a fixed upper limit.
The common case is the evaluator-optimizer pattern: a writer Agent drafts, a checker Agent
judges the draft against a rubric, and the writer gets the feedback and tries again. The loop
ends when the checker accepts the draft, or when the limit is reached.

The loop is a node, **Repeat**, not an edge that goes back. The workflow graph stays acyclic.

## Prior art (read 2026-11-09)

- **Evaluator-optimizer.** Anthropic's [Building effective agents](https://www.anthropic.com/research/building-effective-agents)
  names this workflow: one model generates, another evaluates and gives feedback, in a loop.
  It is a *workflow* (the control flow is fixed in code), not an agent. It fits when there are
  clear evaluation criteria. (Summaries consulted: [agentpatterns.ai](https://agentpatterns.ai/patterns/agent-design/anthropic-effective-agents-framework/).)
- **LangGraph cycles.** LangGraph allows back edges. A run is capped by `recursion_limit`,
  which counts graph steps for the whole run, not iterations of one loop, and it raises
  `GraphRecursionError` ([LangChain docs](https://docs.langchain.com/oss/javascript/langgraph/errors/GRAPH_RECURSION_LIMIT),
  [reference](https://reference.langchain.com/python/langgraph/errors/GraphRecursionError)).
  The docs say a graph that hits the limit often has an unexpected cycle. So a run-wide step
  cap is a safety net, not a loop contract.
- **Dify Loop node.** A container node. It stops when its termination condition is met, when
  the maximum count is reached, or when an Exit Loop node runs ([Dify docs](https://docs.dify.ai/en/cloud/use-dify/nodes/loop)).
  A community report shows the condition once could not see variables set inside the loop
  ([discussion](https://github.com/langgenius/dify/discussions/24863)): the stop rule must
  read exactly the values that the iteration produced.
- **Subworkflows.** Airflow deprecated `SubDagOperator` in favor of `TaskGroup`
  ([source](https://airflow.apache.org/docs/apache-airflow/2.9.0/_modules/airflow/operators/subdag.html)).
  The operator held a pool slot while its child tasks waited for slots, and the docs warn of
  deadlock. n8n's Execute Sub-workflow has a "wait for completion" option, and users report
  that the parent can continue while the child still waits ([n8n docs](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.executeworkflow/),
  [forum](https://community.n8n.io/t/wait-for-sub-workflow-completion-option-is-super-confusing-and-not-working-as-expected/285652)).
  Lesson for A6.2: run a subgraph inside the parent run, under its lease and budgets. Do not
  start a child run that the parent waits for.

## Facts that shape this design

1. **The graph must stay acyclic.** `_validate_flow` uses Kahn order. `binding_guarantees`,
   `branch_errors` and contract-2 checks walk the same order. A cycle is refused with
   `Workflow contains a cycle; loops are not supported yet.` (probe).
2. **Checkpoints are keyed by node ID.** `compile_workflow` restores `completed[node.id]`. A
   node that runs several times in one run has no unique checkpoint. A back edge would break
   restore, binding rules and the "Join runs once" contract.
3. **For-each already runs an attached callable several times inside one node.** It owns the
   identity `<loop>:<index>:<body>`, saves each finished item as a `run_loop_items` row
   (`node_id`, `item_index`) through the `loop_item` event, skips saved items on resume, and
   charges each call to the run-wide budgets (`iteration.py`). The row table needs no change.
4. **The typed condition can judge a checker's verdict.** `eq` on field `pass` with `true`, or
   `gte` on `score`, works on JSON text. Text that is not JSON is an error (probe).
5. **Branches are read-only** because a cancelled branch must change nothing outside Relay
   (A5.3). The same reason applies to a loop: a write in an iteration is sent once for each
   iteration.
6. **Nested loops are refused** today (`Nested loops are not supported yet.`, probe).

## Scope

A6 has two features with different risks. This design splits them:

| Step | Content | This design |
|---|---|---|
| **A6.1 Repeat** | Bounded loop node: body, optional checker, stop rule, limit | Full design (below) |
| **A6.2 Subgraphs** | Use a saved workflow as one step | Plan and open questions only (see "A6.2") |

A6.1 is delivered in three parts, as A5.3 was:

- **Part 1:** backend: node, validation, runtime, persistence, accounting, tests, storage rows.
- **Part 2:** editor: palette, the `body` and `check` ports, inspector, iterations in the run view.
- **Part 3:** deployed scenarios in the release gate.

Not in A6.1: back edges; nested loops (Repeat in For-each, For-each in Repeat, Repeat in
Repeat); writes or memory Agents in a loop; a wait between iterations (so polling is not a
use case yet); approvals inside a loop.

## Model

```
input ─► [Repeat r]  ─► out
           │ body  ─► writer  (Agent, tool or retrieval)
           │ check ─► judge   (Agent, optional)
```

**Node `repeat`** ("Repeat until", category Control, no routes).

- Input: `input` (string).
- Outputs: `text` (the last body output), `feedback` (the last checker output, or `''`),
  `summary` (JSON text, below).
- Attachments: two `loop` edges. `body` (required): the same types as a For-each body.
  `check` (optional): an Agent.
- Settings (`RepeatConfig`, strict):
  - `body`, `check`: the attached node IDs, as For-each uses `body`.
  - `max_iterations`: 1–10, default 3.
  - `until`: a `ConditionConfig`, the same model as the Condition node, unchanged.
  - `on_limit`: `fail` (default) or `last`.

**One iteration** (index `i`, from 0):

1. **The body runs.** Its input is the Repeat input for `i = 0`. For `i > 0` its input is
   compact JSON: `{"input": <Repeat input>, "previous": <last body output>, "feedback": <last
   checker output or "">}`, produced by `exact_json.dumps(..., compact=True)`.
2. **The checker runs, if attached.** Its input is `{"input": <Repeat input>, "answer": <this
   body output>}`, as compact JSON.
3. **Completeness is checked first.** An iteration is *fully evaluated* only when the body
   output and the checker output (if attached) are complete: `iteration.body_truncation`
   returns `''` for each, and neither call was refused or failed by a budget. An incomplete
   output never reaches the stop rule (see "Incomplete iterations"). This matters because a
   free-text Agent stopped by the token limit *succeeds* with an abstention text and
   `truncated: true` (`agent_runtime.budget_abstention`), and a `contains` rule can match
   that text.
4. **The stop rule** is `conditions.evaluate(verdict, until)`, on a fully evaluated iteration
   only. The verdict is the checker's output text, or the body's output text when there is
   no checker. With a checker, the verdict field's JSON type is checked first (see "Typed
   verdict").
5. **The iteration is saved** (see Runtime). Then:
   - If the rule is true, the Repeat succeeds with this iteration's outputs.
   - If it is false and `i + 1 < max_iterations`, the next iteration starts.
   - If it is false at the limit, `on_limit` applies:
     - `fail`: the node fails with `r: the stop rule was not met after 3 iterations.` The
       node's own `on_error` (`fail`, `continue` or `route`) then applies, as for any node.
     - `last`: the node succeeds with the last iteration's outputs. It is marked incomplete
       under the reserved truncation key: `r reached 3 iterations without meeting its stop
       rule; the last answer is returned.` The run reports this as incomplete work, never as
       a clean success.

**The stop rule errors loudly.** If the verdict cannot be compared (not JSON, missing field,
text where a number is needed), the Repeat fails with `r: iteration 2: <condition error>`.
A malformed verdict never counts as "not met", because that would spend more iterations on
a checker that cannot answer.

**Typed verdict.** The condition compares text, so `true` and `"true"` both equal
`compare_to: true` (`conditions.as_text`). A checker's verdict must not depend on that. With
a checker attached, validation requires:

- the checker sets `output_schema`, and `until.field` is set;
- each key of `until.field` is a `required` property of the schema (nested objects for a
  dotted path);
- the property type matches the rule: `boolean` for `eq`/`ne` with `compare_to` `true` or
  `false`; `number` or `integer` for `gt`, `gte`, `lt`, `lte` and `compare_as: number`;
  `string` for `contains`, `in`, and other `eq`/`ne`. `empty` is refused for a checker.

A fresh checker output that does not match its schema fails in the Agent itself, after its
one repair (`Agent output did not match the required structure: …`), before the Repeat sees
it. The Repeat still parses the verdict and checks the field's JSON type before
`conditions.evaluate`, on fresh and on restored verdicts. On the fresh path this is a second
line; on the restore path it is the only check, because a saved row does not pass through the
Agent again: `r: iteration 1: the verdict field pass must be a JSON boolean.` Without a
checker, the rule reads the body output as today (any operator), after the completeness
check.

**A body or checker failure fails the Repeat.** Each call keeps its own `retry` policy. There
is no per-iteration `continue`. A failed draft is not a draft to improve. An output that is
incomplete for a reason other than a run-wide budget (the Agent's own `max_steps`, or a tool
that capped its result) also fails the Repeat:
`r: iteration 1: the body's output is incomplete (<reason>).` (or `the checker's output`).

**Incomplete iterations (run-wide budget).** The token limit or the action budget can stop an
iteration at any call: before the body, between the body and the checker, or inside either.
Then:

- The loop stops. The interrupted iteration is never judged, and its draft is never returned.
  A draft whose writer finished but whose checker never ran is ineligible.
- The fallback draft is the *last fully evaluated iteration*. It did not meet the rule
  (otherwise the loop would have stopped there).
- With `on_limit: last` and a fully evaluated iteration, the Repeat succeeds with it, marked
  incomplete (`stopped_by: budget`).
- With `on_limit: fail` (the default), or with no fully evaluated iteration, the Repeat
  returns no draft and fails, and `on_error` applies (decision 2a, confirmed). The error keeps
  its budget class (`TokenBudgetExhausted` or the action-budget error), so run-level budget
  reporting is unchanged.
- **The reason says which stop it was**, so the author can tell an unmet rubric from a budget
  stop:
  - limit, `fail`: `r: the stop rule was not met after 3 iterations.`
  - limit, `last`: `r reached 3 iterations without meeting its stop rule; the last answer is
    returned.`
  - budget, `fail` or no fully evaluated draft: `r: the run budget stopped iteration 2 before
    it was checked. <iteration.HALT_REASONS[kind]>`
  - budget, `last`: `r: the run budget stopped iteration 2 before it was checked; the answer
    from iteration 1 is returned. <iteration.HALT_REASONS[kind]>`

  (Iteration numbers in messages count from 1; row indexes count from 0.)
- To tell a run-wide budget from the Agent's own `max_steps`, Part 1 adds `kind`
  (`tokens`, `action` or `steps`) to each Agent truncation record. The Repeat reads the kind,
  never the reason text. Existing readers of `truncations` ignore unknown keys.

**Summary** (JSON text): `{"iterations": n, "max_iterations": m, "met": bool,
"stopped_by": "rule" | "limit" | "budget", "truncated": bool, "truncation_reason": str}`.
`iterations` counts fully evaluated iterations only.

## Validation rules

New messages. The existing messages keep their text, except the cycle message.

| Case | Message |
|---|---|
| No body, or two | `r: Repeat needs exactly one attached body.` |
| Configured body differs from the edge | `r: the configured body must match the attached node.` (existing text) |
| Checker not an Agent, or two checkers | `r: a Repeat has at most one checker, and it must be an Agent.` |
| Configured checker differs from the edge | `r: the configured checker must match the attached node.` |
| `check` handle on a For-each | `Loop connections must leave the body port.` (existing text) |
| A loop edge from another node type | `Loop connections must start at a For each or Repeat node.` (was "a For each node") |
| A write in the body's or checker's attachment closure | `<id>: external writes belong after the Repeat.` |
| A memory-role Agent in the closure | `<id>: an Agent that writes memory cannot run inside a Repeat.` |
| Repeat in For-each, For-each in Repeat, Repeat in Repeat | `Nested loops are not supported yet.` (existing text) |
| `max_iterations` outside 1–10, invalid `until` | Shown as written, as for Condition settings |
| A checker without `output_schema`, or `until` without `field` | `r: a checker needs an output schema, and the stop rule must read one of its fields.` |
| `until.field` not a required property, or its type does not match the rule | `r: the stop rule reads <field>, which the checker's schema must require as <type>.` |
| A cycle | `Workflow contains a cycle; use a Repeat node for a bounded loop.` |

The closure walk is the one that `branch_errors` uses (`deps`: tools, specialists, loop
bodies). A Repeat may be a step in a parallel branch. Its closure is read-only by these rules,
so the branch rules accept it.

## Why the loop is read-only

An iteration that writes sends the write again on the next iteration. A crash in an
iteration repeats that iteration (see below). The only safe rule for Part 1 is that nothing
in the loop changes anything outside Relay. A loop that drafts and then sends belongs as
Repeat → (verify) → write, with the write after the Repeat.

## Runtime

The Repeat handler runs the body and the checker as attached callables through
`invoke_attached` and `execute_with_policy`, as For-each does. No LangGraph node runs more
than once.

- **Identity.** The checkpoint owner of iteration `i` is `r:<i>`, shared by the body and the
  checker, because the iteration is the save unit. The invocation IDs are `r:<i>:<body>` and
  `r:<i>:<check>`, and the parent node is `r`. Every nested event carries `loop_node_id = r`
  and `item_index = i`, applied last by the wrapper, as in `_run_item`.
- **Child invocations derive from the invocation, not the owner.** Today a root Agent's call
  is `<checkpoint owner>:<step>:<target>` (`agent_runtime._execute_agent`: at depth 0,
  `identity_base` is the checkpoint owner). Under a shared owner, a writer and a checker that
  both call tool `shared` on their first step both get `r:0:1:shared`, so their usage and
  cancellation records mix (review finding 2). Part 1 adds an optional `child_base` to
  `ExecutionIdentity` (default `None`). The Repeat sets it to the invocation ID, and
  `_execute_agent` uses it as the depth-0 base when it is set:
  `r:0:writer:1:shared` and `r:0:judge:1:shared`. Specialists below already derive from the
  calling invocation (`>`), so they become distinct too. `child_base` stays `None` for every
  other caller, so For-each items and top-level Agents keep their current IDs, and an
  in-flight run resumes across the upgrade with the same identities.
- **Persistence.** Each fully evaluated iteration is one `loop_item` event with `item_result =
  {index, status: "success", value, verdict, met}` and provenance. The existing fenced commit
  saves it as a `run_loop_items` row. The table needs no migration.
- **Every budget stop is saved, with the accounting.** An iteration stopped by a run-wide
  budget is saved as `{index, status: "interrupted", budget_halt: "tokens" | "action",
  calls: k}`, without its draft (it is never used). `k` is the number of calls that ran in the
  iteration, and it can be 0: a refusal before the body's first call also saves the row
  (revision 2 saved none, so a cached `last` result had no durable evidence for
  `stopped_by: budget`). Saving the row charges no action. This extends the For-each
  `budget_halt` mechanism, which saves only items that did work. The row commits with the
  accounting snapshot: `record_progress` updates the accounting sink first, and
  `storage.publishes` treats a `loop_item` event as one that needs accounting, so the row and
  the spend commit in one fenced transaction.
- **Replay guarantee, defined from that commit.**
  - *After the interrupted row commits:* a replay reads it, makes no call, and reproduces the
    outcome exactly: the same `budget_halt` kind, reason, returned draft or error.
  - *Before it commits:* the unfinished iteration can run again and stop at a different call,
    for a different budget. The review reproduced this with Relay's helpers: a read used the
    last action, the checker was then refused for tokens, and a replay from the same
    accounting refused the read for actions. So the guarantee is narrower: the fully
    evaluated iterations are kept and not called again; the usage already paid is kept and
    counted once; the outcome is a budget stop; and under `last` the returned draft is the
    same last fully evaluated draft. The kind and the reason may differ. (A response lost
    before any durable save is paid again: the accepted TA02 limit.)
  - This is the replay boundary of a durable workflow: work is reproduced only from what was
    recorded ([Temporal: activity idempotency](https://docs.temporal.io/activity-definition#idempotency)).
- **Resume from iteration rows.** Saved iterations are not run again. Their saved `value` and
  `verdict` rebuild the next iteration's input. Each saved verdict is checked again: the type
  check, then the stop rule. If the result differs from the saved `met`, or a non-terminal row
  is `met`, restore is refused: `r cannot be restored: a saved iteration no longer matches its
  stop rule. Start a new run.`
- **Restore of a checkpointed Repeat.** A Repeat with a node checkpoint does not reach its
  handler: `compile_workflow` returns `completed[r]` (the restore branch). That branch gets a
  `repeat` case that runs before the output is returned, with the same check as above on
  every saved iteration row (`run_state['loop_progress'][r]`, rows survive the node
  checkpoint). It also confirms that the checkpoint matches the terminal state.
- **One function derives the outcome on both paths.** `repeat_outcome(rows, config)` takes
  the iteration rows and returns the complete outputs: `text`, `feedback`, `summary` and,
  for an incomplete result, the reserved truncation key `_truncation`. The fresh handler
  returns its result. The restore branch calls it on the saved rows and requires the
  checkpoint to be **equal** to it, key by key, including `_truncation` present or absent.
  The rows are valid only as `0 … n-1` fully evaluated with no gap, plus at most one
  `interrupted` row at index `n`; `stopped_by: budget` requires that row. Any difference
  refuses restore with the message above, so a downstream node never runs on a changed
  verdict, text or completeness (review findings 4 and, for revision 2, 3).
- **The run verdict reads the Repeat summary.** `worker.run_truncation` reads `summary` only
  for `for_each` nodes; other nodes are read through `grounding` and `_truncation`. So a cached
  fallback without `_truncation` would look complete (confirmed in the code). Part 1 adds
  `repeat` to the nodes whose `summary` is read (cause kind `repeat`). With the equality
  check above, a missing `_truncation` is refused on restore, and if it were ever missing on
  a fresh path the summary still marks the run incomplete.
- **The unit of work is the iteration.** A crash after the body and before the checker repeats
  that iteration's body. This is at most one iteration's calls. It is the same as a For-each
  item, and the same accepted limit as TA02 (a response lost before a durable save is paid
  again).
- **Size.** `value` and `verdict` are saved in full, because a shortened value would give a
  different next input after a resume. If an iteration's entry is over 200,000 UTF-8 bytes,
  the Repeat fails with `r: iteration <i> output is too large to save (limit 200000 bytes).`
  Ten iterations stay within the 2 MB that For-each allows for a results list.
- **Budgets.** Each call is charged to the run-wide action budget and to the token admission
  limit, as For-each items are. A refusal at any point follows "Incomplete iterations".
- **Saved sources are checked before reuse, not at the Response.** Revision 1 relied on the
  final Response check. That is too late: on resume, the next writer or checker receives the
  saved draft before the Response runs. The shared pre-run check `kb_errors`
  (`platform_validation.py`, called by the worker before compile and by resubmission) checks
  a reused owner's saved sources, but it recognizes only For-each owners: `loops` is the set
  of `for_each` nodes, so the owner `r:0` is skipped (review finding 1, confirmed in the
  code). Part 1 adds `repeat` to that set. The owner `r:<i>` is then reused when `r` has a node
  checkpoint or row `i` is saved, and a removed source refuses the run before any call:
  `r:0: saved knowledge sources are unavailable or removed. Start a new run with current
  evidence.` Reused iterations are still added to `reused_owners`, and the `compile_workflow`
  restore branch that does this for `for_each` gets the same `repeat` case, so the Response
  check stays as the last line.
- **Saved sources are checked again where the Repeat uses a saved draft.** The pre-run check
  can pass and a source can be removed after it, before the next call. So the check also runs
  at the point of use, with the same message, through the existing
  `ctx.platform('verify_vector_sources', …)` call (the one a grounded Agent uses for its own
  answer):
  - in the handler, immediately before the first fresh call that receives restored text (the
    next iteration's body, which gets the saved `previous` and `feedback`), for the saved
    dependencies of every restored owner `r:0 … r:<i-1>`;
  - in the restore branch of a checkpointed Repeat, before it returns its outputs, for the
    saved dependencies of every `r:<j>`. A downstream node receives the text only after this
    check.
  Iterations that ran fresh in this attempt are not checked again here: their sources were
  verified when they were produced. The final Response check stays.
- **Cancellation.** In a parallel branch, a sibling's failure cancels the Repeat. The open
  iteration is not saved and reports its own spend, as `_run_item` does.
- **Provenance.** `text` never carries a stronger label than the last body output.
  `feedback` is labelled as the checker's output (guessed for a model). The stop decision is
  a model judgment when the checker is an Agent. It controls only the number of iterations
  and never authorizes an action.

## Contract 2

The Repeat is allowed in both contracts. Its output is model output when the body is an
Agent. The existing rules then apply without change: `unchecked_model_input` walks `deps`
into the body, so a Switch on `r.text` is refused, and a write that binds `r.text` needs
verification on every path.

## Acceptance (Part 1)

All new tests must fail on the baseline (the node does not exist). The tests use the demo or
fake provider, with exact scripted outputs.

**Validation (V)**

- V01 A Repeat with an Agent body and an Agent checker validates. Later nodes bind `r.text`,
  `r.feedback` and `r.summary`.
- V02 No body; two bodies; configured body differs from the edge.
- V03 A tool as checker; two checkers; configured checker differs.
- V04 A write in the body, in the checker, and in a tool attached to the body: refused.
- V05 A memory-role Agent in the closure: refused.
- V06 Nesting in all three directions: refused.
- V07 `max_iterations` 0 and 11 refused; 1 and 10 accepted. An invalid `until` shows the
  condition message.
- V08 A back edge gives the new cycle message.
- V09 A `check` edge on a For-each is refused.
- V10 `bindable_outputs` offers exactly `r.text`, `r.feedback` and `r.summary` downstream.
- V11 A checker without `output_schema`, and a checker with `until` that has no `field`:
  refused with the exact message.
- V12 Typed verdict: `eq pass true` with `pass: {"type":"string"}`, with `pass` not required,
  and `gte score 8` with `score: {"type":"string"}`: refused. `pass: boolean` and
  `score: integer` accepted. `empty` with a checker: refused.

**Runtime (R)**

- R01 The rule is met at iteration 2 of 3: body 2 calls, checker 2 calls, output is the second
  draft, `summary.iterations = 2`, `met = true`.
- R02 Iteration inputs are exactly the specified compact JSON, including `previous` and
  `feedback`.
- R03 `on_limit: fail`: the exact error. With `on_error: route`, the error branch runs.
- R04 `on_limit: last`: the last draft is returned and the run is incomplete with the exact
  reason.
- R05 No checker: the rule reads the body output.
- R06 A verdict that is not JSON fails at that iteration with the condition's message.
- R07 A body failure, after its retries, fails the Repeat. No further iteration runs.
- R08 The action budget is charged once for each call (`r:0:writer`, `r:0:judge`, ...).
  Exhaustion before iteration 3: with `on_limit: last`, iteration 2 is returned, marked
  `stopped_by: budget`; with `on_limit: fail`, the Repeat fails with the budget error.
- R09 The token admission limit refuses a call in the loop, as for a For-each item.
- R10 Events: each nested event has `loop_node_id`, `item_index` and a distinct invocation ID.
  One `loop_item` event for each fully evaluated iteration. **Shared tool:** the writer and
  the checker both call the same read tool on their first step. The two calls have the IDs
  `r:0:writer:1:shared` and `r:0:judge:1:shared`, and each one's usage and settled outcome is
  recorded under its own ID.
- R10b For-each child IDs do not change: a For-each Agent body that calls a tool still gets
  `<loop>:<i>:1:<tool>` (guards `child_base` against changing existing identities).
- R11 Resume: row 0 is saved, the worker stops, the run resumes. Row 0 is not
  called again, and iteration 1 gets the saved `previous` and `feedback`.
- R12 A saved iteration whose `met` no longer matches the rule: restore is refused with the
  exact message.
- R13 An entry over 200,000 bytes fails with the exact message, and nothing is saved.
- R14 A Repeat in a parallel branch: success; and cancellation by a failed sibling, with the
  open iteration not saved.
- R15 A body Agent with a Retrieve tool: the citations of a reused iteration are checked at
  the Response.
- R16 Contract 2: a Switch on `r.text` is refused.
- R17 **Removed source, partial progress.** Iteration 0's writer retrieves source S; iteration
  0 is saved; the worker stops; S is removed. On resume the run is refused by the pre-run
  check with `r:0: saved knowledge sources are unavailable or removed. …`, and the fake
  provider receives **no further call** (the next writer never gets the draft).
- R18 **Removed source, checkpointed Repeat.** The same, with `r` checkpointed and a
  downstream Agent not yet run: refused before the downstream Agent's call.
- R17b **Removed after the pre-run check, partial progress.** As R17, but the fake knowledge
  service passes the pre-run check and removes S right after it. The run fails with the same
  message at the Repeat's point-of-use check, and the next writer's provider call never
  happens.
- R18b **Removed after the pre-run check, checkpointed Repeat.** As R18, with the same
  late removal: the restore branch refuses, and the downstream Agent's call never happens.
- R19 **Budget between writer and checker.** The writer spends the last tokens; the checker
  is refused at admission. Row 1 is saved as `interrupted` with `budget_halt: tokens` and
  `calls: 1`. `on_limit: last` returns row 0's draft (`iterations = 1`, `stopped_by:
  budget`) with the exact budget-`last` reason; `on_limit: fail` fails with the exact
  budget-`fail` message. With no fully evaluated iteration (interruption in row 0), both fail
  with the budget-`fail` message.
- R19b **Budget stop before any call.** Row 0 is fully evaluated; the action budget is
  exhausted; the body of row 1 is refused before its first call. Row 1 is saved as
  `interrupted` with `calls: 0`, and no action is charged for it (the action counter is the
  same before and after the save). The `last` outcome is the same as in R19.
- R20 **A truncated verdict is not judged.** The review's reproduction: a free-text Agent
  stopped by the token limit succeeds with its abstention text and `truncated: true`, and a
  `contains` rule matches that text. With a checker, V11 requires a schema, and a structured
  Agent out of tokens fails typed instead (`TokenBudgetExhausted`), so the free-text case is a
  Repeat without a checker: the body is a free-text Agent, `until` is `contains` on a phrase in
  the abstention text, and iteration 1's body is stopped by the token limit. The rule is not
  evaluated, and the outcome is the R19 budget outcome, never `met`.
- R21a **Replay after the halt row commits.** After R19's interrupted row is saved, the worker
  stops and the run resumes: no call is made, and the outcome is exactly the same (kind,
  reason, returned draft or error). The same for R19b's `calls: 0` row.
- R21b **Replay before the halt row commits.** The review's reproduction: in row 1 a read tool
  uses the last action, then the checker is refused for tokens; the worker stops before the
  interrupted row is saved. On resume: row 0 is not called again; the usage already paid is
  counted once (the run's committed token and action totals before the resume plus the
  resume's own spend equal the final totals); the outcome is a budget stop, and the replay
  may stop for actions instead of tokens; under `last` the returned draft is row 0's draft.
  The test asserts these and does not assert the kind.
- R22a **Agent schema error (fresh).** A checker that returns `{"pass": "true"}` on both its
  attempt and its repair fails in the Agent with `Agent output did not match the required
  structure: …`, and the Repeat fails with that error. This proves the Agent's check, not
  the Repeat's.
- R22b **Repeat type check (restored).** A saved row whose verdict has `"pass": "true"` in
  place of `true`, with `met` and the rest of the rows and checkpoint consistent with the text
  comparison. Restore is refused with `r: iteration 1: the verdict field pass must be a JSON
  boolean.`, and nothing downstream runs. Without the Repeat's type check this case passes
  (the text comparison accepts it), so it is the oracle for M19.
- R23 **Non-budget incompleteness.** A body Agent that reaches its own `max_steps` fails the
  Repeat with the exact incomplete-output message.
- R24 **Changed verdict after the Repeat checkpoint.** `r` is checkpointed and the downstream
  node has not run. The test changes (a) the terminal row's `verdict` so the rule no longer
  holds, (b) a row's saved `met`, (c) the checkpoint's `text` so it differs from the terminal
  row. Each resume is refused with the restore message, and the downstream node never runs
  (no `running` event, no provider call).
- R25 **Cached fallback without its truncation key.** A `last` result checkpointed after a
  limit stop and after a budget stop. The test removes `_truncation` from the checkpoint:
  restore is refused. Separately, `run_truncation` on the intact checkpoint reports a
  `repeat` cause with the exact reason, and on a checkpoint whose `summary` is intact but
  whose `_truncation` is removed it still reports the run incomplete.
- R26 **Cached budget fallback without its halt row.** A checkpointed `last` result with
  `stopped_by: budget`; the test deletes the interrupted row: restore is refused.

**Storage rows.** `sqlite-repeat-sigkill` and `postgres-repeat-sigkill`. A real worker process
is killed during iteration 1. After the lease, a new worker repeats only iteration 1. The rows
are counted exactly, and each call is charged exactly once for each attempt that ran.

**Mutants (planned).**

| Mutant | Oracle |
|---|---|
| M1 the stop rule is ignored (always runs to the limit) | R01 |
| M2 off-by-one: `max_iterations + 1` iterations | R03 |
| M3 `previous` is the first draft, not the last | R02 |
| M4 a malformed verdict counts as "not met" | R06 |
| M5 `on_limit: last` returns without truncation metadata | R04 |
| M6 saved iterations run again on resume | R11, storage rows |
| M7 the restored `met` is trusted, not checked again | R12 |
| M8 the value is shortened instead of refused | R13 |
| M9 the write rule skips the checker's closure | V04 |
| M10 the checker is not charged to the action budget | R08 |
| M11 reused iterations are not added to `reused_owners` | R15 |
| M12 nested events lose `item_index` | R10 |
| M13 `kb_errors` does not recognize Repeat owners (revision 1 behavior) | R17, R18 |
| M14 the Repeat does not set `child_base` (shared owner as the child base) | R10 shared tool |
| M15 `child_base` is applied to every caller | R10b |
| M16 the stop rule runs before the completeness check | R20 |
| M17 the interrupted iteration's draft is the fallback | R19 |
| M18 the interrupted row is not saved (`budget_halt` dropped) | R21a |
| M19 the Repeat's verdict type check is removed | R22b |
| M20 the cached-Repeat restore check is removed | R24 |
| M21 the cached check compares `met` but not `text` with the terminal row | R24 (c) |
| M22 the handler's point-of-use source check is removed | R17b |
| M23 the restore branch's source check is removed | R18b |
| M24 no halt row when no call ran (revision 2 behavior) | R19b, R26 |
| M25 the restore equality check ignores `_truncation` | R25 |
| M26 `run_truncation` does not read the Repeat summary | R25 |
| M27 saving a `calls: 0` halt row charges an action | R19b |
| M28 the replay re-runs a fully evaluated row before the halt row commits | R21b |

**Gates.** The full backend suite at PYTHONHASHSEED 0–4 (PostgreSQL on a throwaway container),
the storage rows, the frontend unit and browser suites (no editor change in Part 1, so they
must not change), the deployed gate (28 of 28, unchanged), and hosted CI 5 of 5.

## A6.2: subgraphs (plan only)

A subgraph is a saved workflow used as one step of another workflow. These are the problems
that a separate design must solve:

1. **Which version runs.** Saving a workflow overwrites it today. If a parent uses a child by
   ID, editing the child changes every parent, including runs that resume later. Two choices:
   - (a) copy the child into the parent's run snapshot at submission;
   - (b) build published workflow versions first, and pin the child version.
   Choice (b) is the real fix, and "Published workflow versions" is already on the roadmap.
2. **Where it runs.** Inside the parent run, under its lease, budgets and checkpoints, with IDs
   in a namespace (`call/<child node>`). Not as a child run that the parent waits for: that is
   the Airflow SubDAG slot problem.
3. **Rules across the boundary.** The binding rule, read-only branches, contract-2
   verification and the Repeat rules must see through the boundary. The simplest correct
   model inlines the child graph at validation and at compile time.
4. **Recursion and ownership.** A workflow must not call itself, directly or through another
   workflow. A child must belong to the same workspace.

Recommendation: deliver A6.1 first. Then decide whether published versions come before A6.2.

## Alternatives considered

- **Back edges with a per-loop counter.** This is the LangGraph model. It breaks facts 1 and 2:
  node-ID checkpoints, the binding rule and the Kahn-ordered checks would all need a second,
  iteration-aware design. It also lets an author make loops that are hard to read. Rejected.
- **A checker only, without a typed rule** (the checker says "stop" or "continue"). This hides
  the stop decision in model text. The typed rule makes the decision exact and visible, and
  the condition's error messages already exist. Rejected.
- **A prompt template for iteration inputs.** Templates support only `{message}` today. A fixed
  JSON shape is exact and testable, and a body Agent's own prompt says how to use it.
  Revisit in Part 2 if authors need it.
- **Saving the body result before the checker runs** (two rows per iteration). This saves at
  most one body call after a crash, but it gives a row two meanings. Rejected for Part 1.
  See decision 4.

## Review of revision 1 (2026-11-09)

Verdict: keep the Repeat design; revise the runtime contract before Part 1. The reviewer
reproduced the baseline refusals and hosted CI on `1d97cb9` (run `42675794227`, 5/5). Each
finding was checked against the code before it was applied.

| # | Finding | Code confirming it | Applied in |
|---|---|---|---|
| 1 (P1) | A removed source is not caught before a saved draft is fed to the next Agent: `kb_errors` checks For-each owners only, and the Response check comes after the next call | `platform_validation.kb_errors`: `loops` holds `for_each` nodes only | Runtime "Saved sources are checked before reuse"; R17, R18; M13 |
| 2 (P2) | The writer and the checker share child invocation IDs (`r:0:1:shared`) | `agent_runtime._execute_agent`: the depth-0 `identity_base` is the checkpoint owner | Runtime "Child invocations derive from the invocation"; R10, R10b; M14, M15 |
| 3 (P2) | A budget between the writer and the checker is undefined; a free-text abstention can satisfy a `contains` rule; `true` and `"true"` compare equal | `agent_runtime.budget_abstention` (success with `truncated: true`); `conditions.as_text` | Model steps 3–4, "Typed verdict", "Incomplete iterations", Runtime "The stop reason is saved with the accounting"; V11, V12, R19–R23; M16–M19 |
| 4 (P2) | A checkpointed Repeat bypasses the verdict recheck | `compile_workflow` restore branch returns `completed[node.id]` before the handler | Runtime "Restore of a checkpointed Repeat"; R24; M20, M21 |

## Decisions (from the review)

1. **Order and split: Repeat first.** Published, immutable workflow versions come before
   subgraphs, and each child is pinned to a version. The first subgraph execution runs under
   the parent's lease and budgets ([AWS Step Functions versions](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-state-machine-version.html)).
2. **`on_limit` default `fail`**, with `last` as the explicit choice that returns the last
   draft marked incomplete.
   - **2a (confirmed by the second review).** A limit stop and a budget stop both follow
     `on_limit`. `fail` returns no draft, and `on_error` applies. `last` may return the most
     recent fully evaluated draft, marked incomplete. A draft whose checker never ran is
     ineligible. The reason names the stop (rule unmet or budget), so the author can tell
     them apart (see "Incomplete iterations").
3. **Limit 10, default 3.** The limit bounds iterations; Agent steps, tool calls and repairs
   still use their existing budgets.
4. **The iteration is the save unit.** A crash before the save can repeat the writer and be
   charged again by the provider. The loop stays read-only, and the tests prove that saved
   iterations are reused ([Temporal: activity idempotency](https://docs.temporal.io/activity-definition#idempotency)).
5. **No wait between iterations.** A wait needs a saved wake-up time, so the worker can
   release the run and claim it again later ([AWS Wait state](https://docs.aws.amazon.com/step-functions/latest/dg/state-wait.html)).
   Deferred.

## Review of revision 2 (2026-11-09)

Verdict: revision 2 fixes the shared invocation IDs and adds the verdict check to the cached
path; four narrow changes before Part 1. Decision 2a confirmed. Each finding was checked
against the code before it was applied.

| # | Finding | Code confirming it | Applied in |
|---|---|---|---|
| 1 (P1) | R17 and R18 prove only the pre-run check; a source removed after it reaches the next Agent | `kb_errors` runs once before compile; `verify_vector_sources` exists for checks during the run | Runtime "Saved sources are checked again where the Repeat uses a saved draft"; R17b, R18b; M22, M23 |
| 2 (P2) | A budget refusal before any call saved no row, so a cached `last` has no evidence for `stopped_by: budget`; the replay guarantee before the row commits was overstated | Revision 2 text; the review reproduced a replay refused for actions where the first attempt was refused for tokens | Runtime "Every budget stop is saved" and "Replay guarantee"; R19b, R21a, R21b, R26; M24, M27, M28 |
| 3 (P2) | A cached fallback without `_truncation` looks complete | `worker.run_truncation` reads `summary` only for `for_each` | Runtime "One function derives the outcome" and "The run verdict reads the Repeat summary"; R25; M25, M26 |
| 4 (P3) | R22 cannot catch M19: the Agent's schema check fails first | `agent_runtime`: `validate_with_repair` raises `Agent output did not match the required structure` | "Typed verdict"; R22a, R22b; M19 oracle |
