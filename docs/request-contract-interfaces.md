# Request admission interfaces, runtime checks and editor (Steps 2–7)

The registry now includes Greeting cache, Request router and Verify extraction.
Contract 2 enables runtime verification and typed human review. New workflow setup
and typed-review presentation are available in the editor; the new narrative starter
uses contract 2. Saved contract-1 workflows retain their behavior. See
[workflow setup and review](request-workflow-setup.md). Live semantic accuracy is Step 8.

`Workflow.contract` is the integer 1 or 2. Omitted values mean 1. Existing workflows
remain runnable, and the editor preserves a loaded contract through editing, undo,
import and save. The new starter uses the complete contract-2 admission path.
Contract changes invalidate highlighting from an older execution.

Node definitions publish `branch_routes` and `optional_inputs`. Condition declares
`true`/`false`; admission and verification nodes declare `continue`/`reply`. Each route must have
exactly one edge. Control nodes cannot recover without a branch or be attached as a
tool, specialist or loop body. Fresh and replayed branches must match the declaration.
Optional inputs need no binding; supplied references receive the same output,
reserved-port and outcome-aware upstream validation as required inputs.

The new strict configuration models reject unknown keys. Prepared replies allow at
most 100 exact message/reply pairs (256/2,000 characters), with normalization collision
detection. A router brief has a nonblank purpose (2,000 characters) and up to 20 entries
of 500 characters in each list. Router decisions require all four fields: route,
reason, missing_fields and reply. Proceed requires empty missing_fields and reply;
clarify/out_of_scope require a nonblank reply. A decision is not authorization.

Extraction configuration uses `expected_schema`, `required_fields`, `field_rules`,
and `cross_checks`. Fields are JSON Pointers (up to 128 characters), with at most 64
required fields and 64 rules; every required field needs a rule. Rule parameters are:

| kind | required parameters besides field/kind |
|---|---|
| quoted | none |
| source_field | source_pointer |
| enum | allowed_values (1–100 JSON scalars) |
| reference_member | reference_list and reference_key (JSON Pointers) |

Cross-checks are up to 20 expressions in the existing Calculate check language.
The whole configuration is limited to 20,000 UTF-8 serialized bytes. A verification
report has verdict, complete and up to 64 unique per-field outcomes (field, status,
reason); it is limited to 8,192 bytes. Incomplete, absent, failed or unverified
coverage cannot yield a passed report. Reports do not mint verification receipts.

Contract-2 structural validation requires Input → Greeting cache → Request router,
using the original trigger message. Reply routes terminate at Response. Extraction
Agents and verification originals also bind to the trigger, never router paraphrases.
Reference-member rules require an explicit, upstream independent-read binding.
Writes and model-dependent business decisions require verification on every possible
path; writes must bind the checked value or its deterministic calculation. Attached
writes are checked at their caller, including transitive dependencies. Narrative Q&A
does not need extraction verification merely to display an answer.

These graph checks are prerequisites. They cannot prove semantic meaning or the
independence of returned records. The runtime receipt and execution boundary
enforces the exact run, value and policy before consequential execution. Invalid
drafts remain saveable; validation and run submission reject them before a run exists.

## Runtime enforcement (Step 6)

Verify extraction returns `text`, `branch`, `reply`, and `report`. Missing or invalid
required facts take `reply`. Complete independent acceptance checks permit `continue`.
Uncertain facts pause for a verification review before returning `continue`; the
router's `proceed` judgment cannot authorize them. Quotation proves origin only.
Configure a source-field equality, accepted enum or independent record membership to
check suitability. An enum still needs independent origin. If those rules cannot
prove the interpretation, a person must accept the displayed original, extracted
value and per-field report. This does not claim general semantic validation by an LLM.

The runtime creates `_verification` receipts and validates them again on resume against
the exact run, original input, value, reference context, verifier identity and frozen
workflow policy. A handler, model response or Redis entry cannot supply a grant.
Human confirmation is also checked against the real tenant-scoped completed approval
record. A label remains an origin label: accepting a guess does not relabel it source.

One pre-attempt eligibility check covers graph nodes, loop bodies, attached Agent
tools and specialists. It runs before action reservation or a network write. Prompt
and Calculate outputs inherit eligibility only after their deterministic result is
recomputed from checked inputs. Calculate evaluates actual dependencies, including
coalesce's short circuit. A schema-declared verified root array can feed For each;
each item receives authority only for that exact element. Agent-authored tool input
must match its eligible bound input exactly; a paraphrase or changed amount is refused.

A failed Calculate check or unchecked business decision requests a **decision** review.
A person must explicitly choose `true` or `false`; an empty approval cannot default to
true. Checkpoints store a compact choice/id/digest record, with the exact payload in
the approval table. Both restored interpretations and restored choices must still
match their actual confirmation. Refusals and review pauses cannot retry, recover via
an error route, become loop-item failures, or become Agent observations.

Ordinary Condition results are recomputed on fresh execution and checkpoint restore;
a changed branch cannot contradict the checked value and configured comparison. Human
control choices are revalidated independently of the action's data binding before
later decisions or writes, including when the payload comes from another verifier.
Expiry therefore cannot be bypassed by wiring around a review's result port. A manual
override is recorded as a choice and never changes a failed automatic check into a pass.

The PostgreSQL conformance CI job also runs the separate-process typed-review campaign
on both disposable SQLite and PostgreSQL schemas. It kills workers after pause,
confirmation and write settlement, and in the genuinely uncertain send window.

Contract-1 behavior is retained. Contract 2 requires its complete graph and configured
acceptance rules. Step 7 adds new-workflow setup and typed-review controls without
converting saved workflows. Business drafts configure no writes and require real
reference connections where applicable. Step 8 separately measures live model quality.
