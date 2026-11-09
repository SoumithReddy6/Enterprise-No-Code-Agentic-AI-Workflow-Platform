# A6: bounded loops (Repeat), and the plan for subgraphs

**Status:** Revision 1 (2026-11-09), for design review. No implementation has started.
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
3. **The stop rule** is `conditions.evaluate(verdict, until)`. The verdict is the checker's
   output text, or the body's output text when there is no checker.
4. **The iteration is saved** (see Runtime). Then:
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
a checker that cannot answer. A checker Agent should set `output_schema` (for example
`{"type":"object","required":["pass","feedback"],...}`). Its one repair attempt is unchanged.

**A body or checker failure fails the Repeat.** Each call keeps its own `retry` policy. There
is no per-iteration `continue`. A failed draft is not a draft to improve.

**Summary** (JSON text): `{"iterations": n, "max_iterations": m, "met": bool,
"stopped_by": "rule" | "limit" | "budget", "truncated": bool, "truncation_reason": str}`.

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

- **Identity.** The checkpoint owner is `r:<i>`. The invocation IDs are `r:<i>:<body>` and
  `r:<i>:<check>`, and the parent node is `r`. Every nested event carries `loop_node_id = r`
  and `item_index = i`, applied last by the wrapper, as in `_run_item`.
- **Persistence.** Each finished iteration is one `loop_item` event with `item_result =
  {index, status, value, verdict, met}` and provenance. The existing fenced commit saves it as
  a `run_loop_items` row. The table needs no migration.
- **Resume.** Saved iterations are not run again. Their saved `value` and `verdict` rebuild the
  next iteration's input. The stop rule is evaluated again on the saved verdict. If the result
  differs from the saved `met`, restore is refused: `r cannot be restored: a saved iteration no
  longer matches its stop rule. Start a new run.`
- **The unit of work is the iteration.** A crash after the body and before the checker repeats
  that iteration's body. This is at most one iteration's calls. It is the same as a For-each
  item, and the same accepted limit as TA02 (a response lost before a durable save is paid
  again).
- **Size.** `value` and `verdict` are saved in full, because a shortened value would give a
  different next input after a resume. If an iteration's entry is over 200,000 UTF-8 bytes,
  the Repeat fails with `r: iteration <i> output is too large to save (limit 200000 bytes).`
  Ten iterations stay within the 2 MB that For-each allows for a results list.
- **Budgets.** Each call is charged to the run-wide action budget and to the token admission
  limit, as For-each items are. If a budget refuses the next iteration before any call, the
  loop stops. If at least one iteration finished, the Repeat returns the last one, marked
  incomplete (`stopped_by: budget`). If none finished, it fails with the budget error.
- **Evidence.** A body that retrieves registers evidence with run-unique labels. Reused
  iterations are added to `reused_owners` (`r:<i>`), so the Response checks their saved
  sources, as for For-each items. The restore branch in `compile_workflow` that does this for
  `for_each` is extended to `repeat`.
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
  Exhaustion before iteration 3 returns iteration 2, marked `stopped_by: budget`.
- R09 The token admission limit refuses a call in the loop, as for a For-each item.
- R10 Events: each nested event has `loop_node_id`, `item_index` and a distinct invocation ID.
  One `loop_item` event for each finished iteration.
- R11 Resume: iteration 0 is saved, the worker stops, the run resumes. Iteration 0 is not
  called again, and iteration 1 gets the saved `previous` and `feedback`.
- R12 A saved iteration whose `met` no longer matches the rule: restore is refused with the
  exact message.
- R13 An entry over 200,000 bytes fails with the exact message, and nothing is saved.
- R14 A Repeat in a parallel branch: success; and cancellation by a failed sibling, with the
  open iteration not saved.
- R15 A body Agent with a Retrieve tool: the citations of a reused iteration are checked at
  the Response.
- R16 Contract 2: a Switch on `r.text` is refused.

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
  See question 4.

## Review questions

1. **Order and split.** Repeat (A6.1) first, in three parts; subgraphs (A6.2) as a separate
   design, possibly after published workflow versions. Agree?
2. **`on_limit` default `fail`.** A loop that never met its rule fails unless the author
   chooses `last`, which returns the last answer marked incomplete. Agree?
3. **Limit 10 iterations.** This is enough for draft-and-check. A bigger limit is mostly
   polling, which Part 1 does not support (there is no wait between iterations).
4. **The unit of work is the iteration.** A crash between the body and the checker repeats
   that iteration's body. Accept, as for a For-each item?
5. **No wait between iterations.** Polling ("repeat until the job is ready") needs a timed wait
   that does not hold a worker. Defer?
