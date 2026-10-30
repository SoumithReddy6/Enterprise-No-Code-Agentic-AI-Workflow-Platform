# TA04 plan review — 2026-10-30

Reviewed baseline: `51ba164`. Plan: `docs/superpowers/plans/2026-10-30-typed-token-budget-refusal.md`.
This is a plan review. No application code, original plan, original probe or original evidence was changed.

Recommendation: choose **D1=B**, with the two plan changes below before implementation.
The typed refusal, non-retry policy and explicit re-raises through both repair wrappers
are appropriate. Recovered token refusal should reuse the existing confirmed budget cause.

## R01 — P2: save a loop's budget halt, not just its failed item

Plan section 4c (lines 105–107) says the loop already handles a partly executed item
followed by budget refusal. That describes a fresh run but not replay. T6 covers graph
recovery and T7 covers a fresh loop; neither covers interruption after the loop item is saved.

`iteration._run_item` returns the exhaustion reason separately from the item entry. The
entry saved by `record_progress` has `status: failed` and an error string, with no typed halt
cause. `for_each_node` halts only in the fresh-item branch. Replayed entries skip that check.

Counterexample using a Query body, three items and the default item-error `continue`:

1. Item 0 retrieves and spends all 100 tokens.
2. Item 1 retrieves; its model call is refused. Save its failed item entry.
3. Interrupt immediately after that save, before the loop's checkpoint.
4. Resume from item progress and accounting. Items 0 and 1 are restored; item 2 retrieves
   before its generation is refused.

| Run | Retrievals | Provider calls | Actions | Item outcomes |
|---|---:|---:|---:|---|
| Uninterrupted | 2 | 1 | 2 | success, failed |
| Interrupted and resumed | 3 | 1 | 3 | success, failed, failed |

The summary and stop reason differ too. Checking only provider-call count misses this.
The restored action budget can still have room, so it cannot substitute for the lost halt.

**Plan change:** persist a typed budget halt with progress (or an equivalent durable loop
halt record), and honor it on replay before another item starts. Preserve the control
metadata through `ResultBudget` compaction and update its compact-size reserve if needed.
Do not reconstruct control flow from the error string.

**Test:** extend T7 with interruption after item 1's progress commit and resume. Require
identical outputs, summary, reason, retrieval count, provider-call count and action spend
to an uninterrupted run. Include the compact-entry path if the halt travels on the entry.

## R02 — P2: D1=B must also cover refusal during an Agent's final-answer repair

Section 4d (lines 111–113) re-raises the new exception through grounded repair. Section
4f chooses an abstention for an Agent with evidence, while T10 covers exhaustion at the
next planning-step boundary. These do not yet specify the same fallback at finalization.

An Agent can receive evidence, make a final-answer call that spends its remaining tokens,
and return malformed grounded JSON. Its repair is then refused. The new re-raise preserves
the type, but a fallback implemented only in the planning loop never sees it: under the
default error policy the Agent still fails, effectively choosing C for this path.

The second probe models the proposed typed refusal and grounded re-raise. It confirms
one provider call, evidence present in that call's prompt, and failure after the typed
repair refusal leaves Agent finalization.

**Plan change:** define B as the Agent-boundary policy for token exhaustion when evidence
is available, including final-answer repair. Query and other nodes can retain their typed
failed outcomes. Preserve already-collected evidence as uncited; never publish the invalid
answer or make another model call. Apply the stated specialist policy consistently.

**Tests:** add the malformed-final-answer / refused-repair case with evidence. Check the
whole user-visible answer, not just `grounding.reason`: the current `NO_EVIDENCE` prefix
also claims that evidence could not be found. Include a nested specialist control and a
no-evidence control so B's scope is explicit.

## D1 and limit wording

B means: “I stopped because the token budget ran out. Here are the sources I collected.”
It preserves evidence without inventing a supported answer or issuing an extra call.
Action-only exhaustion can retain its existing final model call when tokens remain.

This still does **not** make the run ceiling a strict reservation limit. A call admitted
below the ceiling can finish above it. TA05 remains separate; document B as stopping
additional calls after exhaustion is known.

## Verification and limits

- Independently reran the author's original read-only Agent probe: one provider call,
  then a token refusal and Agent failure, as reported.
- Ran two plan counterexamples, both exit 0. They use real `llm_node`, provider adapters,
  Query/Agent handlers and synthetic HTTP. Narrow in-process shims model the proposed
  typed policy outcome/re-raise, because TA04 is not implemented yet.
- The loop probe restores the actual progress/accounting shape after in-process
  cancellation; it is not a worker-process kill or a database durability test.
- No backend suite, frontend suite, hosted CI or production campaign was rerun for this
  plan-only review. There is no claim that a future complete implementation has failed.
- Application files remain unchanged. Evidence: `evals/results/ta04-plan-review-2026-10-30/`.

Sources checked for the plan's analogies:
[AWS error handling](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html),
[OpenAI Agents SDK limits](https://openai.github.io/openai-agents-python/running_agents/),
[OpenAI incomplete generations](https://developers.openai.com/api/docs/guides/reasoning).
These support named limit errors and explicit outcomes, not proof of Relay's replay semantics.
