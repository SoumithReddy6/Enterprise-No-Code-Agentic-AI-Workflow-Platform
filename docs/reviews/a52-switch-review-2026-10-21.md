# A5.2 Switch backend review

Reviewed commit: `7d846f615be6cb9aa81e76c4ea656ac547d309e8`.
Scope: combined Tasks 2 and 3. Hosted CI is left to the user. No application code changed.

## R01 — P2: independent read accepted at validation, refused at execution

A valid contract-2 workflow can run Request reader → Agent generating a Jira query →
Jira search → Switch. Validation returns no errors. Jira successfully returns records,
and the runtime labels `records.text` as `source`. The Switch nevertheless fails with
“Route on checked values, or use a Condition, which supports review.”

Resume restores the Reader, Agent and Jira checkpoints without repeating their calls,
then refuses the same independent source again. The control workflow, with a configured
query and no intervening Agent, succeeds and selects Default.

The mismatch is between two lineage rules:

- `compiler.py:300` accepts the existing `static_label(...) == STATIC_TRUSTED` rule.
  `provenance.independent_source` explicitly establishes Jira record reads as independent
  sources, regardless of what generated their search query.
- `request_safety.py:215-219` also calls `unchecked_model_input`, which follows the Jira
  request's inputs back into the Agent. It therefore rejects the returned source data
  despite the trusted runtime label. The same traversal does not stop at the independent
  read boundary.

This is a false refusal, not evidence of an unauthorized action. It contradicts the
documented supported-input rule and allows a workflow to validate successfully but fail
only after paying for the model and completing the read.

Recommendation: make validation and execution apply the same independent-source
boundary. Preserve refusal of raw model values, model-derived calculations without
verification, arbitrary HTTP/Python results and write responses. Add positive worker
tests for independent record reads whose queries are model-generated, fresh and resumed.
If that lineage is intentionally unsupported, reject it at validation and explicitly
document the narrower rule instead.

Reproduction (repository root):

```bash
.venv/bin/python evals/results/a52-switch-review-2026-10-21/probe_switch_read_trust.py
```

The probe uses a temporary SQLite database, real Store/Worker/ToolService paths, a
fixture model and httpx MockTransport. It sends no network requests and changes no real
workspace data. Exit 1 is the intentional failing assertion that the allowed workflow
should succeed. Results are in `evals/results/a52-switch-review-2026-10-21/worker-results.json`.

## Fresh verification

- Switch, route contract, request control flow and compiler: 117 tests passed.
- Safety boundary, Reader, provenance foundation/static rules and receipts: 149 passed.
- Frontend unit tests: 102 passed, including the configured-route fixtures.
- `git diff --check`: clean.

The reported five full backend seeds, browser suite, build, deployed campaign and fault
profile were not repeated during this bounded review. The new positive read-source
tests in `test_switch.py:197-204` currently exercise validation only; that is why they
do not catch R01.

## Assessment

First-match behavior, exact Condition reuse, branch recomputation, the atomic introduction
of the safety rule, and retaining write approval behind a case called `approved` are
sound choices. Fix R01 before declaring the backend tasks closed. Task 4 remains the
next planned feature; no broader redesign is indicated by this finding.
