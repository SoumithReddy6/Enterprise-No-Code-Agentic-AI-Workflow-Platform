# TA07: count Claude cache input in the prompt total, and keep the cache breakdown

**Status:** draft for review.
**Date:** 2026-11-05.
**Baseline:** `dce6f66` (TA06 closed; branch content equals `main` at `0f3bb05` plus the
TA06 re-check report).
**Scope decision:** option A, chosen by the user on 2026-11-05. Fix the Claude prompt
total and keep the cache breakdown on usage totals, events and the journal. No schema
migration and no metrics column (option B, a cache hit ratio, is not in scope). New-run
behavior only: no backfill.
**Tier:** P2. Failing tests, then the implementation, then one implementation review.

## 1. Problem and goal

Finding TA07 (`docs/reviews/token-accounting-production-review-2026-10-25.md`): a valid
Messages response with input 10, cache creation 400, cache read 600 and output 5 was
recorded as 15 tokens instead of 1,015. `claude_chat` passes only `input_tokens` to
`record_usage` (`backend/app/providers.py`).

Relay does not send `cache_control` today, so live Claude calls have no cache tokens.
This is a latent adapter gap. It becomes an undercount, in metrics and in the token
ceiling, as soon as caching is enabled or a compatible endpoint reports cache tokens.

The OpenAI adapter is already correct: `prompt_tokens` includes cached input. Its
`cached_tokens` breakdown is discarded, although OpenAI caches automatically, so live
calls can carry it.

**Goal:** `prompt_tokens` means all input processed for the call, on every provider. The
cache categories are kept as a breakdown that never adds to the total. A cache category
that is present but invalid makes the input count incomplete (TA06 `partial`), not zero.

## 2. Published sources and adaptation

Source check: 2026-11-05.

| Source | Documented behavior | Adaptation to Relay |
|---|---|---|
| [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), FAQ | `total_input_tokens = cache_read_input_tokens + cache_creation_input_tokens + input_tokens`; `input_tokens` is only the input after the last cache breakpoint. | Claude `prompt_tokens` = the sum of the three. |
| [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) | Caching is opt-in through `cache_control` (including automatic caching). Reads and writes have different prices. | Confirms the gap is latent in Relay. Tokens are not money (unchanged docs), so price multipliers stay out of scope. |
| [anthropic-python `Usage`](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/usage.py), [`CacheCreation`](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/cache_creation.py) | `cache_creation_input_tokens` and `cache_read_input_tokens` are `Optional[int]`; `cache_creation` splits writes by TTL. | Absent or `null` means 0. The TTL split is not used. |
| [openai-python `CompletionUsage`](https://github.com/openai/openai-python/blob/main/src/openai/types/completion_usage.py) | `prompt_tokens_details.cached_tokens` and `cache_write_tokens` are details of `prompt_tokens`; `reasoning_tokens` is a detail of `completion_tokens`. | The OpenAI total is unchanged. Read and write become the breakdown; reasoning stays out of scope. |
| [Ollama chat API](https://docs.ollama.com/api/chat), [LiteLLM #45021](https://github.com/berriai/litellm/issues/45021) | Docs: `prompt_eval_count` is the "number of tokens in the prompt", and `prompt_eval_cached_count` the "number of prompt tokens read from the cache". The issue shows 6,016 cached of 6,024. | The cached count is a part of the total, not an addition. The Ollama total is unchanged; the cached count becomes `cache_read_tokens`, checked to be at most the total. |
| [Apache APISIX #14028](https://github.com/apache/apisix/issues/14028) | Native Anthropic `prompt_tokens` was `input_tokens` only; the fix is `input + cache_read + cache_creation`. A converter that subtracted only cache reads counted cache writes twice. | The same defect and the same normalization. Tests prove that the categories add up exactly once. |
| [LiteLLM prompt caching](https://docs.litellm.ai/docs/completion/prompt_caching) | `prompt_tokens` includes cache hits and misses; cache counts go in `prompt_tokens_details`. | Same total meaning, breakdown kept apart. |
| [OTel GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md) | `gen_ai.usage.input_tokens` SHOULD include cached tokens; `cache_read.input_tokens` and `cache_write.input_tokens` are recorded when available and non-zero. | Same total meaning; breakdown omitted while zero, as TA06 does for its counters. OTel is not a dependency. |

## 3. Approaches

| Approach | Trade-off | Decision |
|---|---|---|
| A: normalize the Claude total; breakdown on totals, events and journal | Fixes the count and the ceiling input; no schema change | Selected |
| B: A plus metrics columns and a cache hit ratio | Dashboard value; needs migration 0007 and API work | Deferred (user decision) |
| Add the breakdown to every provider's total | Double-counts OpenAI and Ollama (APISIX bug 3) | Rejected |
| Start sending `cache_control` | A product and cost decision, not an accounting fix | Out of scope |

## 4. Required rules

1. **One meaning of `prompt_tokens`:** all input processed for the call, cached or not.
   - Claude: `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.
   - OpenAI: `prompt_tokens` (unchanged).
   - Ollama: `prompt_eval_count` (unchanged).
2. **Breakdown fields:** `cache_read_tokens` and `cache_write_tokens`, parts of
   `prompt_tokens`. They are omitted while zero, and they never add to `prompt_tokens`,
   `tokens_spent`, the ceiling or SQL rows.
   - Claude: read = `cache_read_input_tokens`, write = `cache_creation_input_tokens`.
   - OpenAI: read = `prompt_tokens_details.cached_tokens`, write =
     `prompt_tokens_details.cache_write_tokens`.
   - Ollama: read = `prompt_eval_cached_count`; no write.
3. **A valid count** is an `int` that is not a `bool` and is `>= 0` (unchanged rule).
4. **Claude validity** (cache categories are part of the total):
   - Absent or `null` cache field: 0, and the call can still be `reported`.
   - A cache field that is present and invalid: the input count is incomplete. Record the
     known subtotal of the valid input categories as `prompt_tokens`, and the call status
     is `partial` (TA06). The invalid category gets no breakdown value.
   - Invalid or missing `input_tokens`: as TA06. With valid cache fields, their subtotal is
     the known prompt subtotal, and the status is `partial`.
5. **OpenAI and Ollama validity** (the breakdown is a detail of a total):
   - The total decides the status, as in TA06. The breakdown never changes the status.
   - Drop the breakdown (both values), not the total, if `prompt_tokens_details` is not an
     object, a detail is invalid, or read + write is greater than the prompt total.
   - A breakdown without a valid prompt total is dropped.
6. **Accounting:** `account_usage` adds the breakdown to the same totals as the counts:
   the checkpoint owner's `usage`, `usage_by_invocation` and each `by_model` row. TA03
   attribution is unchanged. `tokens_spent` is unchanged: it sums only `prompt_tokens`
   and `completion_tokens`, so the corrected Claude total counts toward the ceiling once.
7. **Journal:** `model.usage` carries `cache_read_tokens` and `cache_write_tokens` when
   non-zero. The filter accepts them only as valid integer counts.
8. **TA06 hardening (recheck note):** the journal keeps `usage_status` only when it is a
   string and one of the four statuses. `None`, a list or a dict is dropped and never
   raises.
9. **No control-flow change.** No status, refusal, retry or business decision changes,
   except that the Claude total and the ceiling now see cache input.

## 5. Proposed implementation behavior

### 5.1 Providers (`providers.py`)

`record_usage` gains optional breakdown arguments (names are the builder's choice). It
keeps its TA06 behavior for the two counts and the `CallUsage` evidence, and adds the
breakdown keys to the usage dict only when they are valid and non-zero. The Claude
transport passes its three input categories and lets `record_usage` (or a small helper)
form the total and the validity under rule 4. The OpenAI and Ollama transports pass their
total unchanged and their breakdown under rule 5.

**Usage-dict contract (TA06-D03):** for a direct caller, the dict stays unchanged when the
response has no cache tokens. When it has cache tokens, the dict also holds
`cache_read_tokens` / `cache_write_tokens`. These are token counts, not evidence markers;
this additive change is listed here on purpose. No existing test has cache fields.

### 5.2 Accounting (`registry.py`)

`account_usage` adds the breakdown keys when present, with the same omit-while-zero rule
as the TA06 counters, and passes them to the journal.

### 5.3 Journal (`observability.py`)

Add the two breakdown names to `FIELDS`, accepted only as valid integer counts. Make the
`usage_status` check type-safe (rule 8).

### 5.4 Not changed

`operator_metrics.py` (`usage_rows` reads only its fixed fields, so the breakdown does not
reach SQL), migrations, the frontend, `token_budget.py`, `execution_policy.py`, the
compiler, agent and loop runtimes, and the Worker.

## 6. Verification and stop rule

Budget: at most **10 new collected cases** and **8 mutants**. HTTP is mocked with
`httpx.MockTransport` through `providers.client`, and the real Worker and Store are used
where a case checks events or metrics, as in TA06.

| ID | Case (rows) | Count | Checks |
|---|---|---|---|
| C01 | The finding's Claude response: input 10, creation 400, read 600, output 5 (Worker) | 1 | Event and SQL: 1,010 + 5, 1 call, `reported`; event breakdown read 600, write 400; `tokens_spent` 1,015 (breakdown not added again); the real journal JSON has both breakdown fields. |
| C02 | Claude with no cache tokens: `absent`, `null` | 2 | 10 + 5, no breakdown keys, `reported` (controls). |
| C03 | Claude `cache_read_input_tokens: "600"` (invalid), creation 400 | 1 | Prompt subtotal 410, `partial`, write 400 and no read in the breakdown. |
| C04 | OpenAI prompt 100 with `cached_tokens` 60 and `cache_write_tokens` 20, completion 50 | 1 | Prompt stays 100 (no double count), breakdown 60 and 20, `tokens_spent` 150. |
| C05 | OpenAI `cached_tokens` 150 with prompt 100 | 1 | Breakdown dropped; total 100 + 50, `reported`. |
| C06 | Ollama `prompt_eval_count` 6,024, `prompt_eval_cached_count` 6,016 | 1 | Prompt 6,024, breakdown read 6,016, `reported`. |
| C07 | Journal status hardening: `None`, a list, a dict | 1 | No exception; `usage_status` dropped for each. |

That is 8 cases, inside the cap. C01 and C03 fail on the baseline by count mismatch. C04
and C06 fail by the missing breakdown values (read as absent, not as an interface error).
C07 fails on the baseline because a list raises `TypeError`. C02 and C05 are controls.

Mutants must cover: the Claude total is `input_tokens` only; the breakdown is added to an
OpenAI or Ollama total; an absent Claude cache field is treated as invalid; an invalid
Claude cache field is treated as 0 and `reported`; the subset check is removed;
`tokens_spent` sums the breakdown; the journal hardening is reverted.

**Existing-test churn.** No existing test is expected to change. Stop rule: if more than
5 existing tests change, or any changes for another reason, stop and report.

Gates: focused tests, full backend seeds 0–4 in a clean-copy isolation, the deployed
scenario gate, and the five hosted CI jobs. TA06's `test_usage_status.py` and TA03's
`test_usage_attribution.py` stay green.

TA07 closes when the bounded tests and mutants pass, the finding's response reports
1,010 + 5 with its breakdown, OpenAI and Ollama totals are unchanged, and hosted CI is
green.

## 7. Limits

- Relay still does not request caching. Enabling `cache_control` is a separate decision.
- Tokens are not money: a cache read counts as one token in the ceiling, though it costs
  less. Pricing and cache hit ratio metrics stay out of scope (option B).
- The Claude TTL split (`ephemeral_5m` / `ephemeral_1h`) and OpenAI reasoning, audio and
  image details are not recorded.
- Streaming, where Claude cache counts can arrive in `message_delta`, is not supported.
- Old runs keep their old counts; no backfill.
- Strict reservation (TA05) and concurrent settlement (TA08) are unchanged.

## 8. Expected change surface

`providers.py` (normalization and breakdown), `registry.py` (accumulate and journal),
`observability.py` (two fields and the status hardening), a focused test module, and
`docs/operations.md` (the meaning of `prompt_tokens` and the breakdown). No migration,
frontend, metrics, token-budget, runtime or Worker change.
