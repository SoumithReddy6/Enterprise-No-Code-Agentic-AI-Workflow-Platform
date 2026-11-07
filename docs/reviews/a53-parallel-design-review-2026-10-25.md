# A5.3 parallel branches: design review

Reviewed commit: `eb4417a6cc83e50b6562cee8869a06274de5a00e`.
Design: `docs/superpowers/specs/2026-10-24-parallel-branches-design.md`.
This reviews a proposal; no parallel-branch application implementation exists yet.

## Assessment

The main scope choices are sound: read-only branches, 2–8 branches without nested
forks, and an implicit fork with an explicit wait-for-all Join. The three reported
LangGraph behaviors were reproduced on the pinned version, 1.2.11.

Before implementation, correct the hard-token-ceiling claim and add an accounting
persistence change to the first delivery. There are also bounded corrections to
reservation contention, the memory explanation, and the concurrency test.

## R01 [P1] — Reserving max_tokens cannot enforce the stated total-token ceiling

Design lines 101–104 and acceptance item 6.

Relay counts prompt tokens plus completion tokens (`token_budget.tokens_spent`).
`LLMConfig.max_tokens` is passed as a generation/output limit to the provider.
It does not bound prompt tokens. The draft formula therefore reserves a smaller
quantity than the one it promises to limit.

Reproduction: using the draft formula, admit two calls with a 1,000-token ceiling
and 300 reserved tokens per call. Each fixture response reports 600 prompt tokens
and 100 output tokens, below its output cap. Through the real `llm_node` accounting
path, total spend is 1,400. Both reservations passed.

The crash clause has a separate hole: in-memory reservations disappear when a
worker dies. A provider can continue a request after the client dies, without
returning usage to Relay. Resuming from only reported spend allows another call
against the same allowance. The design acknowledges unreported cancelled calls,
but its unconditional exact-ceiling claim does not make that exception.

Required design change:

- State whether the guarantee covers reported total tokens, generated tokens, or
  an upper bound on all provider consumption. Do not call these interchangeable.
- For a hard total ceiling, reserve a defensible input bound plus the enforced
  output cap before each actual provider request, including repair/retry calls.
  If a provider/model cannot supply that bound, state a weaker guarantee for it.
- Persist unresolved reservations under the run lease. Do not silently refund an
  unknown call on cancellation or worker death; define reconciliation or a
  conservative retained charge, separate from confirmed usage reporting.
- Test long prompts with short output caps, retries/repairs, cancellation, and
  process death while a provider call is still outstanding.

The token probe models the draft formula around the existing accounting seam;
it is not a claim that a proposed reservation implementation already exists.

## R02 [P1] — Row locking does not prevent stale accounting snapshots

Design facts about atomic action debit and runtime lines 105–106.
Relevant implementation: `execution_policy.accounting_snapshot`,
`worker.persist_accounting` / `persist_spend`, and
`storage.reserve_accounting` / `record_accounting`.

The synchronous action decrement is safe within one asyncio task switch. The
full persistence path awaits a thread, however. An old snapshot can reach the
database after a newer one. Both storage methods replace the complete accounting
document after acquiring the same valid lease lock. The lock serializes commits;
it does not make their snapshots chronological.

A controlled two-thread test on real SQLite storage committed a newer snapshot
first, then released a delayed older snapshot. Both methods accepted it:

| Field | Newer committed value | Final stored value |
| --- | --- | --- |
| Tokens | 30 | 10 |
| Actions used | 2 | 1 |
| Remaining actions | 8 | 9 |

The second branch's usage also disappeared from the saved map. A crash at that
point would restore budget that was already spent. Distinct checkpoint keys do
not protect this whole-run accounting column.

Required change: serialize snapshot creation together with persistence using one
run-scoped mechanism, or use ordered/idempotent accounting updates. Merely locking
the database write, or locking after an old snapshot has already been taken, is
insufficient. Route all persistence paths, including post-event accounting, through
the same rule.

Add deterministic out-of-order action, token, and usage tests plus crash/resume
tests on SQLite and PostgreSQL. This belongs in delivery 1, not a later hardening
step.

Eight simultaneous checkpoint writes did preserve all eight distinct checkpoints
on SQLite. Keep the PostgreSQL checkpoint test: this review has not proved it.

## R03 [P2] — Reservation contention needs a defined outcome

Design lines 96–104 and acceptance item 6.

The proposed acceptance says one call is admitted and its sibling fails when
only one reservation fits. The already-observed LangGraph behavior then cancels
the admitted call. This does not establish that one call can finish successfully.

Reproduction: with a ceiling of 1,000 and two reservations of 600, branch A starts;
branch B refuses its reservation; LangGraph cancels A. Both requests might have
fit after A reported a smaller actual usage and released unused capacity.

Recommended rule: wait for outstanding reservations to settle when the shortage
is temporary; refuse only when the remaining allowance cannot fund the request
even after that settlement. Bound the wait by the existing run cancellation and
deadline. If fail-fast is intentional, document that the whole fork fails and
the already-started call may be cancelled, and adjust the acceptance accordingly.

Test temporary reservation pressure separately from genuinely exhausted spend.

## R04 [P2] — The memory claim describes the wrong implementation

Design facts about memory and the validation table's memory-key rule.

`agent_runtime` reads/writes conversation memory only for `role == 'memory'`.
Ordinary summary/reasoning Agents have a default memory_key but never use it.
Refusing every pair of Agents sharing that default would unnecessarily reject
ordinary parallel Agents.

Also, `MemoryService.write` does not overwrite a previously read message list.
It locks, reads the current row, and appends, using BEGIN IMMEDIATE on SQLite
and a PostgreSQL advisory lock. Two concurrent same-key writes after both callers
read an empty context retained both messages in the review probe. No lost update
was observed.

The real questions are stale context, nondeterministic ordering, and durable
memory changes before a branch checkpoint or a sibling failure. Distinct keys
do not make those changes read-only or give them replay idempotency.

For the smallest v1, refuse memory-writing Agents within parallel regions,
including attached specialists and loop bodies. Ordinary Agents remain allowed
regardless of their unused memory_key. If Memory Agents are retained, specify
their cancellation/replay semantics and scope conflict checks only to actual
memory users across the full attachment closure.

## R05 [P2] — Prove concurrency with barriers, not a wall-clock threshold

Acceptance item 4, line 133.

"Total time below the sum of branch times" can fail because of scheduling or CI
load even when branches overlap. This repository has already replaced multiple
short timing assumptions with controlled barriers.

Use events: every branch signals that it started and waits; the test requires all
start signals before releasing any branch. A sequential executor cannot satisfy
that barrier. Then finish branches separately and assert the Join remains blocked
until the last one finishes, after which it runs once. An outer generous timeout
is only a hang guard. Keep elapsed time as performance evidence, not correctness.

## Additional acceptance cases worth naming

- Unequal branch lengths; two fork/Join regions in sequence; an exclusive
  Condition/Switch merge after a completed Join.
- Recovered `continue` failures on resume. They are not checkpointed today, so the
  phrase "finished nodes restore" must distinguish successful checkpoints from
  recovered nodes that will execute again.
- Recursive attachment traversal for writes, approval-producing operations, and
  memory users, including a specialist attached to a loop body's Agent.
- Checkpoint, accounting, evidence/receipt, and lease behavior under cancellation
  together; successful checkpoint serialization alone is a narrower property.

## Evidence and limits

Reproduce:

```sh
.venv/bin/python evals/results/a53-design-review-2026-10-25/probe.py
```

The runner exits zero when the recorded observations reproduce. Its results are
in `evals/results/a53-design-review-2026-10-25/results.json`. It uses a temporary
SQLite database, local transport fixtures and the pinned LangGraph installation;
no external service or real workflow history is touched. The design remained
unchanged. No application code, dependencies, or schema was modified.

PostgreSQL, hosted CI and full regression suites were not run in this design
review. The in-flight-call crash issue is a design failure sequence, not a
measured provider-billing experiment.
