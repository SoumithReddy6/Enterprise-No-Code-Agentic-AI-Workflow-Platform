# Request admission interfaces (Step 2)

This step declares and validates interfaces. It does not install Redis, make routing
model calls, verify extracted values, or enable human review. The three future node
types are deliberately absent from the production registry until their handlers ship.

`Workflow.contract` is the integer 1 or 2. Omitted values mean 1. Existing workflows
remain runnable, and the editor preserves a loaded contract through editing, undo,
import and save. The starter remains contract 1 until the complete contract-2 path is
available. Contract changes invalidate highlighting from an older execution.

Node definitions publish `branch_routes` and `optional_inputs`. Condition declares
`true`/`false`; admission nodes will declare `continue`/`reply`. Each route must have
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
independence of returned records. The later receipt and runtime execution boundary
must enforce the exact run, value and policy before consequential execution. Invalid
drafts remain saveable; validation and run submission reject them before a run exists.
