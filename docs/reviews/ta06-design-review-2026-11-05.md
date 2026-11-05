# TA06 draft review

Date: 2026-11-05, America/New_York.
Baseline: `bcc8d9b1e1e421a144b2738ce0630a21150c9061`.
Draft: `docs/superpowers/specs/2026-11-05-unreported-usage-design.md`.
Evidence: `evals/results/ta06-design-review-bcc8d9b/`.

## Verdict

Approve the aggregate-counter approach after the two P2 boundary corrections below.
Record the P3 adapter compatibility constraint before implementation. These edits fit
the P2 workflow and need a short confirmation of the edits rather than another full
design review.

The four statuses, valid-zero handling, invocation attribution, additive SQL columns and
incomplete-total display address the original finding. Keeping logical calls defined
at `call_model` also makes the retry scope explicit. The per-physical-request ledger can
remain deferred under that definition.

## TA06-D01 — P2 — Define an observable dispatch boundary

Location: design §4.2 and §5.1, especially lines 70–72 and 98–99.

The proposed marker is set immediately before `http.post`. Request preparation still
happens inside that method. A malformed URL can raise before the transport sees a
request. A connect/pool timeout can also happen before anything reaches the provider.
The marker cannot establish the draft's claim that a request was sent.

The diagnostic instrumented the draft's exact placement. An Ollama URL with a nonnumeric
port produced `InvalidURL`, zero transport invocations and `_request_sent=True`. Applying
the draft's classification gives `unanswered`, although its no-status rule requires a
pre-send failure to contribute nothing.

**Recommended correction.** Define and name the observable boundary as a client request
attempt after request preparation. Define `unanswered_calls` relative to that boundary.
Application validation and request preparation failures contribute no call status.
Connection/pool failures after dispatch can be classified as unanswered attempts under
that documented client-side meaning. The design must then use that meaning consistently
instead of claiming confirmed delivery to the provider.

If confirmed network sending is a required product distinction, specify the transport
evidence and tests for it before promising that distinction. A flag before a high-level
method provides insufficient evidence. The request hook documented by HTTPX is also
explicitly before the network send, so it supplies a preparation/dispatch boundary.
[HTTPX exceptions](https://www.python-httpx.org/exceptions/),
[HTTPX event hooks](https://www.python-httpx.org/advanced/event-hooks/).

Add a request-preparation failure to the pre-dispatch test and explicitly classify
connection/pool failures. Preserve the current retry and exception behavior while making
the measurement boundary precise.

## TA06-D02 — P2 — Record response receipt independently of usage parsing

Location: design §4.2 and §5.1, especially lines 99–102.

`record_usage` is reached after JSON-object parsing and, for Claude/OpenAI, after usage
container validation. Both adapters raise `ProviderResponseError` for a list/string
`usage` value before reaching it. A valid JSON response has therefore been read even
though the proposed response-read marker remains unset.

For both Claude and OpenAI, the diagnostic returned a valid JSON response with a valid
answer and `usage: []`. The body was fully received and parsed into an object. The adapter
then refused the usage shape, and `record_usage` was never called. The literal draft
markers classified the operation as `unanswered`. Under §4.2 it is a received response
with unavailable counts, which belongs to `unreported`.

**Recommended correction.** Capture completed response receipt as soon as the non-streaming
`http.post` returns, before HTTP status handling, JSON decoding or usage validation.
Keep token-field validity as separate evidence. A complete response with unusable usage
can then be counted as unreported while preserving the existing response error. A timeout
while receiving the body still has no complete response and can remain unanswered.

Explicitly classify HTTP error responses and invalid JSON too. The instrumented 401 and
invalid-JSON cases both had a complete body but never called `record_usage`. Add these
assertions to the existing rejected/malformed-response tests within the agreed budget.

HTTPX documents that response hooks precede the decision to read the body. A hook used
for this purpose needs `aread()`, or the marker can follow the completed non-streaming
request as proposed above.
[HTTPX event hooks](https://www.python-httpx.org/advanced/event-hooks/).

## TA06-D03 — P3 — Preserve the adapter usage-dictionary contract

Location: design lines 104–106 and 178–183, plus `backend/tests/test_providers.py`.

The draft places internal marker keys inside the caller-owned usage dictionary. Removing
them in `call_model` protects the managed execution path, but direct transport callers
also use that dictionary. Existing normal-response and rejected-response tests require
exact token-only dictionaries, and malformed-usage tests require `{}`.

The literal-marker diagnostic's normal OpenAI response returned the correct 10/5 tokens
plus `_request_sent` and `_response_read` in the adapter's dictionary. This conflicts with
the existing direct-call expectations even for fully reported responses. The draft's
existing-test allowance currently covers only answer-returning stubs with absent usage.

**Implementation constraint.** Keep transport evidence private to the managed call while
preserving token-only dictionaries for existing plain-dictionary callers. A private
capture object with separate state is one possible implementation. If the public mapping
is intentionally changed instead, list those contract changes and extend the test-change
allowance explicitly before the builder encounters the stop rule.

## Evidence and source checks

- The original three-response missing-usage reproduction still succeeds with a one-token
  limit and reports zero tokens and zero calls.
- The literal-marker instrument runs seven cases through the current adapters. It
  monkeypatches marker placement only inside its process. It is a draft counterexample,
  rather than a candidate TA06 implementation.
- 80 current provider, token-budget, TA03 and TA02 tests pass. No implementation success
  is inferred from those baseline controls.
- Application files, the draft and the original auditor evidence retain their recorded
  hashes. The review writes only its own report and evidence.

OpenAI's official non-streaming Chat Completions schema declares optional usage. Anthropic's
official SDK requires its two base counts. These support the draft's distinction between
an optional measurement and a malformed provider contract. The status counters remain
Relay's chosen response to those conditions.
[OpenAI Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create),
[Anthropic Usage type](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/types/usage.py).

Langfuse distinguishes ingested and inferred usage, with ingested data taking priority.
That supports keeping provenance explicit. The draft's decision to display incomplete
reported subtotals is an appropriate product choice.
[Langfuse usage and cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking).

The Ollama issues describe historical omissions. PR 5371 is titled "Return Correct Prompt
Eval Count Regardless of Cache Prompt" and closes issue 2068. Use these as historical
compatibility evidence. The local reproduction uses synthetic responses and does not
establish that the current local Ollama version exhibits that old cache defect.
[Ollama issue 2068](https://github.com/ollama/ollama/issues/2068),
[Ollama PR 5371](https://github.com/ollama/ollama/pull/5371).

The cited LiteLLM issue 30420 could not be fetched or corroborated by the review's search.
No finding or recommendation here depends on that citation. The official provider and
HTTPX contracts establish the required corrections.

## Closing expectation

Keep the draft's bounded tests, status counters, new-run scope and P2 implementation
review. Incorporate request preparation, malformed usage, HTTP error and invalid JSON
classification into the existing case matrix. Verify the two boundary edits and the
chosen dictionary-compatibility handling before starting the failing implementation
tests.
