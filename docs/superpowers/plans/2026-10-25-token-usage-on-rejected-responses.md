# TA01: count tokens when a provider answer is rejected

> Defect: TA01 in [token accounting review](../../reviews/token-accounting-production-review-2026-10-25.md).
> Baseline: `347c82f`. Status: revised after the [plan review](../../reviews/ta01-plan-review-2026-10-25.md)
> (R01–R04 and the cancellation note). No code yet.

**Goal:** when a provider returns HTTP 200 with usage, Relay counts that usage, even when
Relay rejects the answer. Today Relay counts usage only for an answer it accepts.

**One rule:** *usage belongs to the provider response, not to the answer.* Whether the
answer is usable is a different question from whether the provider did the work.

## 1. The defect in Relay

The defect has two parts. Both must change. If you fix only one, the tokens are still lost.

| Layer | Location | What happens today |
|---|---|---|
| Adapter | `providers.py:165–171` (Ollama), `:265–282` (Claude), `:317–331` (OpenAI) | The adapter checks the text first. When the text is bad, it raises before it calls `record_usage`. |
| Caller | `registry.py:178–188` (`llm_node`) | `account_usage` and `record_spend` run only after a successful return. When the call raises, the usage dictionary is discarded. |

Every model call goes through `llm_node`. This includes Agent planning, Request reader,
Request router, query generation and repairs. So one fix covers all model nodes.

**Relay already has this rule one level higher.** The Request reader and Request router
count the first answer, then they validate its JSON (`request_reader.py:203–206`,
`request_router.py:103–106`). An invalid answer from them is counted, and the repair
call is counted too. Only provider-level rejection loses the tokens. So this fix makes
the adapter follow the rule that the reader and router already follow.

The repair policy does not change. The first `llm_node` call in the reader and router is
outside their validation `try` block. So a provider rejection (for example an empty answer)
escapes with no repair, before and after this fix. Only returned text that fails JSON
validation gets the one repair.

## 2. Trusted sources and what each one proves

Only official provider documents and the OpenTelemetry specification are used as
evidence. Community posts (for example about LiteLLM failure callbacks) agree, but this
plan does not depend on them.

| Source | Published behavior | Rule for Relay |
|---|---|---|
| [Anthropic: handling stop reasons](https://docs.claude.com/en/api/handling-stop-reasons) | A refusal is a normal HTTP 200 response. A `max_tokens` stop is valid but truncated. An empty `end_turn` response can occur. Successful responses include `usage`. | Relay rejects some of these answers. The provider still reports usage, so Relay must count it. |
| [OpenAI: structured outputs](https://platform.openai.com/docs/guides/structured-outputs) and [Chat Completions](https://platform.openai.com/docs/api-reference/chat/create) | A refusal comes back in a `refusal` field and `content` can be null. A length stop can return empty visible text, and the usage includes the hidden reasoning tokens. | TA01's exact case: 100 + 50 tokens, empty text, `finish_reason: length`. Count 150. |
| [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md) | `prompt_eval_count` and `eval_count` are at the top level of the body, not inside `message`. | The counters do not depend on the message. Read them before the message checks. |
| [OpenTelemetry GenAI spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) | `error.type` is Conditionally Required "if the operation ended in an error". `gen_ai.usage.*` are separate Recommended attributes on the same span. Instrumentations "SHOULD make a best effort to populate" input tokens. A failed generation maps to the finish reason `error`. | One operation can have both an error and usage. Record both on the same event: `model.usage` gets `error_type` when the answer was rejected. |
| [Amazon Bedrock token quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) | Quota usage and billing are separate. You pay "only for your actual token usage". | The run budget counts actual reported usage. Reservation (input + max_tokens) is a different feature and is not part of this fix. |

What we do **not** copy:
- Bedrock's reservation. It is for strict admission (TA05), which is planned with A5.3.
- An estimate for a call that returned no usage (an HTTP error, a timeout). This step adds
  no usage when no usage was returned. A timeout's consumption is unknown: OpenAI states
  that "a request may still run after its client times out"
  ([per-run spending controller](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api)).
  Handling unknown consumption is TA06.

## 3. Changes

### 3a. Adapters (`backend/app/providers.py`)

In each adapter, use this order: parse the body, check the usage shape, record usage, then
check the content.

- **Ollama:** move `record_usage(usage, body.get('prompt_eval_count'), body.get('eval_count'))`
  to the line after `_json_object`, before the message checks.
- **Claude and OpenAI:** move the usage block (the invalid-usage check, then
  `record_usage`) to the line after `_json_object`, before the content checks.
- Keep the current behavior for invalid usage: a non-object `usage` still raises
  "invalid usage metadata" and records nothing. Invalid counts (negative, not int) are
  still ignored by `record_usage`.
- Error messages for content stay the same.

`with_retries` stays safe. It retries only `ProviderBusy`. `_raise_retryable` raises that
before the body is parsed, so a retried attempt never adds usage. A test proves this.

### 3b. Caller (`backend/app/registry.py`)

Replace the three copies of `with_retries → account_usage → record_spend` with one helper:

```python
async def call_model(ctx,config,call):
    """One provider call. Usage is counted for every response the provider reported,
    used or rejected: a rejected answer was still paid for."""
    usage={}
    try:
        text=await with_retries(lambda:call(usage),provider=config.provider,model=config.model)
    except Exception as exc:
        account_usage(ctx,usage,config,error_type=type(exc).__name__);await record_spend(ctx);raise
    except asyncio.CancelledError:
        # Keep the spend in memory. The next persistence point writes it;
        # awaiting a write here would delay the cancellation.
        account_usage(ctx,usage,config,error_type='cancelled');raise
    account_usage(ctx,usage,config);await record_spend(ctx);return text
```

- The cancellation branch catches `asyncio.CancelledError` only, not every
  `BaseException`. `PersistedNodeCancellation` is a subclass, so it is included.
  Cancellation stays non-retryable and propagates at once. `LeaseLost` is a
  `RuntimeError`, so it takes the `Exception` branch, where `record_spend` is already
  best-effort.
- `account_usage` already returns when `usage` is empty. So a call that returned no usage
  (an HTTP error, a timeout) adds nothing, as today.
- `account_usage` gets an optional `error_type`. It is added to the `model.usage` journal
  line only. Run totals and event shapes do not change.
- A rejected call counts as one call in `calls`, because the provider did one call.

### 3c. What already works and needs no change

- The failed node event takes usage from `usage_for(run, identity)`
  (`compiler.py:577`). This is the invocation's own usage, so a failure is not counted
  twice. Operator metrics read event usage for any status, so rejected tokens reach
  `run_token_usage`.
- A provider response error is not a `TransientToolError`. So a node-level retry does not
  run again and does not double the count.
- `tokens_spent` and `accounting_snapshot` read `run['usage']`. So the counted tokens reach
  the budget check and the durable accounting with no other change.

### 3d. Docs

`docs/operations.md`, lines 81 and 406: say that a rejected answer with reported usage
is counted. A call that returned no usage (an error, a timeout) adds nothing; its real
consumption is unknown (TA06).

## 4. Tests (write first)

Two kinds of test:
- **Defect reproduction (D):** fails on `347c82f`, passes after the fix.
- **Control (C):** the behavior must not change. It passes on `347c82f` and after the fix.

**Adapters (D)** (`backend/tests/test_providers.py`, existing `mocked_http` fixture). Each case
is HTTP 200 with usage. Each asserts that `ProviderResponseError` is raised **and** that the
usage dictionary has the counts.

| Provider | Rejected response |
|---|---|
| Ollama | no `message` object; empty `content` (a thinking model with an empty answer) |
| Claude | `content` is not a list; an unsupported block; `content: []` with `stop_reason: refusal` |
| OpenAI | `content: null` with `refusal`; `content: ""` with `finish_reason: length` (TA01's exact case); empty `choices` |

Also:
- (C) Claude and OpenAI: a non-object `usage` still raises and records nothing.
- (C) A 429, then a 200: usage is counted once.

**Caller** (new tests in `backend/tests/test_token_budget.py`):
- (D) A fake adapter records 100/50, then raises `ProviderResponseError`. Then:
  - `run['usage']` has 150 tokens and `calls == 1`;
  - `record_spend` persists `tokens_spent == 150` once;
  - the journal `model.usage` line has `error_type`.
- (D) The same with `CancelledError`: the usage is in memory, `calls == 1`, persist is not
  called, and the cancellation propagates.
- (C) Success: 100/50 is counted exactly once (`calls == 1`), and persist is called once.
- (D) Limit 150: a rejected 150-token call, then the next `llm_node` is refused with the
  token-budget message before any provider call.

**Reader and router boundaries** (`backend/tests/test_request_reader.py` and the router tests):
- (D) The provider returns an empty answer with usage. Its usage is counted once. The
  `ProviderResponseError` escapes. There is one provider request and no repair.
- (C) The provider returns nonempty invalid JSON, then a valid reading. The existing single
  repair runs, and both responses are counted exactly once.

**Worker (D)** (durable, through the existing worker fixture): a run that fails on a rejected
answer has accounting `tokens_spent == 150`, and its `run_token_usage` rows sum to 150.

**Auditor probe:** the historical `rejected_paid_response` asserts the broken result
(`tokens_spent == 0`) and returns a fixed `accounted_tokens: 0`. Do not run it and do not
change it. Instead:
- the permanent regression test is the caller (D) test above, with the probe's exact
  response (empty content, `finish_reason: length`, 100 + 50 tokens);
- a revised copy goes in a new evidence folder
  (`evals/results/ta01-fix-2026-10-<day>/`). It measures `tokens_spent`, asserts 150 and
  writes the measured value. The original evidence folder is not touched.

## 5. Mutants (scratch copies only)

| Mutant | Must be caught by |
|---|---|
| M1: OpenAI `record_usage` back after the content checks | OpenAI adapter tests |
| M2: Ollama `record_usage` back after the message checks | Ollama adapter tests |
| M3: Claude `record_usage` back after the content checks | Claude adapter tests |
| M4: delete the `except Exception` accounting in `call_model` | Caller test (150), worker test |
| M5: call `account_usage` twice inside the `except Exception` handler | Caller test (exactly 150, `calls == 1`) |
| M6: call `account_usage` twice on the success path | Success control (exactly 150, `calls == 1`) |
| M7: `await record_spend` in the cancellation branch | Cancellation test |

## 6. Out of scope

TA02 (outage plus interruption), TA03 (cancellation attribution), TA04 (typed token
refusal), TA05 (strict reservation), TA06 (missing usage looks free), TA07 (Claude cache
fields), and the monotonic accounting snapshot. Each one gets its own step.

## 7. Done when

1. Every (D) test fails on `347c82f` and passes on the fix. Every (C) test passes on both.
   All mutants are caught.
2. Backend `PYTHONHASHSEED` 0–4 passes. The isolation copy of the commit files passes.
3. Deployed scenario gate passes (the runtime changes).
4. Hosted CI is green on the pushed commit.

**Size:** one commit, about 20 changed lines in `providers.py` and `registry.py`, plus tests
and two doc lines.
