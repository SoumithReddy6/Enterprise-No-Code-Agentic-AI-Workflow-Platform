# TA06: count model calls whose usage is missing, and mark incomplete totals

**Status:** approved for tests-first implementation on 2026-11-05, after the design review
(`docs/reviews/ta06-design-review-2026-11-05.md`). TA06-D01 (dispatch boundary), TA06-D02
(response receipt) and TA06-D03 (usage-dictionary contract) are incorporated in §4.2,
§5.1, §5.2 and §6. The approval's two execution notes (retry wording, explicit case
allocation) are in §6.
**Date:** 2026-11-05.
**Baseline:** `bcc8d9b1e1e421a144b2738ce0630a21150c9061` (`main`; TA03 merged).
**Scope decision:** new-run behavior only, as the user decided for TA03. No backfill of
events or `run_token_usage` rows, and no run deletion. Rows written before this change keep
their meaning, and §5.5 documents it.
**Tier:** P2. Failing tests, then the implementation, then one implementation review.

## 1. Problem and goal

Finding TA06 (`docs/reviews/token-accounting-production-review-2026-10-25.md`): three valid
model responses with no usage metadata succeeded under a one-token ceiling. Accounting and
metrics reported zero tokens **and zero calls**. Relay treated "not measured" as "free" and
also lost the fact that the call happened.

The current code has three causes:

1. `providers.record_usage` skips a missing or invalid count without a mark, so a response
   with one count (the Ollama cached-prompt case) looks complete.
2. `registry.account_usage` returns early when no count was recorded, so `calls` is not
   incremented and no outcome usage exists for the invocation.
3. A request that was dispatched and got no response (read timeout, dropped connection,
   cancellation in flight) leaves no record. Its consumption is unknown, because a provider
   can continue to process after the client stops waiting. A response that arrived with an
   error status, an unreadable body or a malformed `usage` value leaves no record either.

**Goal:** every model call that dispatched a request (§4.2) is counted at the invocation that made it, with
a usage status: reported, partial, unreported or unanswered. Token totals stay the known
subtotal. When a window or an event contains incomplete calls, the API and the UI show that
the total is a lower bound. No count is estimated, and no business decision or token-ceiling
decision changes.

## 2. Published sources and adaptation

Source check: 2026-11-05.

| Source | Documented behavior | Adaptation to Relay |
|---|---|---|
| [openai-python `ChatCompletion`](https://github.com/openai/openai-python/blob/main/src/openai/types/chat/chat_completion.py) | `usage: Optional[CompletionUsage] = None`. | A completion with no usage is valid. Count it as an unreported call; do not refuse it. |
| [anthropic-python `Usage`](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/usage.py) | `input_tokens: int` and `output_tokens: int` are required; the cache fields are optional. | A Claude response without them breaks the contract, but it was still paid for. Count it as partial or unreported; do not discard the answer. (The rendered docs page could not be read; the official SDK type is the source.) |
| [Ollama #2068](https://github.com/ollama/ollama/issues/2068), [#3427](https://github.com/ollama/ollama/issues/3427), [PR #5371](https://github.com/ollama/ollama/pull/5371) | `prompt_eval_count` was absent on a repeated prompt (cache), and absent or wrong on other calls. PR #5371, "Return Correct Prompt Eval Count Regardless of Cache Prompt", closes #2068. | Historical compatibility evidence: older or other Ollama-compatible servers can send one count. Mark it partial. Tests use synthetic responses and do not claim the current Ollama has the defect. Relay cannot detect a wrong value. |
| [HTTPX exceptions](https://www.python-httpx.org/exceptions/), [HTTPX event hooks](https://www.python-httpx.org/advanced/event-hooks/) | Request preparation errors (for example `InvalidURL`) are separate from transport errors. `ConnectError`, `ConnectTimeout` and `PoolTimeout` happen while a connection is opened or obtained. Request hooks run after preparation and before the network send. | The dispatch boundary in §4.2 is after request preparation. Connection-phase failures are classified as not delivered. Checked locally on httpx 0.28.1: `build_request` with a nonnumeric port raises `InvalidURL` with zero transport calls, and a non-streaming `send` returns with the body read. |
| [SWE-agent #44](https://github.com/princeton-nlp/SWE-agent/issues/44) | The fix for the absent count was to assume 0 input tokens. | The error that TA06 removes: absent is not zero. |
| [OTel GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md) | `gen_ai.usage.input_tokens` is Recommended, not Required; detail counts are recorded "when reported by the provider". | An absent count is unknown. New counters are omitted when zero, like the OTel conditional attributes. OTel is not a dependency. |
| [Langfuse token tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking) | Ingested usage takes priority. Tokenizer estimates are inferred usage, kept apart. | Do not estimate. Any future estimate is a separate, labeled field, outside this design. |
| [LiteLLM #30420](https://github.com/berriai/litellm/issues/30420) | Seen only as a search excerpt: the estimate fallback did not run because a 1-token value was truthy. The design review could not fetch it. | Supporting example only. No rule depends on it. |
| [OpenAI streaming usage](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events), [Azure APIM token limit](https://learn.microsoft.com/en-us/azure/api-management/llm-token-limit-policy) | With streaming, usage arrives only in the final chunk; APIM estimates prompt tokens. | Not applicable: all three Relay transports are non-streaming. Recorded as a limit (§7). |

The status names and the counting rules are Relay's design. They are not a claim about the
internals of those systems.

## 3. Approaches

| Approach | Trade-off | Decision |
|---|---|---|
| Status counters on the existing usage totals, carried by the TA03 per-invocation counters and three new `run_token_usage` columns | Fits current accounting, outcome events and SQL. One additive migration. | Selected |
| A durable record for each provider call | Exact identity of each call; much larger lifecycle, schema and durability work | Deferred (same deferral as TA03) |
| Estimate missing counts with a tokenizer | Fills the gap with a number that is not reported; adds a dependency and its own bugs | Rejected (§2) |
| Refuse a response without usage | Discards an answer that was paid for, and blocks providers whose usage is optional | Rejected |

## 4. Required rules

1. **A call counts once.** A call is one `registry.call_model` invocation. Provider-internal
   retries inside `with_retries` are not separate calls (unchanged, §7).
2. **Two boundaries per HTTP attempt** (TA06-D01, TA06-D02):
   - **Dispatch:** the prepared request is handed to the HTTP client's send. Application
     validation, credential resolution and request preparation (for example `InvalidURL`)
     are before it. Dispatch is a client-side fact. It does not prove that the provider
     received anything.
   - **Response receipt:** the non-streaming send returned a complete response. This is
     before the HTTP status check, JSON decoding and usage validation, so it covers an
     error status, an unreadable body and a malformed `usage` value too.
   - An attempt that ends with `httpx.ConnectError`, `ConnectTimeout` or `PoolTimeout` is
     **not delivered**: no connection carried the request.

   **Status of a call**, decided once, at the end of `call_model`, over all its attempts:
   - `reported`: a response was received, and both counts are valid (`int`, `>= 0`).
     Zero is a valid reported value; it is not missing.
   - `partial`: a response was received, and exactly one count is valid.
   - `unreported`: a response was received and neither count is valid. This includes an
     HTTP error status, an invalid JSON body and a `usage` value of the wrong type (for
     example `usage: []`, which still raises the existing `ProviderResponseError`). It also
     includes a call that returned an answer with no evidence recorded: a returned answer
     proves that a response arrived. Counts come from the last received response.
   - `unanswered`: at least one attempt was dispatched and not proven not-delivered, and no
     response was received (for example a read timeout, a write timeout, a dropped
     connection, or cancellation after dispatch). The provider may or may not have the
     request, so its consumption is unknown.
   - No status: no attempt passed dispatch, or every dispatched attempt was not delivered.
     Nothing is counted, because nothing reached a provider.
3. **Counters.** On each totals dict that `account_usage` updates (the checkpoint owner's
   `usage`, `usage_by_invocation`, and each `by_model` row):
   - `calls` counts reported, partial and unreported calls: calls that got a response.
   - `partial_calls` and `unreported_calls` are subsets of `calls`.
   - `unanswered_calls` is separate. Attempted calls = `calls` + `unanswered_calls`.
   - `prompt_tokens` and `completion_tokens` add only valid reported counts (unchanged).
   - The three new counters are omitted while zero. A totals dict with only reported calls
     is byte-identical to the baseline.
4. **Attribution** follows TA03 without change. The call's status counters go to the
   invocation that made the call, through `usage_for` / `with_own_usage`. An outcome that
   has only an unanswered call carries usage, because the call happened.
5. **Durability.** Any counted call, including an unanswered one, marks accounting changed
   and is saved at the existing persistence points (`record_spend` after a completed or
   failed call; the next persistence point after cancellation). No new write path.
6. **Token ceiling unchanged.** `tokens_spent` still sums only valid reported counts, and
   the ceiling stays soft. A run with partial, unreported or unanswered calls is not capped
   on that consumption. Strict handling of unknown consumption belongs to TA05.
7. **No control-flow change.** A status never causes a retry, a refusal, a fallback, an
   extra model call or a different business decision. A response that Relay rejects
   (refusal, empty answer) is still counted with its status, as today.

## 5. Proposed implementation behavior

### 5.1 Transports (`providers.py`)

In `ollama_chat`, `claude_chat` and `openai_chat`, replace `http.post(...)` with
`http.build_request(...)` and `http.send(request)`, with the same arguments, timeouts and
exception handling. Record dispatch after `build_request` returns, and response receipt
right after `send` returns. Record not-delivered in the existing exception handling for
the three connection-phase errors. Retry and exception behavior stay as they are.
(`build_request` and `send` are an implementation suggestion; any boundary with the same
observable meaning and tests is acceptable. A request event hook is equivalent for
dispatch.)

`record_usage` keeps its token behavior and also records which counts were valid. Missing
and invalid counts are not written as zero.

**Usage-dictionary contract (TA06-D03).** The `usage` dict that a transport receives stays
token-only. Direct callers that pass a plain dict, and existing tests that compare it with
`{}` or with exact token dicts, see no change. The dispatch, receipt, not-delivered and
validity evidence is private to the managed call. One acceptable form: `call_model`
passes a `dict` subclass that keeps the evidence in attributes, not keys, and the transport
helpers write evidence only to that type. The evidence never reaches accounting, events or
SQL. Names and factoring are the builder's choice. Test stubs that call `record_usage` keep
working.

### 5.2 Classification and accounting (`registry.py`)

`call_model` decides the status in all three exits (normal return, exception, cancellation)
and passes it to `account_usage`. `account_usage` returns early only when the call has no
status (rule 4.2, last item). The existing `error_type` journal behavior stays. The
`model.usage` journal line adds the status, and omits a token field that was not reported.

### 5.3 Metrics (`operator_metrics.py`, migration `0006`)

- Migration `0006_usage_status`: add `partial_calls`, `unreported_calls` and
  `unanswered_calls` to `run_token_usage`. Each is `Integer`, `nullable=False`,
  `server_default='0'`, through `batch_alter_table`, as in `0002`. Downgrade drops the
  three columns; tokens and `calls` survive.
- `RunTokenRecord` declares the same columns. `test_models_and_migrations_agree` and the
  frozen baseline tests must pass unchanged.
- `usage_rows` reads the three counters with the same integer bounds as the existing fields,
  default 0. The metrics query sums them per provider/model.
- `definitions['tokens']` states that the token totals are a lower bound when any of the
  three counters is non-zero, that unanswered calls are not in `calls`, and that rows from
  before revision 0006 have zero counters and count only reported calls.

### 5.4 Event display (`frontend`)

`frontend/lib/workflow.ts` adds the three optional counters to the event `usage` type. The
event row in `workflow-editor.tsx` today shows `prompt+completion tok`. With non-zero
counters it must show that the count is incomplete. If the event has unanswered calls and
no response, it shows that the tokens are unknown, not `0+0 tok`. The wording is the
builder's choice.

### 5.5 Old records

No backfill. Events and rows written before this change have no status counters, so they
read as zero. Their `calls` count only calls with recorded usage. `docs/operations.md` states
this and that a metrics window spanning the change mixes both meanings.

## 6. Verification and stop rule

Budget: at most **16 new collected cases** (backend and frontend together, including
parametrization) and **8 mutants**. HTTP is mocked with `httpx.MockTransport` through
`providers.client`, as `test_providers.py` does. The real `call_model`, Worker and Store are
used where a test checks events or metrics. No live provider calls.

Required coverage, allocated to exactly 16 collected cases. Each parametrized row is one
case. "Worker" means a one-node workflow run through the real Worker and Store, checked
through the outcome event and the operator metrics rows.

| ID | Case (rows) | Count | Checks |
|---|---|---|---|
| V01 | Original reproduction: three valid Ollama responses with no counts, one-token ceiling (Worker) | 1 | The run succeeds, as on the baseline (soft ceiling). Metrics: 3 calls, 3 unreported, 0 tokens, instead of 0 calls. |
| V02 | One count missing or none: `ollama-partial` (no `prompt_eval_count`), `claude-partial` (only `output_tokens`), `openai-null` (`usage: null`) (Worker) | 3 | Partial, partial, unreported. Known counts still add. |
| V03 | Read timeouts: `exhausted` (3 × `ReadTimeout`), `then-zero` (`ReadTimeout`, then a success with both counts `0`) | 2 | `exhausted`: 1 unanswered, 0 calls, existing error. `then-zero`: 1 call, reported (zero is not missing), no unanswered: one call, not one HTTP attempt. |
| V04 | Cancellation while the dispatched request is held (Worker) | 1 | 1 unanswered on the cancelled invocation's outcome (TA03 attribution). |
| V05 | No status (TA06-D01): `invalid-url` (Ollama URL with a nonnumeric port), `connect-error` (one attempt, as today), `connect-timeout` (3 × `ConnectTimeout`, retries exhausted) | 3 | Zero transport calls for `invalid-url`. No counter and no usage on the outcome for any row. Existing errors unchanged. |
| V06 | Received but unusable (TA06-D02): `claude-usage-list`, `openai-usage-list` (`usage: []` with a valid answer), `http-401`, `invalid-json` (Worker) | 4 | 1 call, 1 unreported on the failed event. The existing error and stop behavior are unchanged. The usage-list rows also cover the rejected-answer requirement: a response that Relay rejects carries its unreported call on the failed event. |
| V07 | Migration `0006` and metrics | 1 | Upgrade adds the columns with 0 on an existing row; downgrade then upgrade restores head; sums are per provider/model. |
| F01 | Frontend event row | 1 | Incomplete or unknown count, not `0+0 tok`. |

Retry counts follow the current adapters: `ConnectError` is one attempt, and `ConnectTimeout`
and `ReadTimeout` are retried up to three attempts. A missing credential is a pre-dispatch
failure like `invalid-url`; it is not a separate case. The dictionary contract (TA06-D03)
is proved by the existing `test_providers.py` exact-dict assertions passing without edits,
and needs no new case.

Baseline defect tests must fail for the intended count mismatch. A missing new interface is
not accepted as proof. Migration and frontend tests that need the new column or field are
controls written to pass after the change and are recorded as such.

Mutants must cover: early return on empty counts (the call vanishes again); a missing count
recorded as reported; zero treated as missing; unanswered calls not counted; a retry counted
as an extra or unanswered call; dispatch recorded before request preparation, or a
connection-phase failure counted; response receipt recorded only after usage validation
(so `usage: []` becomes unanswered); `usage_rows` dropping the new counters. The event
display is covered by the frontend case; a ninth mutant for it is optional, outside the
8-mutant budget.

**Existing-test churn.** Rule 4.2 counts a stub that returns an answer without recording
usage as an unreported call. Existing tests that assert exact usage dicts or call counts for
such stubs can change. The builder runs the full suite on the change, updates only
expectations that change for this reason, and lists each one in the evidence README.
**Stop rule:** if more than 10 existing tests change, or any test changes for another
reason, stop and report before continuing.

Gates: focused tests, full backend seeds 0–4 in a clean-copy isolation, frontend unit tests,
the deployed scenario gate, and the five hosted CI jobs on the pushed commit. TA02's tests
and TA03's `test_usage_attribution.py` stay green.

TA06 closes when the bounded tests and mutants pass, the reproduction shows counted calls
with an incomplete marker, the ceiling behavior is unchanged, and hosted CI is green.

## 7. Limits

- Relay cannot detect a count that a provider reports wrong (Ollama #3427).
- `unanswered` is a client-side classification. Relay cannot prove that a dispatched
  request reached the provider, or that a not-delivered one did not partly reach a proxy.
- `unreported` includes error-status responses. Providers usually do not charge for them,
  but Relay does not assume either way.
- Provider-internal retries inside one call stay uncounted. If one attempt times out and a
  later attempt succeeds, the call is reported, and the timed-out attempt's consumption is
  not shown.
- Streaming is not supported; a future streaming transport needs its own rule for
  final-chunk usage.
- Claude and Ollama cache counts are TA07. Strict reservation and unknown consumption under
  a strict ceiling are TA05. Concurrent settlement is TA08.
- A worker that dies after a response and before a durable save still loses that call.
- No per-call durable record; the counters are aggregates per invocation and per model.

## 8. Expected change surface

`providers.py` (dispatch and receipt boundaries, private call evidence), `registry.py` (status and counters),
`operator_metrics.py` (columns, rows, query, definition), `migrations/versions/0006_usage_status.py`,
`frontend/lib/workflow.ts` and `frontend/components/workflow-editor.tsx` (event display),
a focused backend test module, one frontend test, listed existing-test updates, and
`docs/operations.md` (lines on missing usage, the token ceiling and metrics). No change to
`execution_policy.py`, the compiler, agent or loop runtimes, the Worker, the token-ceiling
rule, accounting snapshot validation or business authorization.
