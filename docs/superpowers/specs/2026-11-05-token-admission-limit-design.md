# TA05: name the run token limit an admission limit, and make its overshoot visible

**Status:** draft for review.
**Date:** 2026-11-05.
**Baseline:** `38f3509` (TA07 closed; the tree equals `main` at `1eb9045`).
**Scope decision:** option A, chosen by the user on 2026-11-05. Name the current contract,
state its rules, and record overshoot on the run. No strict reservation (option C), no
output allowance (option B), no migration. New-run behavior only: no backfill.
**Tier:** P2. Failing tests, then the implementation, then one implementation review.

## 1. Problem and goal

Finding TA05 (`docs/reviews/token-accounting-production-review-2026-10-25.md`): with a
limit of 100, the Worker accepted responses of 80 and 30 tokens and finished at 110 with
`truncated: false`. Admission checks whether past spend has reached the limit; it does not
bound the next call. The review asks to "choose and name the contract" and to make the
response to crossing the limit on the final call explicit.

Two gaps exist today:

- **Wrong name.** `token_budget.py` calls the ceiling "the hard limit". The behavior is an
  admission check, so a run can finish above it.
- **Invisible overshoot.** When the last call crosses the limit and nothing more is
  refused, the run is `success`, not truncated, and nothing on the run says it went over.

**Goal:** the limit has one documented name and rule set, the code and docs use it, and
every finished run that spent more than its limit says so, with the amount.

## 2. Published sources and adaptation

Source check: 2026-11-05. Feasibility probes on `38f3509` (synthetic HTTP, temporary
databases): an invalid limit fails the run before any request; a cancellation requested
while the crossing response returns ends `cancelled` with durable spend 110 (3 of 3 runs).

| Source | Documented behavior | Adaptation to Relay |
|---|---|---|
| [Claude Managed Agents session budgets](https://platform.claude.com/docs/en/managed-agents/budgets) | Before each model request the platform checks consumed cost; the request in flight when the cap is crossed still completes, so the final cost "can land a fraction past the budget". The session stops with `stop_reason: budget_reached`. | The same admission rule. Relay keeps it and names it. |
| [Azure APIM `llm-token-limit`](https://learn.microsoft.com/en-us/azure/api-management/llm-token-limit-policy) | Without prompt estimation, actual usage comes from the response; an exceeded limit is detected from the response and later requests are blocked. Concurrent requests can exceed the limit. | Same rule. Concurrency is TA08 / A5.3 scope. |
| [Amazon Bedrock token counting](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) | Deducts `input tokens + max_tokens` at the start, adjusts during processing, replenishes unused tokens at the end. | The strict style (option C). Named as not provided; an A5.3 prerequisite. |
| [LiteLLM budgets](https://docs.litellm.ai/docs/proxy/users) | The `ExceededBudget` error reports spend above the budget. | Overshoot by the last request is a common gateway result; reporting the amount is useful. |

## 3. Approaches

| Approach | Trade-off | Decision |
|---|---|---|
| A: name the admission limit, state its rules, record overshoot on the run | Fixes the contract and visibility; no behavior change for admission | Selected |
| B: A plus `max_tokens` capped at the remaining budget | Bounds output only, not input; can cut answers short | Deferred |
| C: strict reservation (input count + `max_tokens`, then settle) | Needs per-provider input counting and durable reservations | Deferred to A5.3 |
| Mark an over-limit run `truncated` | Wrong: nothing was skipped | Rejected |

## 4. Required rules

1. **Name.** The run token limit (`RELAY_RUN_TOKEN_LIMIT`) is an **admission limit**. Code
   comments and docs stop calling it a hard limit. The env name and the refusal text are
   unchanged.
2. **Admission (unchanged).** Before each model call, the call is refused when
   provider-reported spend is at or above the limit (`tokens_spent >= limit`). The refusal
   is the typed `TokenBudgetExhausted`, before any provider request.
3. **Overshoot (unchanged, now stated).** An admitted call always completes. On a
   sequential path the run can end above the limit by at most the last admitted call. Each
   call's own size is not bounded by the limit.
4. **Final call (now explicit).** A run that crosses the limit on a call and has no later
   model call to refuse keeps its status: `success` stays `success` and is not truncated,
   because no work was skipped. Truncation still means refused work, as today.
5. **Overshoot record (new).** Every finished run (success, failed, cancelled) whose
   provider-reported spend is above a non-zero limit carries
   `token_limit: {"policy": "admission", "limit": L, "spent": S, "over": S - L}` in its run
   data. It is omitted when the limit is 0 (unlimited) or `S <= L`, so other runs keep
   identical data.
   - `S` is the durable accounting `tokens_spent` read in `finish_run`, under the same
     fence and transaction as the terminal status.
   - `L` is the limit in force when the run finished.
   - An invalid `RELAY_RUN_TOKEN_LIMIT` or an unreadable accounting snapshot gives no
     record and never makes `finish_run` raise: a run must always reach its terminal state.
6. **Unknown usage (unchanged, now stated).** Partial, unreported and unanswered calls
   (TA06) add only their known counts. A provider that reports nothing cannot be stopped
   by this limit; the TA06 counters show it.
7. **Unchanged run-control flow.** No refusal, retry, status, truncation or error type
   changes. Only the run data of an over-limit run gains `token_limit`.

## 5. Proposed implementation behavior

### 5.1 Token budget (`token_budget.py`)

Docstrings say "admission limit" and state rules 2–3 and 6. A small helper returns the
overshoot record for a spend and a limit, or `None` (rule 5). No arithmetic change.

### 5.2 Storage (`storage.py`)

`finish_run` reads the run's durable accounting, asks the helper for the record, and adds
it to the run data with the terminal status. It reads the limit safely: a `ValueError` from
`run_token_limit()` or an unreadable snapshot means no record.

### 5.3 Docs (`docs/operations.md`)

The **Token ceiling** section is renamed in text to the admission limit and states rules
2–6 in one paragraph, with the `token_limit` record and its fields.

### 5.4 Not changed

`execution_policy.py`, `registry.py`, `agent_runtime.py` (the agent's `agent_budget`
warning and truncation stay as they are), `providers.py`, `operator_metrics.py`,
migrations and the frontend. The run API already returns run data, so the record is
visible there; an editor badge is not in scope.

## 6. Verification and stop rule

Budget: at most **7 new collected cases** and **6 mutants**. HTTP is mocked with
`httpx.MockTransport` through `providers.client` (the TA06 helpers); the real Worker and
Store are used.

| ID | Case (rows) | Count | Checks |
|---|---|---|---|
| L01 | The finding: limit 100, two Ollama calls of 80 and 30 | 1 | Two requests; `success`, not truncated; `tokens_spent` 110; `token_limit` = admission, 100, 110, over 10. |
| L02 | Limit 100, three calls; the third is refused | 1 | Two requests; m3 refused with `budget_exhausted` before a request; run `failed`; `token_limit` over 10 on the failed run. |
| L03 | Exactly at the limit: 60 + 40, then a third call | 1 | Third call refused (`>=` rule); no `token_limit` (`over` would be 0). |
| L04 | No overshoot: `unlimited` (limit 0, 80 + 30), `under` (limit 200, 80 + 30) | 2 | `success`; no `token_limit`. |
| L05 | Invalid `RELAY_RUN_TOKEN_LIMIT` (`"lots"`) | 1 | No provider request; the run reaches `failed` with the existing configuration error; `finish_run` does not raise; no `token_limit`. |
| L06 | Cancellation after the crossing call: limit 100, 80 + 30; the provider fixture requests cancellation while it returns the second response | 1 | Run `cancelled` (as `finish_run` does today when cancellation was requested); durable `tokens_spent` 110; `token_limit` over 10 (rule 5 covers every terminal status). |

That is 7 cases. L01, L02 and L06 fail on the baseline by the missing record (a value, not
an interface). L03, L04 and L05 are controls.

Mutants must cover: the record only on the success path; `>` replaced by `>=` (a record at
exactly the limit); a record when the limit is 0; an unguarded limit read in `finish_run`;
`over` computed as `spent`; spend read from the run data instead of the durable accounting.

**Existing-test churn.** No existing test is expected to change. Stop rule: if more than 5
existing tests change, or any changes for another reason, stop and report.

Gates: focused tests, full backend seeds 0–4 in a clean-copy isolation, the deployed
scenario gate, and the five hosted CI jobs. TA02 durability, TA06 status and TA07 cache
tests stay green.

TA05 closes when the bounded tests and mutants pass, the finding's run reports
`token_limit` over 10, docs and code use one name, and hosted CI is green.

## 7. Limits

- The limit does not bound one call's size; option C (reservation) is the A5.3 route.
- Unknown usage still adds nothing (rule 6).
- Concurrent branches can overshoot by more than one call; parallel execution does not
  exist yet (TA08, A5.3).
- The record uses the limit in force at finish; if the operator changes the limit between
  a pause and a resume, it is compared with the new value.
- The editor does not show the record yet.
- Old runs are not backfilled.

## 8. Expected change surface

`token_budget.py` (docstrings and the helper), `storage.py` (`finish_run`), a focused test
module and `docs/operations.md`. No migration, provider, registry, runtime, metrics or
frontend change.
