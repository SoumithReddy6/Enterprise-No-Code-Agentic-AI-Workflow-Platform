# Purchase-input safety audit — 2026-10-08

Reviewed commit: `477e6981bd13a4bf69801d4ff47aa68c4b4248ab`. No application, example, test, saved workflow or real run was edited.

## Screenshot confirmed in stored runs

A read-only query for exactly `How do AI workflows work?` found three successful runs of the older seven-node Purchase routing workflow. Its extraction agent returned `{"amount": 0}`, and the `gt 1000` condition correctly chose false. The auto branch printed the approval message.

The old schema required a non-negative number. It could not represent missing data with null; zero was valid even though the input supplied no amount. Shape validation did not verify extraction accuracy.

The screenshot contains only input, agent, condition, prompt and response nodes. It did not send an external purchase or invoke the durable approval subsystem; the approval was text.

## Current example: partial repair verified

Commit `477e698` makes amount nullable, instructs the model to use null for missing totals, and adds a missing-total branch. Existing saved workflows and historical runs are snapshots and do not change when the example file changes.

Through the actual compiler and live local `llama3.1:latest`, three cases passed:

- The screenshot question extracted null and reached the `none` response.
- `Hello!` extracted null and reached the `none` response.
- An explicit $24 purchase extracted 24 and reached the `auto` response.

## P01 — invented numbers still bypass the missing-total gate

The runtime checks only the output's JSON schema. Any non-negative number, including zero, passes. The `empty` operator treats zero as a present value, so the gate passes it to the amount comparison.

A controlled provider returning `{"amount": 0}` for the screenshot question still reached auto-approval through the current ten-node example. Returning 24 for `Hello!` did too. These are actual compiler, extraction, schema and branch executions with deterministic provider responses. They show the validation gap; they are not claims that the updated prompt emitted those responses in the live checks.

Changing the threshold, banning zero, or adding another model-produced validity/confidence field would not validate the source. Explicit zero-dollar purchases must be distinguished from missing data.

Required invariant for consequential automation: approval routing consumes only an amount independently verified against the input or a validated structured order record. Missing, unsupported or ambiguous values must reach a separate refusal/review path before the monetary threshold. A quoted source span can support this if the runtime verifies its presence and parses its value; matching a number alone still does not establish that the message is a purchase request.

## Coverage gap

The live purchase campaign in `scripts/check_workflows.py` covers valid high and low orders. It does not exercise unrelated input, missing totals, or a schema-valid hallucinated amount. The latter requires a deterministic regression; passing a few improved-prompt runs cannot prove it is blocked.

Keep controls for explicit zero, $24, exactly $1,000, $1,350, missing totals, irrelevant questions/greetings, invented numbers, negative values and malformed types. If writes are later attached, assert no approval record or external send on invalid-input paths.

## Evidence

`evals/results/purchase-input-audit-2026-10-08/` contains sanitized stored-run observations, eleven controlled cases (eight safe controls and three reproduced unsafe paths), three live cases, source hashes and rerunnable probe scripts.

No whole-system campaign or complete backend suite was rerun. The current prompt improves the observed behavior, but P01 remains open as a runtime guarantee.
