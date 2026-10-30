# TA04: a token-budget refusal must mark the run incomplete

> Defect: TA04 in [token accounting review](../../reviews/token-accounting-production-review-2026-10-25.md).
> Baseline: `51ba164` (TA01 closed, hosted CI green). Status: revised after review
> (2026-10-30): D1 = B; the loop halt is persisted (4h); B also covers a refused
> final-answer repair (4f). Revised again after
> [the second review](../../reviews/ta04-revised-plan-review-2026-10-30.md): B keeps the
> source check, B applies to free-text agents only, and the halt reason is the same in
> fresh and resumed runs. A closing checklist is in section 10. No code yet.

**Goal:** when the run token budget stops model work, the run says so. Under `on_error`
`continue` or `route`, the run ends `success` **and** `truncated`, with a `budget` cause.
This is the same result that Relay already gives for the action budget.

**One rule:** *work skipped for lack of budget is not the workflow's chosen path.* Relay
already applies this rule to the action budget (`compiler.py:585–592`). This plan applies
it to the token budget.

## 1. The defect in Relay

| Location | What happens today |
|---|---|
| `registry.py:194` (`llm_node`) | The refusal is a plain `ValueError(token_reason(...))`. |
| `execution_policy.py:228–229` | Only `ActionBudgetExhausted` sets `budget_exhausted=True`. A token refusal takes the generic failure path. |
| `compiler.py:575–592` | With `continue` or `route`, a generic failure recovers with **no** truncation, so the run looks complete. |

The review reproduced this with two LLM nodes and a limit of 10. Under `continue` and
`route` the run ended `success`, `truncated: false`
(`evals/results/token-accounting-review-2026-10-25/probe.py`, `recovered_token_refusal`).

### More paths found while planning and in review

1. **The type is lost inside repairs.** `finalize_grounded_answer`
   (`agent_runtime.py:115`) and the agent's structured repair (`agent_runtime.py:426`) catch
   every `ValueError` around the repair call. They rewrap it as "Invalid grounded answer
   contract…" or "Agent output did not match…". A typed refusal that is a `ValueError`
   subclass would lose its type there. This affects Agent and Query nodes.
2. **An agent with evidence fails instead of giving a partial answer.**
   `docs/operations.md:319` says that on token exhaustion "an agent stops, answers from the
   evidence it already has". With the real `llm_node`, this is false. The agent sees
   `out_of_tokens`. It has evidence, so it asks `llm_node` for a final partial answer.
   `llm_node` refuses, and the agent node fails with the plain refusal. I reproduced this
   with synthetic HTTP: 1 provider call, then
   `ValueError: Run token budget exhausted (100 of 100 …)`
   (`evals/results/ta04-plan-2026-10-30/agent_token_probe.py`, run as
   `.venv/bin/python -I <file> "$PWD"`). The existing test
   (`test_agent_run_truncates_gracefully_when_tokens_run_out`) replaces `llm_node` with a
   fake that never checks the budget, so it does not see this. See decision D1.
3. **A loop does not remember why it stopped (review).** Item progress is persisted per
   item (a `run_loop_items` row written with its `loop_item` event). The budget halt
   (`iteration.py:261`, `halt=('budget', index+1, reason)`) is a local variable only. A token
   refusal can happen after the item did work: a `query` body charges an action and retrieves,
   and only then is refused. After a crash, resume restores that item as `failed` and
   starts the next item, which retrieves again. The review modeled this: uninterrupted,
   2 retrievals and 1 model call; interrupted and resumed, 3 retrievals and 1 model call.
   `compact()` (`iteration.py:63`) keeps only `index`, `status`, `value`, `error` and
   `details_omitted`, so a marker inside the entry is lost unless `compact()` keeps it.
4. **A refused final-answer repair makes the agent fail (review).** An agent can collect
   evidence, spend its last tokens on a malformed final answer, and then have no tokens left
   to repair it. Keeping the exception type (4d) still makes that agent fail. D1 = B must
   also apply here.

## 2. What Relay already has (proven, CI-tested)

The action budget has a complete path. TA04 reuses all of it:

| Piece | Location | Tests |
|---|---|---|
| Typed refusal → `ExecutionOutcome.budget_exhausted` | `execution_policy.py:228` | `test_budget_recovery.py` |
| Recovered node → confirmed truncation (`budget_truncation`) | `compiler.py:585–592`, `execution_policy.py:97` | `test_a_refused_node_that_recovers_marks_the_run_incomplete` |
| Failed event carries `budget_exhausted: true` | `compiler.py:579` | `test_the_recovered_event_keeps_the_typed_exhaustion` |
| Run stores a `budget` cause | `worker.py:40` (`run_truncation`) | `test_a_recovered_budget_refusal_is_stored_as_a_budget_cause` |
| Loop halts and reports incomplete | `iteration.py:249–261`, `completeness` | `test_loop_completeness.py` |
| Resume reaches the same verdict | recovered nodes are not checkpointed; the budget is restored | `test_resume_after_the_recovery_reaches_the_same_verdict` |

The token budget is restored on resume too (`compiler.restored_tokens`). So a resumed run
is refused again, not granted a new budget.

## 3. Trusted sources and what each one proves

| Source | Published behavior | Rule for Relay |
|---|---|---|
| [AWS Step Functions: error handling](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html) | Errors have names, so `Retry` and `Catch` can tell them apart. `States.Timeout` is its own name: `States.TaskFailed` "matches any error except for `States.Timeout`". A caught error goes to a fallback state with an error output that names the `Cause`. | A limit refusal needs its own type, not a generic error. A fallback (`route`) must still carry the cause. |
| [OpenAI Agents SDK: running agents](https://openai.github.io/openai-agents-python/running_agents/) | Over the turn limit, "we raise a MaxTurnsExceeded exception". Error handlers for `max_turns` can "return a controlled final output instead of ending the run with the corresponding error." | A budget stop is a typed exception. A controlled fallback is allowed, but it is a separate, explicit outcome. |
| LangGraph `GraphRecursionError` (installed `langgraph/errors.py:67`, raised in `pregel/main.py`) | The library Relay runs on raises a dedicated `GraphRecursionError(RecursionError)` "when the graph has exhausted the maximum number of steps". | The same pattern for Relay's token ceiling: a dedicated subclass of the base error type. |
| [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning) | A generation stopped by `max_output_tokens` returns `status` `incomplete` with `incomplete_details.reason` `max_output_tokens`. This "might occur before any visible output tokens are produced". | A limit-stopped result is labelled incomplete with the reason, never completed. Relay's equivalent is `truncated` with a confirmed cause. |
| [Microsoft API Management token limit](https://learn.microsoft.com/en-us/azure/api-management/llm-token-limit-policy) (from the review) | A response-based limit can overshoot. | Relay's limit stays a soft guard (TA05). This plan does not change admission. |

What we do **not** copy: in Step Functions a caught error lets the execution end
`SUCCEEDED` with no incomplete flag. Relay keeps `success` too, but adds `truncated` and a
`budget` cause, because that is Relay's existing rule for the action budget.

## 4. Changes

### 4a. The type (`backend/app/token_budget.py`)

```python
class TokenBudgetExhausted(ValueError):
    """The run token ceiling refused a model call before it was sent."""
```

It subclasses `ValueError`, as `ActionBudgetExhausted` does. So every existing message
path (`str(exc)` for a `ValueError`) shows the same text as today.

### 4b. The refusal (`backend/app/registry.py:194`)

`raise TokenBudgetExhausted(token_reason(ctx.run))`. The message does not change.

### 4c. The policy (`backend/app/execution_policy.py`)

- Catch `TokenBudgetExhausted` next to `ActionBudgetExhausted` and return
  `budget_exhausted=True`. Neither is retried: the budget is run-wide, so a retry cannot
  succeed.
- `ExecutionOutcome` gets `budget_kind: str = ''` (`'action'` or `'tokens'`). It is used
  only for the loop journal line (4e). The event field stays `budget_exhausted: true`.
- **Attempts are reported honestly.** The action budget refuses *before* the operation
  starts. A token refusal happens *inside* it: a Query node may have retrieved first, and a
  Request reader or Agent may have made a model call before its repair was refused. So for a
  token refusal, the refused attempt counts as executed only if, during that attempt, an
  action was charged or a model call was recorded. Otherwise it counts as not run.
  - Plain LLM node refused at entry → "second did not run: Run token budget exhausted…"
  - Query node refused after retrieval → "stopped after 1 attempt(s): …"
  - In a loop, an item that did no work is `not_run` and halts the loop. An item that did
    some work is `failed`, and the loop then halts with the budget reason. Both already exist
    (`iteration.py:249–261`), but the second one is not persisted; see 4h.

### 4d. Keep the type through repairs (`backend/app/agent_runtime.py`)

Add `except TokenBudgetExhausted: raise` before the generic `except ValueError` at line 115
(`finalize_grounded_answer`) and line 426 (agent structured repair). Nothing else changes in
those wrappers. For a Query node, the typed refusal then reaches the policy (4c). For an
Agent, it reaches the agent boundary: a free-text agent applies B, and a structured agent
lets the typed failure reach the policy (4f).

The Request reader and router need no change. Their repair call is not inside a catch, so
the typed refusal already propagates.

### 4e. Loop journal (`backend/app/iteration.py:317`)

`reason_code` becomes `'token_budget'` or `'action_budget'` from `outcome.budget_kind`.
Today it always says `action_budget`.

### 4f. Decision D1: an agent with evidence runs out of tokens — **decided: B**

The review chose B on 2026-10-30. These were the options:

| Option | Behavior | Cost |
|---|---|---|
| **A. One final call** | Allow exactly one partial-answer call after the ceiling, as the docs say today. | Overshoots the ceiling by one call. Its input can be up to the 60,000-character agent context, not only `max_tokens`. |
| **B. No call (recommended)** | Make no model call. Return the existing truncated abstention with the token reason, and list the collected evidence as uncited sources. The node succeeds; the run is `truncated` with an `agent` cause. | No partial answer text. |
| C. Typed failure | Let `llm_node` raise `TokenBudgetExhausted`. The node becomes a recovered `budget` failure under `route`, and fails under `fail`. | The evidence is lost from the output; the run fails under the default `on_error`. |

**Recommendation: B.** The ceiling is documented as "the cost backstop", and a plain LLM
node already refuses rather than spend more. B keeps that promise, never invents an answer,
and keeps the evidence visible. The abstention reason must state the budget. The current
`immediate_abstention` text says "No evidence passages were retrieved", which would be false
here. Nested specialists follow the same rule, and their parent already records a truncated
child (`agent_runtime.py:356–357`).

The action-budget case does not change: when only actions run out, the final call costs
tokens, not actions, so it is still allowed.

**Central rule (second review):** *a budget stop changes the outcome. It never bypasses the
normal source check or the output contract.*

**Which agents B covers.** B applies only to **free-text agents**. An agent is structured
when it has an `output_schema` or the role `extraction` or `classification`
(`agent_runtime.py:267`). Such an agent promises JSON to downstream nodes, so a prose
fallback would break that contract. The OpenAI Agents SDK also validates a structured
fallback against the agent's output type
([JS guide](https://openai.github.io/openai-agents-js/guides/running-agents/#errors-and-recovery)).
The review allows a schema-valid abstention "when the declared contract supports one".
Relay has no declared abstention shape in a user schema. A value such as `{}` that passes
a schema with no required fields would still be an invented business result. So for every
structured agent, the result is the **typed failure**: the agent raises
`TokenBudgetExhausted` with the token reason, and makes no further call. That failure then
takes the 4c path (a recovered `budget` failure under `route`, a failed run under `fail`).
This is a deliberate narrowing of B.

**B in the implementation.** One helper, `budget_abstention(reason, evidence, truncations)`
in `agent_runtime.py`, is used in two places, both only for free-text agents:

1. **Exhaustion known at a step** (`agent_runtime.py:283–289`): today the agent abstains only
   when it has no evidence. With evidence and `out_of_tokens`, a free-text agent now returns
   `budget_abstention` and makes no model call. A structured agent raises the typed failure.
   When only the action or step budget ran out, the current final call stays.
2. **Final-answer repair refused**: the agent boundary catches `TokenBudgetExhausted` from
   `finalize_grounded_answer` (`agent_runtime.py:422`). A free-text agent returns
   `budget_abstention`; a structured agent lets the typed failure propagate. The
   structured-only wrapper (`agent_runtime.py:425–426`) keeps the type with its own
   re-raise (4d), so it also propagates.

**Source check.** Before `budget_abstention` returns any source, it calls the same gate as a
normal grounded answer: `ctx.platform('verify_vector_sources', …)` (`agent_runtime.py:423`).
"Uncited" does not mean "unchecked". If the gate fails (for example a document was removed
during the call), the agent fails as a normal answer would. That failure is not converted to
token exhaustion. Cancellation and `LeaseLost` stay control flow.

**A valid answer at the limit stays valid.** If the final answer is valid and its call
spends the last tokens, there is no repair and no refusal. The answer is returned as complete.

The output:
- `text`: a budget-specific message that never says "no evidence was found". Draft:
  "The run token budget ran out before an answer could be written. The sources collected so
  far are listed but were not used to answer." Then `Reason:` and the token reason.
- `sources`: the collected evidence after the source check, every item `cited: false`.
- `grounding`: `abstain: true`, the token reason, `compliance: "budget_exhausted"` (no
  code restricts this value), `truncated: true`, `truncation_reason`, and `truncations`.

A nested specialist returns the same output, so its parent records a truncated child.

**Limit of B.** B stops *further* calls once exhaustion is known. It does not make the
ceiling strict: a call admitted under the ceiling can still finish above it. Strict
admission is TA05.

### 4g. Docs (`docs/operations.md`, Token ceiling section)

- A plain LLM, Query, Request reader or Request router node refused by the token ceiling is
  a `budget` failure. Under `continue` or `route` the run is `success` and `truncated`, with
  a `budget` cause. Under `fail` the run fails.
- Correct the agent sentence to match D1 = B, including its limit (no further calls; not a
  strict ceiling), its source check, and that a structured agent gets the typed failure.
- A loop stopped by a budget stays stopped after resume (4h).

### 4h. Persist the loop's budget halt (`backend/app/iteration.py`)

- When an item that did work is halted by a budget (`exhausted` with `attempts>0`), its entry
  gets `budget_halt: "tokens"` or `budget_halt: "action"` **before** it is admitted and saved
  by `record_progress`. The marker is then saved in the same `run_loop_items` row and
  `loop_item` event as the item result. The review confirmed that SQLite keeps this key after
  a reopen, refuses a stale worker, and rolls back the row and the event together.
- `compact()` keeps `budget_halt`. `COMPACT_BYTES` is computed from a compact entry that has
  the longest marker, so the hard result limit still holds. The added reserve is about
  25 bytes per item: at most about 25 KB for 1,000 items, against a 2 MB limit.
- **One interpretation in both paths.** The halt is read from the admitted entry, in a fresh
  run and in a replay, by one function. The loop summary reason comes only from the marker's
  kind, never from the item's `error` text, which compaction can shorten:
  - `tokens` → "Run token budget exhausted; raise RELAY_RUN_TOKEN_LIMIT."
  - `action` → "Run-wide agent action budget exhausted; raise AGENT_RUN_BUDGET."
  The item's own `error` keeps the full message when there is room. A fresh run and a resumed
  run therefore give the same summary, compacted or not.
- Replay: when a restored entry has `budget_halt`, the loop sets
  `halt=('budget', index+1, <kind reason>)` and stops, exactly as the fresh run did.
- The `not_run` case (the refusal came before any work) records no entry. It stays as it
  is: on resume the same refusal happens again before any work, with the same live message.
- The action budget uses the same marker. Today, after a resume, its next item is refused
  before any work, so the result is the same, but the marker makes both kinds follow one rule.

## 5. What already works and needs no change

- `compiler.py:575–592`, `worker.run_truncation` and the stored `truncation_causes` take a
  typed outcome as it is.
- Resume: the token baseline is restored, so the recovered node is refused again with the
  same verdict.
- The attached-agent check in `agent_runtime.py:350` matches "budget exhausted" in the error
  text. The token message contains it, so a specialist refused by the token ceiling already
  adds a truncation. I will not change this string match here.
- A free path (guard refusal, abstention without evidence) still resolves before any token
  check (`execution_policy.py:195–200`).

## 6. Tests (write first)

- **D (defect reproduction):** fails on `51ba164`, passes after the fix.
- **C (control):** passes on both.

All model calls go through the real `llm_node` and a substitute provider (HTTP mock or
`providers.ollama_chat`), never a fake `llm_node`. A fake `llm_node` hid the agent finding.

New file `backend/tests/test_token_budget_recovery.py`. Its helper is `token_flow(policy)`:
`input → first (llm) → second (llm, on_error=policy)`, limit 10, and `first` reports 8 + 2.

| # | Kind | Test |
|---|---|---|
| T1 | D | `continue` and `route`: 1 provider call; `run_truncation` is truncated, confirmed, and names `second` and `RELAY_RUN_TOKEN_LIMIT`; the reason says "did not run". `route` takes the fallback. |
| T2 | D | `continue` and `route`: the failed event has `recovered: true`, `budget_exhausted: true` and the token message. |
| T3 | C | `fail`: the run fails with the token message. |
| T4 | C | An ordinary provider error under `continue`/`route` is a recovered failure, not truncation. |
| T5 | D | Worker and SQLite: status `success`, `truncated: true`, `truncation_causes == [('second','budget','confirmed')]`. |
| T6 | D | Resume after a crash at the recovered event: the same verdict, and no extra provider call. |
| T7 | D | Loop with a `query` body over 3 items. Item 0 spends the limit; item 1 retrieves and is refused at generation. Then: the loop halts, its summary has `budget_exhausted: true`, item 2 did not run, and the run is truncated. Counts: 2 retrievals, 1 model call. |
| T7b | D | T7 through the worker and SQLite, with a crash right after item 1's progress row is saved, then resume. Run it under `on_item_error` `continue` and `stop`. The resumed run equals the uninterrupted one: **2 retrievals, 1 model call, the same action counter, and the same complete loop summary** (reason text included) and run verdict. |
| T7c | D | Compaction plus crash and resume, in one test. Force item 1's marked entry through compaction (a small result limit set by the test), crash after its progress commit, then resume. Compare with the fresh, compacted, uninterrupted run: the same complete summary and reason, the same calls and the same actions. Also: `compact()` keeps the marker, and `COMPACT_BYTES` covers it. |
| T8 | D | Query node: the first grounded answer is invalid and spends the limit, and the repair is refused. The event has `budget_exhausted: true` (not "Invalid grounded answer contract"), and the reason says "stopped after 1 attempt(s)". |
| T9 | D | Structured agent **without evidence** (reaches the structured-only wrapper, `agent_runtime.py:425`): an `output_schema` of `{"amount": number}`, an invalid first answer that spends the limit, and a refused repair. Result: typed failure (`budget_exhausted: true`), 0 extra provider calls, and no business output. Repeated for the `extraction` and `classification` roles (a JSON object is required). |
| T9b | D | Structured agent **with evidence** (grounded wrapper): the same, with the typed failure; no prose in the output. |
| T9c | D | Free-text agent with evidence, final answer malformed, repair refused: B. The node succeeds with `budget_abstention`; the **complete visible text** is asserted; the sources are uncited; the grounding is truncated with the token reason. |
| T10 | D | Free-text agent with evidence, exhaustion known at a step: B with no further call (1 provider call). The **complete visible text** is asserted: it never says "could not find matching evidence". |
| T10b | D | A structured agent with evidence, exhaustion known at a step: the typed failure and no further call. |
| T10c | D | A nested free-text specialist under B: the parent's grounding records the truncated child, and no call is made after exhaustion. |
| T10d | C | Action budget only (tokens remain): the agent with evidence still makes its final partial-answer call, as today. |
| T14 | D | Source check at both B return sites (step and refused repair): the source is removed during the call. The agent fails at the source check, and no removed source appears in any success output. Includes one nested-specialist case. |
| T14b | C | The same with an unchanged source: B succeeds, and the source check runs exactly once. |
| T15 | C | A valid final answer whose call spends the last tokens: the node succeeds, complete and not truncated. |
| T11 | D | Request reader: the first answer is invalid and spends the limit; the repair raises `TokenBudgetExhausted` with the unchanged message. |
| T12 | C | A node with `retry.attempts=2` refused by tokens is not retried. |
| T13 | C | The existing action-budget tests (`test_budget_recovery.py`, `test_run_causes.py`) pass unchanged. |

The review's `recovered_token_refusal` probe asserts the old result, `truncated: false`. It
is not run and not changed. A revised copy goes in `evals/results/ta04-fix-2026-10-<day>/`
and asserts `truncated: true` under `continue` and `route`.

## 7. Mutants (scratch copies only)

| Mutant | Must be caught by |
|---|---|
| M1: drop `TokenBudgetExhausted` from the policy catch | T1, T2, T5 |
| M2: `llm_node` raises a plain `ValueError` again | T1, T2, T11 |
| M3: remove the re-raise in `finalize_grounded_answer` | T8, T9b, T9c |
| M4: remove the re-raise in the structured-only repair | T9 |
| M5: a token refusal never counts its attempt as executed | T7, T8 |
| M6: a token refusal always counts its attempt as executed | T1 ("did not run") |
| M7: the agent with evidence calls the model again (B reverted at the step) | T10 |
| M8: the agent boundary does not catch a refused repair | T9c |
| M9: `budget_abstention` uses the no-evidence text | T9c, T10 (complete text) |
| M10: replay ignores `budget_halt` | T7b |
| M11: `compact()` drops `budget_halt` | T7c |
| M12: `budget_abstention` skips the source check | T14 |
| M13: B also applied to structured agents | T9b, T10b |
| M14: the summary reason comes from the item's `error` text | T7c |

## 8. Out of scope

TA02, TA03, TA05 (strict reservation), TA06 (missing usage), TA07, the monotonic snapshot,
and the attached-agent string match (it already matches the token message).

**Found during the second review, existing behavior, not changed here.** A structured agent
can already return prose in two places: the no-evidence abstention when any budget ends at a
step (`agent_runtime.py:287–291`), and an ordinary grounded abstention
(`finalize_grounded_answer`, `agent_runtime.py:116–121`). Both break the same output
contract as R02. They apply to the action budget and to normal runs, not only to tokens, so
they need their own step.

## 10. TA04 closing checklist

TA04 is closed when each line below has a passing test, and not before:

1. A typed refusal goes through every model call and repair path: LLM, Query, Request reader,
   Request router, Agent step, Agent grounded repair, and Agent structured repair (T1, T8,
   T9, T9b, T11, plus a router test).
2. The fallback keeps the source check and the output contract (T9, T9b, T10b, T14, T14b).
3. A compacted crash and resume gives the same results and the same spend as a fresh run
   (T7b, T7c).
4. A valid answer that ends exactly at the limit stays valid (T15).

Anything outside this list is a new finding for a later step, not a reason to reopen TA04.

## 9. Done when

1. Every D test fails on `51ba164` and passes on the fix. Every C test passes on both. All
   mutants are caught.
2. Backend `PYTHONHASHSEED` 0–4 passes. The isolation copy passes.
3. The deployed scenario gate passes.
4. Hosted CI is green on the pushed commit.

**Size:** one commit. About 80 changed lines in five application files (`token_budget.py`,
`registry.py`, `execution_policy.py`, `agent_runtime.py`, `iteration.py`), plus the tests
and the docs.
