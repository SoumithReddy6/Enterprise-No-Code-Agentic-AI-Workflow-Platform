# Set up and review a request workflow

Choose **Workflows → New workflow**. Select a template and configure its purpose,
accepted requests, examples, exclusions and clarification rules. These are saved
router policy, not evidence that an extracted business fact is correct.

The contract-2 general-question starter has configured whole-message replies,
a request router, a narrative Agent and terminating Response branches. Broad
questions are in scope. A narrative response needs no fictitious business check
and cannot authorize an external action. Saved/imported contract-1 documents keep
their contract and behavior.

Business templates additionally expose the expected JSON schema, required JSON
Pointers, field rules and cross-checks. Purchase, refund and yes/no include a
read-only reference-record node. Select its real tenant-scoped connection and
adapt the query and reference pointers to your data. Missing connections or failed
reads stop execution. Empty/mismatched records require review instead of defaults.
These drafts configure no external writes.

Quotation establishes origin only. `source_field` checks an exact role in structured
original input; `enum` checks an allowed value; `reference_member` uses an independent
record read. A boolean in another record does not establish this record's eligibility,
so the generic yes/no draft remains manual. See [examples and explicit test fixtures](../examples/request-admission/README.md).

## Drafts and validation

Invalid JSON remains the displayed setting and can be saved as a draft. Validate
and Run refuse it with the setting named, rather than using an older value.
Backend schema/binding/connection and execution guards remain authoritative.
Select enabled models for both router and Agent. A new draft chooses the first
enabled local model, otherwise an enabled cloud model; imports preserve settings.

## Reading a run

**Request checks** distinguishes prepared replies (Redis hit/fill or local fallback)
from model routing. Checkpoint replay explains that no new lookup/model call occurred.
Router “continue” means admission, never verification or business approval.
Clarification/out-of-scope replies remain labelled model-generated.

Extraction displays actual per-field statuses/reasons, missing facts and recorded
human confirmation. Original input and checked values can be opened without rounding
embedded JSON numbers. Human confirmation retains failed/unverified checks and origin
labels; completed manual branch choices remain visibly manual. Unavailable/previewed
report data is not presented as a verified pass.

## Three distinct review kinds

| Kind | Decision |
|---|---|
| Interpretation | Compare original input, exact extracted value and checks; confirm or reject the interpretation. |
| Business decision | Read resolved values, saved policy and proposed comparison; choose true or false explicitly, or reject the run. |
| Outgoing action | Read the full outgoing request; approve or reject that write. |

The full backend `payload_json` stays visible and is the SHA-256 digest input. It
is never reserialized in JavaScript to create a confirmation digest. Decision
approval sends an explicit boolean. Rejection sends no branch choice; it is not
equivalent to choosing false.

Expired/unknown/malformed reviews disable continuation. Expiry updates while open;
the server enforces its own clock, digest, tenant scope, rate limits and idempotence.
Digest/expiry refusals appear beside the review. Refresh and reread before deciding.

Checks, reviews and answers share the scrollable Response area so long reasons do
not hide the answer. The execution log carries the admission/replay explanations.
These screens demonstrate enforcement, not LLM semantic accuracy. Live admission,
clarification and unnecessary-review rates are measured in Step 8.
