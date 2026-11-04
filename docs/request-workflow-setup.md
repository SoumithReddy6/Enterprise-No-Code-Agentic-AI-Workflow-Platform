# Set up and review a request workflow

Choose **Workflows → New workflow**. Select a template and configure its purpose,
accepted and excluded requests, examples and prepared replies. These settings, with
the fields below, are the workflow's request contract. They guide the model; they are
not evidence that an extracted business fact is correct.

Every template starts with one **Request reader**:

1. A message that matches a prepared reply gets that reply. No model is called.
2. Otherwise one model call reads the message. It returns a type (`request`,
   `unclear` or `unrelated`), the values it finds, and an optional reply.
3. Code checks every value with the field rules, then chooses the branch:

| Check result | Model type | Branch |
|---|---|---|
| Passed | request or unclear | continue, with a verification receipt |
| Passed | unrelated | a person confirms (interpretation review) |
| Required facts missing, some values | any | reply: code names the missing facts |
| No values | unrelated | reply: the model's reply |
| No values | request or unclear | reply: code asks for the required facts |
| Unconfirmed values or a failed check | any | a person confirms |

The model's type never makes a request continue on its own. Saved workflows that use
the earlier cache → router → extraction check chain still load and run; those nodes
are no longer in the palette. Saved/imported contract-1 documents keep their contract
and behavior.

The fields are a JSON schema of the values to read. **Required fields** must all be
present. **Required, one group of** lists alternatives: a purchase needs `total`, or
`quantity` and `unit_price`. Every field needs a rule, and the reader needs at least one
required field or group. The inspector suggests fields that later Condition and
Calculate nodes read; nothing is added until you choose **Add suggested fields**.

The general-question starter reads one `question` field and continues to a narrative
Agent. A narrative response needs no fictitious business check and cannot authorize an
external action. Purchase, refund and yes/no include a read-only reference-record node
before the reader. Select its real tenant-scoped connection and
adapt the query and reference pointers to your data. Missing connections or failed
reads stop execution. Empty/mismatched records require review instead of defaults.
These drafts configure no external writes.

`quoted` establishes origin only and never passes a field alone. `accept_quoted`
passes a value that is written in the request: it proves where the value is written,
not its role, so add a cross-check for numbers (`total == quantity * unit_price`).
`source_field` checks an exact role in structured original input; `enum` checks an allowed value; `reference_member` uses an independent
record read. A boolean in another record does not establish this record's eligibility,
so the generic yes/no draft remains manual. See [examples and explicit test fixtures](../examples/request-admission/README.md).

## Drafts and validation

Invalid JSON remains the displayed setting and can be saved as a draft. Validate
and Run refuse it with the setting named, rather than using an older value.
Backend schema/binding/connection and execution guards remain authoritative.
Select enabled models for the reader (and the Agent, where present). A new draft chooses the first
enabled local model, otherwise an enabled cloud model; imports preserve settings.

## Reading a run

**Request checks** distinguishes prepared replies (configuration, no model call) from
a model reading. It shows the model's type and says that code checked the values and
chose the branch. Checkpoint replay explains that no new model call occurred. Older
runs with a request router still show the router's decision as model routing.

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
clarification and unnecessary-review rates are measured on a frozen case suite.
