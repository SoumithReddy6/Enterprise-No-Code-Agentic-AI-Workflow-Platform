# Mandatory Agent input validation and supported decision fields

Status: proposed design for review; product implementation has not started.

Baseline inspected: `477e6981bd13a4bf69801d4ff47aa68c4b4248ab`.

## Intent and confirmed rollout

A message unrelated to a purchase must not become an invented zero-dollar order and
reach an auto-approval branch. The same boundary must protect Boolean and category
decisions, rather than adding an amount-specific patch to one example.

The user explicitly chose **mandatory checking for existing and new Agents**. Every
Agent needs an author-configured input policy. Existing drafts remain saveable, but
validation, submission, compilation and resume refuse missing policies. The platform
must not invent acceptance criteria for a user's saved workflow.

This feature has two distinct responsibilities:

1. Assess whether the resolved input fits the Agent's stated task, before it calls
   tools, delegates or produces its task answer.
2. Verify the fields that an automatic Condition will use, before publishing them
   to downstream nodes. A model's `valid` flag is not proof of those fields.

Input suitability assessed by an LLM remains fallible. Deterministic checks establish
specific properties, such as a number appearing in the input or a Boolean agreeing
with a supplied record. They do not establish arbitrary business truth, the authenticity
of a user-supplied record, or the meaning of unrestricted prose.

## Approaches considered

- **Required assessment plus independent field checks — selected.** Reuses the Agent
  runtime, preserves public output ports and makes missing evidence stop execution.
  It adds a bounded assessment call and requires workflow authors to configure rules.
- **A second general-purpose judge model.** Adds another probabilistic decision and
  another model dependency. Agreement between two models is not a proof; this is not
  the default or the safety boundary.
- **Prompt changes alone.** Cheap and helpful, but a schema-valid invented `0` or
  `true` still escapes. This does not address the reproduced defect.

## Configuration contract

Add `input_policy` to `AgentConfig`. It has no enabled/disabled switch and no permissive
default. A missing policy produces a message naming the Agent and the required setting.

```json
{
  "input_policy": {
    "version": 1,
    "requirements": "Accept a purchase request with one explicit total in US dollars. An unrelated question is invalid. A request without a total needs information.",
    "input_schema": {},
    "output_checks": [
      {
        "kind": "source_literal",
        "field": "amount",
        "evidence_field": "amount_evidence",
        "format": "usd"
      }
    ]
  }
}
```

- `requirements`: 1–4,000 characters after trimming; explicitly states acceptable
  input, invalid input and required information.
- `input_schema`: optional JSON Schema, bounded at 20,000 serialized bytes. If set,
  the resolved input must parse as exact JSON and satisfy it before the assessment
  call. Use a bounded structural/numeric subset: regex/format validation and remote
  references are refused. Schema operands retain the existing documented API parsing
  limits. This restriction applies to the new input policy, not existing output schemas.
- `output_checks`: at most 32 checks; duplicate target fields, unknown kinds and
  invalid object paths are rejected. Paths use the existing dotted-key grammar with
  an additional 128-character maximum. `$` names the entire raw input/output string;
  other paths read keys from exact JSON objects.
- The whole policy is bounded at 32,000 serialized bytes. Extra keys are forbidden.

Initial independent checks are deliberately small:

| Kind | Configuration | Runtime proof |
|---|---|---|
| `input_field` | output `field`, source `input_field`, `operation: equals` | The output equals a field in the resolved JSON input, with strict Boolean handling and exact numbers. |
| `input_field` | the same paths, `operation: map`, explicit mapping of at most 100 string keys to scalar JSON values | The output equals the author's deterministic mapping of the input field. Useful for Boolean and category decisions. |
| `source_literal` | output `field`, output `evidence_field`, `format: text` | The evidence string occurs exactly once in the original resolved input, and the output equals that string verbatim. |
| `source_literal` | the same paths, `format: number` or `usd` | The quoted literal occurs exactly once, parses by the selected fixed grammar and equals the output exactly. The input must contain exactly one candidate in that grammar; competing candidates require clarification. |

`number` accepts an explicitly written finite numeric literal within Relay's existing
range; `usd` accepts a dollar-prefixed literal or a literal followed by `USD`, including
correctly grouped thousands. Neither performs arithmetic, currency conversion or a
guess at which of several amounts is the total. There is no author-supplied regular
expression, Python code or network call in a check.

For the purchase example the task answer becomes, for example,
`{"amount": 24, "amount_evidence": "$24"}`. Its output schema declares both fields.
No currency candidate, a missing required field or `null` produces `needs_information`;
an invented value or contradictory candidate fails verification. An explicit `$0`
remains valid. JSON fields can express `false` without conflating it with missing data.

A check proves only the configured relationship. A quoted `$24` in unrelated prose
does not itself prove that the prose is a purchase; the input suitability assessment
still applies. Workflows needing stronger intent or authorization guarantees must use
a structured, authenticated request and deterministic rules or human review.

## Runtime sequence and assessment protocol

Implement the capability in `execute_agent`, used by graph Agents, loop bodies and
specialist delegation. It validates the policy at runtime as well as at compilation.
It operates on the **resolved input handed to that invocation**, before formatting
`user_prompt`. Parent and specialist policies are independent.

1. Enforce input and policy bounds; run the optional input schema.
2. Ask the Agent's already configured provider/model to assess that input against
   `requirements`. This call has no tools, delegation, memory writes or task actions.
   Treat input as data, including instructions embedded in it.
3. Validate this internal protocol strictly:

   ```json
   {"status": "valid", "reason": "Required purchase information is present."}
   ```

   Status is exactly `valid`, `invalid` or `needs_information`. Reason is non-empty,
   at most 1,000 characters. No extra keys are accepted. The runtime, not a field the
   model provides, decides whether any fact is verified.
4. Permit one contract-repair call using the existing bounded repair helper. A second
   malformed result stops with `guard_contract_failed`; it never becomes valid prose.
   Provider errors and exhausted tokens cannot silently skip the guard.
5. Only `valid` permits normal Agent execution. After the normal output schema and
   grounding checks, run `output_checks` on the candidate and original resolved input.
6. Publish the normal public outputs only after those checks succeed. Keep `text`,
   `provider`, `sources` and `grounding` ports unchanged. Evidence fields live in the
   structured `text` value, not new ports.

Assessment and repair calls use the normal model authorization, token ceiling and
durable spend accounting. They do not debit the action budget, increment tool-step
identities, or consume `max_steps`. Use a separate stable assessment invocation suffix.
This normally adds one model call per fresh Agent invocation, with at most one repair.
Each assessment/repair response is limited to 512 output tokens, subject to the remaining
run token ceiling. It does not inherit an Agent's potentially much larger task-answer cap.

Preserve the existing free, deterministic no-evidence/answerability abstention path:
it may return the standard abstention without a new paid assessment because no task
answer or action is produced. Record `assessment_skipped: safe_abstention`; this does
not mark any decision field verified. A policy is still mandatory on that Agent.

## Automatic decisions and failure semantics

A Condition must not treat an unverified model field as a business fact:

- A Condition reading `Agent.text` with `field: amount` requires a successful check
  covering exactly `amount`. An input `valid` flag or output JSON schema is insufficient.
- Conditions over the whole model string require a check covering the whole value.
  A check on one JSON field cannot certify the other fields or the entire string.
- Trace value bindings, not merely flow edges. A Prompt or other transform cannot
  erase model provenance. Initially only an exact pass-through preserves field checks;
  other transformations of model values into decision inputs are refused with guidance
  to bind the checked Agent field directly.
- Copying an invented field through another Agent does not verify it. An `input_field`
  check consuming a model-derived input must also have support for that source field.
  Exact copying proves fidelity to the immediate input, not independence from its model.
- Raw `llm` or `query` output cannot bypass this requirement by replacing an Agent.
  Its use as an automatic decision input is refused unless a supported independent
  check is available; ordinary generation/display workflows keep their current ports.
- Free-text semantic labels with no executable evidence/rule cannot automatically
  authorize a branch under this feature. Use a structured field from the appropriate
  trusted source. A dedicated human review before a Condition would be a separate
  feature; existing write approval occurs later and does not satisfy this requirement.
  Adding a universal semantic judge is outside this change.

The compiler reports missing check coverage; the Condition runtime verifies the
corresponding runtime metadata too, including on restored values. Declared checks
alone cannot authorize an unchecked candidate.

For newly configured decision workflows, an Agent supplying those fields generates
its candidate without write-capable tools or specialists. Attachment analysis enforces
this transitively. Put consequential writes in explicit downstream nodes, after field
verification and their separate approval checks. Otherwise an Agent could write before
its final candidate was checked.

`invalid`, `needs_information`, missing/contradictory support and protocol failures
raise a typed `AgentInputStop`. Add it to the shared `CONTROL_FLOW` tuple. It bypasses
retry/recovery, `on_error: continue/route`, loop `on_item_error`, and parent Agent generic
tool-error handling. It cannot become an empty result, `false` branch or a successful
failed-item count. Callers own one terminal event; the shared policy owns retry events
as before.

The worker finishes the current run as `failed`, with a structured `input_validation`
record. Distinguish user input outcomes from `guard_contract_failed` and provider faults.
No downstream node on the stopped execution path runs. Earlier completed work remains
recorded: this feature cannot roll back earlier external writes. It does not replace
write approval, tenant authorization or uncertain-write reconciliation.

Other Agents' internal write tools remain protected by the existing exact-payload approval
mechanism. Input assessment is required before their first call, but final-answer field
checks do not retroactively verify earlier tool arguments. Do not advertise this feature
as certifying every argument an Agent chooses internally.

## Persistence, resume and bounds

Use reserved `_input_validation` runtime metadata alongside Agent outputs, never a
bindable port. It contains a version, assessment status, check identifiers/verified
paths, and SHA256 fingerprints of policy, resolved input and checked output. Each entry
is strict and bounded at 8,000 bytes. Reasons are bounded at 1,000 characters; no source
quotes or additional copies of the input are stored in metadata. Check identifiers
are small indexes into the fingerprinted policy; metadata stores a fixed reason code,
not the assessment's prose reason. Detailed reasons live in the bounded terminal event
and run rejection record. This keeps 32 paths within the metadata bound.

- Validate metadata on fresh output and checkpoint restore; reject unknown/malformed
  shapes instead of treating a dictionary's presence as success.
- Fingerprints detect stale input/policy/output combinations; they are not signatures
  or a defense against an attacker who controls the database.
- Replay independent checks against the reconstructed original input and restored
  output before downstream execution. Restore assessment only for matching fingerprints.
- A legacy Agent checkpoint/frame lacking the decision is refused with an actionable
  new-run message. Do not infer past validation or silently re-execute settled writes.
- Approval frames retain the bounded decision and fingerprints. Approval resume uses
  them without resetting budgets, identities or rerunning an already-paid assessment.
- Resume API validation occurs before any job/status mutation. A run stopped for invalid
  input is corrected with a new input/new run, not by replaying the same rejected input.
- Preserve existing item-row authority, compact loop checkpoints and event ceilings.
  Metadata must remain bounded even across 1,000 items and nested delegation.

Reuse run JSON/checkpoints and event rows; no new database table or schema column is
required for this design. Do not rewrite historical runs or existing write approvals.

## API, editor and operations

- Validation messages name every Agent missing a policy and every decision field lacking
  check coverage. Draft save remains allowed; Validate and Run are blocked.
- Add an Agent inspector section for acceptance criteria, optional input schema and
  independent output checks. Resolve nested schema definitions or use a dedicated typed
  editor; the current generic settings renderer does not resolve `$ref`.
- Preserve invalid JSON edits as entered. Name the setting and block Validate/Run;
  never send an older hidden policy value.
- Run view distinguishes `invalid`, `needs_information`, `verification_failed` and
  system guard failures. Show the named Agent/reason and let the user edit the input
  and start a new run. Do not label this an ordinary incomplete success.
- Record assessment/check decisions as structured node events with the same frozen
  execution identity and loop ownership. Structured logs contain status/reason code
  and identifiers, never input, model prompts, source quotes or credentials.
- Reuse existing event payloads for operator inspection. An indexed aggregate metric
  is a separate change requiring schema/index design; do not scan large run JSON blobs
  to add one implicitly.
- Update repository-owned examples, browser fixtures, scenario generators and evaluation
  configurations with explicit policies. Do not exempt tests or create a global off switch.
- Document rollout, assessment cost, deterministic check limits and how to repair a saved
  workflow. Existing saved workflows need author edits; example updates do not migrate them.

## Acceptance and cross-checks

Tests must fail on the current baseline before the implementation and cover both safe
inputs and attempts to bypass the boundary:

1. Purchase: unrelated screenshot input, greeting and missing total stop; a forced
   schema-valid invented `0` still cannot reach the condition; explicit `$0`, `$24`
   and `$1350` pass and route correctly. Multiple amounts require clarification.
2. Boolean: missing is distinct from `false`; a guessed `true` conflicting with the
   input record fails; a deterministic mapping of a supported record routes correctly.
3. Categories/text: unsupported labels and fabricated quotes fail; exact supported
   text/mapped categories pass. Read-only summaries can use suitability assessment
   without claiming that every sentence has been independently verified.
4. Guard protocol: extra keys, unknown status, malformed JSON twice, prompt-injection
   input, provider failures and token exhaustion never default to valid.
5. Composition: direct Agent, loop Agent and specialist paths all enforce the same
   boundary. Invalid loop items stop rather than continue. Parent Agents and every
   `on_error` policy cannot swallow the stop. No guarded write is sent after rejection.
6. Provenance: direct checked fields pass; unchecked siblings, transforms, raw LLM/query
   replacement and forged/missing runtime metadata cannot authorize a Condition.
7. Durability: crash/approval resume retains decisions, action identities and token
   spend. Changed inputs/policy/output, malformed metadata and unchecked legacy
   checkpoints fail before downstream calls. Lost lease stops persistence/execution.
8. Rollout: old/new missing policies fail `/api/validate`, submission, direct compile
   and resume. No run/job is created or requeued after refusal. Invalid drafts can save.
9. UI: criteria/check editing, invalid JSON, actionable run rejection and replay
   messages are covered with unit tests and Playwright in a throwaway workspace.
10. Mutation checks: remove each runtime/control-flow/coverage/replay check individually
    and demonstrate that its intended regression fails for the intended reason.

After focused tests: full backend suite at five hash seeds, frontend unit/typecheck/lint/
build/browser checks, the production scenario campaign with real API/worker/auth and
live models, and the 53-question grounding gate through the updated product path.
Keep existing calibrated thresholds unchanged. Record first-attempt/after-repair/failed
assessment-contract counts, model/source hashes and fresh artifacts. A passing old
generation artifact or hosted CI run does not certify this new feature.

Release evidence must distinguish what was mechanically verified, what was assessed by
a model and what remains a semantic judgment. No claim of universal input understanding.
