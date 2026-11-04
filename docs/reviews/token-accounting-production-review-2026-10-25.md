# Token usage and accounting: production comparison

Reviewed on 2026-10-25, against application code at
`eb4417a6cc83e50b6562cee8869a06274de5a00e`.

The checkout also contains a user-edited A5.3 design; that document was not changed
by this review. This review changes no application code, dependencies or schema.

## Assessment

Relay has a useful foundation: provider-reported input/output counts, run-wide
accounting, attribution to invocations, persisted resume state, and explicit
separation between token counts and monetary cost. The normal nested-agent
success control reconciles correctly.

It is not yet a billing-grade usage ledger or a strict admission controller.
Four current correctness defects reproduced. Two additional behaviors are policy
limitations already partly documented, one adapter gap is conditional on cache
usage, and the prior stale-snapshot finding is latent until concurrent writes.
These categories should not be counted as eight newly introduced production bugs.

## Official published behavior compared

These are public product contracts and documented operating patterns, not claims
about unobservable company internals or a universal production standard.

| Source | Relevant published behavior | Consequence for Relay |
| --- | --- | --- |
| [Amazon Bedrock token quotas](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) | Reserves input plus the output allowance at request start and adjusts quota consumption later. Quota accounting is distinguished from billing. | A strict admission policy needs a pre-call reservation, not just a check of earlier reported spend. Its model/cache-specific quota formula must not be copied as a billing formula. |
| [Microsoft API Management token limits](https://learn.microsoft.com/en-us/azure/api-management/llm-token-limit-policy) | Documents response-based enforcement, optional prompt estimation, and possible overshoot with concurrent calls. | A soft usage guard is legitimate when the guarantee and overshoot are explicit. Calling Relay's current threshold an exact hard ceiling is misleading. |
| [OpenAI token counting](https://developers.openai.com/api/docs/guides/token-counting) and [Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) | Offers input counting; generation caps include non-visible generated tokens. Usage totals have category breakdowns. | Keep total counters separate from their component breakdowns; adding reasoning/cache components again can double-count. |
| [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) | Total processed input includes uncached input, cache creation, and cache reads. | A generic two-field adapter must normalize these additional input categories when present. |
| [Anthropic token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting) | Describes a preflight count endpoint. One fetched official rendering explicitly qualifies counting as an estimate, while the canonical page currently omits that note. | Do not promise a mathematically strict cross-provider limit based on an assumed universally exact estimator; specify model/endpoint guarantees and margin/bound policy. |
| [Ollama chat](https://docs.ollama.com/api/chat) | Defines prompt and generated-token counters, with a separate cached-prompt breakdown. | Current base-counter mapping is appropriate; cache breakdown can be retained without adding it twice. |

## Current correctness defects

### TA01 [P1] — Reported usage disappears when response text is rejected

Locations: `backend/app/providers.py:315–331`, analogous Claude/Ollama paths,
and `backend/app/registry.py:178–188`.

An HTTP 200 Chat Completion reported 100 prompt tokens and 50 completion tokens,
including reasoning tokens, with empty visible content and finish_reason length.
Relay raised `OpenAI returned no text.` and recorded zero tokens. Text validation
runs before provider usage extraction; the caller also only accumulates usage
after a successful return.

Whether the answer is usable is separate from whether reported work occurred.
Record usage for the physical provider attempt even when content/schema validation
fails. Moving record_usage alone is insufficient while llm_node still accounts
only after a successful return. Preserve usage/error status through that boundary.

### TA02 [P1] — Accounting outage plus interruption replenishes a resumed budget

Locations: `registry.record_spend`, `worker.persist_spend` and post-event
persistence, and `compiler.restored_tokens`.

With a limit of 15, the first model response reported 15 tokens. Accounting writes
were made unavailable, while the success event/checkpoint still committed. The
worker was then cancelled at that exact commit barrier. On recovery, the first
answer was correctly replayed, but missing accounting became a zero baseline.
A second 15-token call ran and the workflow succeeded.

Observed: 30 known response tokens, 15 final accounting tokens. Durable usage-event
rows contained the full 30, so the information was not wholly lost; the budget
restore path did not use it. No first-call replay was required for this failure.

Retaining a paid answer on an accounting outage remains the right goal. Do not
fix this merely by discarding that answer and triggering another model call.
Require consistent durable usage evidence before further spend, reconstruct from
deduplicated authoritative call records when possible, or pause/fail closed when
the used allowance cannot be established.

### TA03 [P2] — Nested-agent cancellation double-counts metrics

Location: `backend/app/compiler.py:648` and event aggregation in
`backend/app/operator_metrics.py:48–65`.

A parent Agent made a 15-token call, delegated to a specialist which made a
15-token call, and was cancelled during its next call before usage was returned.
Run accounting correctly contained 30 known tokens. The specialist success event
reported its 15, then the parent's cancellation event reported the inclusive
checkpoint-owner total of 30. SQL operator metrics therefore reported 45 tokens
and 3 calls, rather than 30 known tokens and 2 completed measured calls.

The ordinary success control reported 45 for three 15-token responses correctly.
The error is specifically in cancellation attribution, not every delegation.
The interrupted third request's actual consumption is unknown; that does not
justify duplicating already reported specialist usage.

Use one non-overlapping attribution rule for success, pause, failure, cancellation
and retry events. Prefer aggregating unique physical-call records rather than
adding arbitrary outcome snapshots.

### TA04 [P2] — A recovered token-budget refusal is presented as complete

Locations: `registry.llm_node:172`, `execution_policy.execute_with_policy`, and
`compiler.py:575–592`.

After one call consumed the 10-token limit, the second LLM was refused before its
provider request. Under both continue and route, the workflow finished success
with truncated false, despite skipping model work. Under fail, the run failed
as expected.

Only ActionBudgetExhausted sets the typed budget_exhausted outcome. Token refusal
is an ordinary ValueError, so it misses the existing incomplete-run propagation.
Use a typed token-budget cause that reaches the same incomplete-run machinery.

## Policy and compatibility limitations

### TA05 — Current limit is a soft guard, including sequential execution

The worker accepted responses costing 80 and 30 tokens against a limit of 100,
then completed with 110 tokens and truncated false. Admission checks whether past
spend has reached the threshold; it does not bound the next call's consumption.

This is not automatically a defect in a documented soft policy: Microsoft's
published response-based policy also permits overshoot. Choose and name the
contract. A strict mode needs model-specific input bounds/counts plus enforced
output allowance, durable reservations, and settlement. max_tokens by itself
does not bound input. The desired response to reaching the threshold on the
final, otherwise completed call also needs to be explicit.

### TA06 — Missing usage is represented as zero, and the call vanishes too

Three valid model responses without usage metadata succeeded under a one-token
threshold. Accounting/metrics reported zero tokens and zero calls. This behavior
is partly documented, but it conflates unavailable measurement with free work.

Track attempted/completed calls independently of token availability. Represent
usage as reported, partial or unavailable; interrupted attempts can be unknown.
Strict enforcement must define what it does with unknown consumption, while
reporting can retain the known subtotal without pretending it is the true total.

### TA07 — Claude cache input is discarded when present

Location: `backend/app/providers.py:278–282`.

A valid synthetic Messages response with input 10, cache creation 400, cache read
600 and output 5 was recorded as 15 instead of 1,015 processed tokens. Relay
currently does not request Claude prompt caching, so this is a conditional
adapter-compatibility gap, not proof of live cache underbilling in today's calls.
Preserve the original category breakdown and normalize total input when those
features are enabled.

The OpenAI positive control already reports 150 correctly for prompt 100 and
completion 50 with cached/reasoning breakdowns. Those details are subsets: adding
them to the aggregates would introduce a new error.

### TA08 — Old accounting snapshots can replace newer ones

A controlled two-thread SQLite test reproduced 30 saved tokens becoming 10 after
a delayed old snapshot committed. This is the prior A5.3 finding, not a newly
demonstrated path through an accepted parallel workflow: parallel branches remain
unimplemented. Retain ordered/deduplicated persistence as an A5.3 prerequisite.

## Recommended order

1. Correct known-usage loss, cancellation attribution, typed token exhaustion,
   and recovery from missing/inconsistent persisted accounting. Add permanent
   reproductions before implementation changes.
2. Establish a single physical-call accounting identity and durable evidence path.
   Separate logical invocation, physical retry attempt and checkpoint replay;
   replay makes no provider call, while a real repeated request is new work.
3. Make the current soft policy explicit, distinguish unknown usage from zero,
   and retain provider-specific token categories. Cost estimation, if added,
   must remain a separately versioned price calculation.
4. Implement strict reservation only against a specified provider/model contract.
   Reserve, send, settle; keep unresolved calls visible after cancellation/crash.
   Do not copy AWS quota weighting or an approximate token estimator into a
   promise about exact billed consumption.

No Redis migration is necessary to correct the demonstrated per-run accounting
defects. Per-tenant quotas and fleet-wide fairness are separate roadmap work.

## Verification

- Nine probe groups reproduced all recorded observations, including a correct
  nested success control and a correct OpenAI inclusive-total control.
- Real provider adapters, the compiler, Worker and temporary SQLite storage were
  used, with synthetic HTTP responses and controlled cancellation barriers.
- Existing relevant tests: **39 passed** across token-budget, observability,
  OpenAI, Claude and S07-boundary modules. The new observations identify gaps in
  that coverage; the passing suite is not evidence that these defects are absent.
- No live provider calls, charges, remote billing experiments, PostgreSQL run,
  full regression suite or hosted CI check was performed.
- Evidence and reproducer: `evals/results/token-accounting-review-2026-10-25/`.
  Application-source hashes are recorded in results.json. Source files were
  unchanged during the probes.
