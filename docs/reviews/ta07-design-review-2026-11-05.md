# TA07 design review — 2026-11-05

**Baseline:** `dce6f66`; application unchanged during review.
The draft was committed as `6c2ea5b` during the review; its content and the application
files match the initial input hashes.
**Design:** `docs/superpowers/specs/2026-11-05-cache-input-tokens-design.md`.
**Verdict:** approve the direction, with two P2 clarifications before implementation.
Both fit the existing scope and ten-case cap. A separate implementation-plan document
or another full design round is unnecessary.

## Published-source check

| Source checked directly | Conclusion for this design |
|---|---|
| [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) | Its input, cache-read and cache-creation categories are disjoint contributions to total input. The proposed sum is correct. |
| [Anthropic Usage type](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/usage.py) | Cache fields are optional; base input and output are required. Optional cache fields must not manufacture a known base input count. |
| [OpenAI Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) | The cache fields are prompt details, not additions. Both cached and cache-write counts are optional, with default zero. |
| [Ollama chat response](https://docs.ollama.com/api/chat) | The prompt count is the total; the cached count describes tokens within it. Keeping the total unchanged is correct. |
| [OTel GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md) | Input usage includes cached tokens, with detailed usage as subsets. This supports a common inclusive total plus separate annotations. |
| [APISIX PR 14028](https://github.com/apache/apisix/pull/14028) | The published fix addresses the same native-Anthropic undercount and a converted-path double count. GitHub confirms it merged on 2026-10-15, commit `64a4afdd1353bd302f03cc8f0f780b8a78176137`. No claim about a specific released deployment is needed. |
| [LiteLLM issue 45021](https://github.com/BerriAI/litellm/issues/45021) | The issue reports the missing Ollama cache annotation. It is supporting compatibility evidence; provider documentation establishes the mapping. |

These sources support the normalization. Relay's malformed-data policy and its four
call-status labels remain local design choices, which need the precise rules below.

## TA07-D01 — P2: separate input completeness from the known subtotal

Sections 4.4 and 5.1 need to explicitly distinguish a numeric subtotal from a complete
input count, and cover an entirely missing receipt.

The current `record_usage` regards two valid integers as complete. Passing the planned
subtotal `410` and output `5` through it yields `reported`, even when a cache category
was invalid. C03 correctly requires `partial`, so the validity evidence cannot simply
remain unchanged after normalization. A small private completeness flag, or equivalent
explicit status logic, is enough; no ledger or new public output is needed.

The reverse edge matters too. A Claude response with `usage: {}` is currently unreported.
Summing absent cache categories into a synthetic prompt zero and calling
`record_usage(0, None)` makes it partial. Optional cache defaults must not create a known
input count when no actual valid input category was reported.

**Required clarification:**

- Keep known contributions, but mark the input incomplete if the base input or any
  required, present cache contribution is invalid.
- `reported` requires complete input and a valid output count, including valid zero.
- When any actual valid input contribution or output count is known, but the receipt is
  incomplete, use `partial`.
- When no actual count is usable, preserve `unreported`. Absent/null optional-cache
  defaults do not count as reported evidence and must not fabricate `prompt_tokens: 0`.

Add one C08 control for an empty Claude usage object. The existing C03 remains the
oracle for a numeric subtotal that must still be partial. Also state that missing output
does not become complete just because all input categories were parsed.
Section 4.9 should refer to unchanged run-control flow; the usage-status label itself
necessarily changes when newly examined cache metadata is malformed.

## TA07-D02 — P2: define absent optional OpenAI detail fields

Section 4.5 says to discard both annotations when a detail is invalid, but never says
what happens when just one optional detail is absent. This ordinary response must retain
its valid read count:

```json
{"prompt_tokens":100,"completion_tokens":50,"prompt_tokens_details":{"cached_tokens":60}}
```

The expected result is total input 100, output 50, cache read 60, omitted cache write,
and `reported`. An implementation that validates both `details.get(...)` results as
integers would discard the read because the write field is absent. Current C04 always
provides both fields; C05 cannot catch this, since its annotation is deliberately invalid.

**Required clarification:** missing optional read/write details default to zero and are
omitted from output. State the explicit-null policy too; treating optional null as absent
is consistent with the proposed compatibility policy. Malformed present values and a
sum exceeding the valid total still discard the annotations without changing the total
or its status. An absent/null details container yields no annotations.

Add a read-only OpenAI row beside C04. Together with C08, this uses exactly the two spare
slots: **ten collected cases**, without enlarging the test budget.

## Recommended strengthening, without another collected case

Extend C01 to a two-model workflow with a token ceiling of 1,000. The first valid response
spends 1,015; the next call must be refused before a second provider request. Assert the
first event, SQL total, accounting snapshot and typed budget refusal. The first call may
finish over the ceiling under the existing soft-limit policy.

This checks that the normalization reaches actual admission behavior, rather than only a
stored total. It also retains the existing no-double-count assertions. No change to
`token_budget.py` is needed.

## Scope and verification

The proposed change surface fits Relay: provider parsing, existing accumulation and
content-free journal fields. TA03 copies invocation usage without filtering its keys;
`tokens_spent` sums only prompt and completion; SQL reads fixed fields. Consequently the
breakdown can flow through current events and snapshots without a migration, while the
existing metric totals receive the normalized Claude input. Price multipliers, enabling
caching, dashboards and historical cleanup remain outside this fix.

Independent checks:

- 101 current tests passed across usage status, providers, token budget, attribution,
  accounting durability and observability.
- Four real Worker/Store baseline cases reproduce the Claude undercount, confirm unchanged
  OpenAI/Ollama totals, and confirm empty Claude usage is unreported.
- Two direct calls to the real TA06 helper demonstrate the completeness distinction above.
  These are compatibility probes, not an implementation of TA07.

Evidence: `evals/results/ta07-design-review-dce6f66/`. Application code and the draft were
left unchanged. Once the two clarifications are present, proceed tests-first under P2;
the scoped implementation review and stated closing gates remain sufficient.
