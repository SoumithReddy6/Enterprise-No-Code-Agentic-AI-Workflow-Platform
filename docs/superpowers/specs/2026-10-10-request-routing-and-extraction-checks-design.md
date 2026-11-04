# Request routing and extraction checks — draft design

**Status:** Draft for review; no implementation is authorised by this document alone.
**Baseline reviewed:** `dad766ed3302441d2ba2503b7457672cd3cba131`.
**Scope:** The default design for new workflows. No separate conversion project for existing workflows.
**Implementation draft:** [Task plan](../plans/2026-10-10-request-routing-and-extraction-checks.md).

## 1. Outcome

Prevent greetings, unrelated questions, incomplete requests and unsupported extracted
values from becoming automatic business decisions or external writes. Keep genuine requests
usable, preserve the original message, and explain any clarification or review to the user.

The user agreed to this order:

```text
Input
  → Greeting cache
      reply → Response, stop
      continue → LLM request router
          reply → Response, stop
          continue → Extraction
              → Extraction check
                  clarify → Response, stop
                  uncertain → Human review, pause
                  verified / specifically confirmed by a person
                      → Calculate / business conditions → Actions → Response
```

The router identifies the kind of request. It does not approve a purchase, decide refund
eligibility, verify product identity, or make an extracted value trustworthy.

## 2. Relationship to the existing provenance plan

Keep the labels and Calculate node already built. First repair R01–R12 from the
[independent audit](../../reviews/decision-provenance-phases-1-3-audit-2026-10-09.md).
The new entry checks complement provenance rather than replace it.

This draft expands the unimplemented portions of the
[decision-provenance plan](../plans/2026-10-08-decision-provenance.md): shared extraction
verification, required facts, decision review and measurement. It adds an LLM call at
workflow entry, not an additional judge model after every Agent. An extraction check is
deterministic; facts whose meaning cannot be established remain unverified.

Use the planned `Workflow.contract: Literal[1, 2]`, defaulting to 1 for documents that
omit it. The editor creates new workflows with contract 2. Fixes to faulty provenance
and arithmetic are not restricted to contract 2. Do not quietly activate the new entry
flow on a saved workflow that lacks it.

For contract 2, there is no `accept_guessed` bypass for a consequential decision. Any
future expert bypass requires a separate explicit product decision and must not be
introduced by this work. This is deliberately stricter than the earlier draft's override.

## 3. Greeting cache

Add a visible `greeting_cache` node immediately after the trigger. It has `message`
input and string outputs `branch`, `reply`, and `receipt`; handles are `continue` and
`reply`. The original message remains bound from the trigger throughout the graph.

The workflow author configures a bounded list of exact messages and prepared replies:
at most 100 entries, message at most 256 characters, reply at most 2,000 characters.
Normalise Unicode to NFC, case-fold, trim ends and collapse whitespace for lookup only.
Do not strip punctuation, remove words, or change the message sent downstream. Aliases
such as `hello` and `hello!` are explicit entries. Reject conflicting aliases that
normalise to the same key. Do not include `yes`, `no`, or approval commands in a preset.

`Hello!` can return a prepared greeting. `Hello, buy 3 laptops` cannot match the standalone
greeting and must reach the router. No substring, embedding or semantic-cache matching.
Only configured whole-message entries are cache candidates; arbitrary requests are not
stored in Redis. A cache entry cannot contain a `proceed` decision or an approval.

Use Redis for the prepared-reply cache with tenant, workflow-content digest and reply-set
version in the namespace. Key messages by a digest, not raw message text. Cache lifetime
is 3,600 seconds. The authoritative reply is the versioned workflow configuration; a
missing, malformed, stale or altered Redis entry uses that configured reply and is
repopulated. Redis failure does not turn a greeting into a purchase or stop the service.

Use async Redis, an overall 200 ms budget for one lookup/fill sequence, no automatic
client retries, at most 4,096 bytes per cached entry, and explicit connection cleanup.
A configured reply whose encoded entry exceeds that budget uses the exact local reply
instead of being cached or shortened.
Redis is an optional readiness dependency. `COMMON_REPLY_REDIS_URL` is server-side
configuration, never exported in a workflow or written to logs. Local Compose exposes it
only on loopback. Pin and audit the client and server versions when adding the dependency.

Prepared replies are authored text, not source evidence for extraction. The reply edge
must terminate at a Response. Cache hits and local fallback greetings use no model calls.

## 4. LLM request router

Add a visible `request_router` node. Preserve the existing Agent `role: router`, whose
current purpose is specialist delegation; do not silently redefine it.

The new node has `message` input, string outputs `branch`, `reply`, and `decision`, and
`continue`/`reply` handles. `decision` is JSON text with exactly:

```json
{"route":"proceed","reason":"Purchase request with stated details","missing_fields":[],"reply":""}
```

Allowed routes: `proceed`, `clarify`, `out_of_scope`. Reason is nonempty and at most 300
characters; missing fields are at most 16 unique names of at most 64 characters; reply is
at most 2,000 characters. Proceed requires empty missing fields and empty reply. Other
routes require a nonempty reply. Reject extra keys. No self-reported confidence threshold.

Router settings contain purpose (1–2,000 characters), accepted request descriptions,
exclusions, clarification rules and examples. Each list has at most 20 entries of at
most 500 characters. Purpose must be configured before a contract-2 workflow can run.
Missing deterministic field requirements are also configured in extraction-check rules;
the router's prose is not their sole enforcement.

Use the chosen authorised LLM, the original message and the saved workflow rules.
Keep rules separate from untrusted message data. This node cannot use tools, delegate,
write externally, or invent replacements for the original message. Initial response plus
at most one structured repair; a second invalid response fails the node with no branch.
Provider failure, token exhaustion and cancellation never default to proceed.

Reuse provider authentication, token limits, usage persistence and execution identity.
Both the initial call and repair count towards tokens. No separate router action budget,
token ledger or provider implementation. A checkpoint stores the validated decision and
policy/input digests; replay within the same run does not call the router again. Do not
cache LLM routing verdicts in Redis in this version.

Its result remains model-derived. The `continue` edge grants access to read-only extraction
and checking, not authority to branch on guessed business facts or perform a write.

Expected purchase routing, to be measured rather than assumed:

| Input | Expected route |
|---|---|
| `3 laptops at $450 each` | proceed; calculation gives 1,350; business policy may require review |
| `Buy some laptops` | clarify quantity and price |
| `Order $450` | clarify item |
| `How do AI workflows work?` | out_of_scope |
| `Hello, buy 3 laptops at $450 each` | proceed, not a cached greeting |

Other workflows supply their own brief. A broadly scoped Q&A workflow can accept general
questions; purchase-specific criteria never become a platform-wide blacklist.

## 5. Extraction checks

Add `verify_extraction`: required inputs `original` and `value`, optional `reference`;
public string outputs `text`, `branch`, `reply`, `report`. The extraction's original JSON
text is preserved in `text`; `report` contains bounded JSON describing the checks. Graph
handles are `continue`/`reply`. Uncertainty pauses before returning a continue branch.

The author specifies an expected JSON schema, required fields, per-field acceptance
rules, and optional cross-check expressions. Use exact JSON parsing and the repaired
Calculate evaluator. Validate declared references with the same upstream/output-availability
checks as ordinary bindings. Optional reference data is required when a rule uses it.

Check three independent things:

1. **Shape/completeness:** expected types, required facts and explicit nulls. Reject invented
   defaults; distinguish explicit zero from absent. Booleans are not numbers.
2. **Origin:** each relevant value is supported by that extraction's actual eligible input.
   Prompt examples, model reasons, tool echoes and unrelated model output are excluded.
   Correct JSON paths and exact numeric values must refer to the same value being used.
3. **Suitability:** configured enum/structured-source equality, independently authoritative
   reference membership, and deterministic cross-field checks. Substring quotation alone
   cannot prove a product, eligibility or the role of a number.

Supported initial rules are `quoted`, `source_field`, `enum`, `reference_member`, and
Calculate `check`. `quoted` reports origin only; a required business fact's suitability
remains unknown unless another configured check establishes it. `source_field` compares
an exact JSON Pointer against structured original input. `reference_member` uses configured
list and record-key JSON Pointers into independently sourced reference JSON. An Agent's
output or request echo is not an authoritative reference. Arbitrary author regexes and
another LLM acting as the final verifier are excluded.

Verdicts:

| Verdict | Behaviour |
|---|---|
| passed | Continue with value-bound verification receipt |
| clarify | Explain missing/malformed information, terminate this run; next submission is a new run |
| needs_review | Persist the exact review payload, checkpoint, release lease and await a person |

Known unsupported/inconsistent values cannot become an automatic pass. A person can
confirm the displayed interpretation; rejection or expiry terminates without sending.
Do not edit values through the approval endpoint in this version.

Free natural-language meaning is not universally provable. In the purchase example,
`item: "order"` must not pass merely because it occurs in the request. A catalogue can
establish product membership; quantity/price role checks still need structured facts or
appropriate deterministic cross-checks. Where those are insufficient, review is expected
and measured. A yes/no classification inferred from prose likewise remains a model
judgment unless checked against suitable records or specifically confirmed by a person.

The verification receipt is reserved metadata `_verification`, version 1, bound to the
run, verification node, input/value/policy digests, exact checked field pointers, and any
human review. At most 64 field results and 8,192 serialized bytes. Excess coverage becomes
unverified and prevents automatic continuation, never a pass. Preserve origin labels:
human confirmation is separate authority, not a rewrite of guessed to source.

## 6. Execution boundary and durability

Implement verification and eligibility once, shared by graph nodes, loop bodies and
specialist/attached execution. A later Agent changing a checked value invalidates the
receipt; identical copying cannot increase trust. Deterministic derivations carry a
receipt only for the exact dependencies actually used, including coalesce short circuits.

Contract-2 validation requires the entry flow after the trigger and verification before
model-derived facts feed a business Condition, Calculate check or external write. Reject
edges, error recovery or attachments that bypass it. Runtime rechecks scope/digests at
those boundaries; a compiler-only check is insufficient. Router/cache control branches
are limited entry routing, not business approvals.

Reuse the existing durable run/checkpoint/approval system and run event rows. Never use
Redis as approval, lease, idempotency or write-result storage. Extend approval kind to
`write`, `decision`, `verification`, retaining old records as write. Decision reviews
choose true/false as in the earlier plan; verification reviews accept/reject only the
exact displayed interpretation. Human decisions remain visibly manual in events/metrics.

Use an Alembic revision for the approval-kind column and backfill; never `create_all`.
Preserve fencing, digest checking, expiry, rate limiting, cancellation, action results
and UncertainWriteError. Approve/reject replay is idempotent. Review grants are bound to
the saved run's policy/input/value; a new run gets no grant from an earlier run.

Checkpoints retain typed receipts, original labels and review decisions. Malformed,
mismatched or insufficient receipts never permit a write. Legacy missing metadata cannot
silently qualify for contract-2 automatic decisions. Cached replay remains distinguishable
from a Redis greeting-cache hit.

## 7. Editor and operations

New-workflow setup asks for purpose, valid request examples, required facts and field
checks. The palette provides the two entry nodes and extraction check. Show the control
branches and the checks before the user runs it; invalid drafts remain saveable.

The run view explains cached reply, router clarification/rejection, missing facts,
verification failure, or pending review. Show input alongside extracted fields and checks
in review. Never present a router proceed as 'verified' or 'approved'. Model-produced
clarification replies are labelled as such; cached replies are labelled prepared replies.

Journal only event/route/reason codes, identifiers, cache outcome, durations and usage;
never message, extracted content, tokens, Redis URLs or credentials. Operator metrics show
cache outcomes, router routes/errors, verification verdicts and manual confirmations.
Keep the existing deployment-wide scope explicit.

## 8. Evidence and release rule

Write a fixed labelled suite before product changes: at least 60 cases across purchases,
tickets/support, refunds and yes/no eligibility, with at least 20 expected proceed,
20 clarify, 10 out_of_scope and 10 prepared-reply cases. Cover misleading/adversarial
inputs as well as ordinary messages. Include the actual screenshot input and observed
`item: "order"` case. At least 20 cases are held out from prompt examples and tuning.
Record the old workflow's baseline first; never change expected labels or gates after
seeing the new run.

Run deterministic wrong-router/wrong-extraction injection separately from real-model
measurements. The former proves boundaries; the latter measures normal behaviour.
Proposed release targets, to be reviewed now and frozen before measurement: zero unsafe
automatic decisions/writes; all configured whole-message cases handled without an LLM;
at least 0.90 of expected-proceed cases admitted (18 of the initial 20); all audit regressions
and process-level resume/approval/fencing tests passing. These are finite-suite criteria,
not a claim of universal accuracy. Report false router acceptance, valid-request rejection,
unnecessary clarification/review, latency and tokens. Do not make perfect router
classification a prerequisite for safety, or accept an always-reject router as success.

Run existing backend/frontend/browser/production/grounding checks unchanged. Use isolated
accounts, databases, Redis namespaces and receiver services. Pin code, model digest,
policy, cases and source hashes in the artifact. Preserve earlier evidence. Check hosted
CI against the final pushed commit separately; local results are not a hosted pass.

## 9. Technical references

Routing classifies inputs into specialised follow-up tasks:
[Anthropic routing workflow](https://www.anthropic.com/engineering/building-effective-agents).
Redis supports expiring exact string values through
[SET expiration options](https://redis.io/docs/latest/commands/set/).
Async Redis connections need explicit cleanup:
[redis-py async documentation](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).
These capabilities support the design; they do not guarantee request validity.
