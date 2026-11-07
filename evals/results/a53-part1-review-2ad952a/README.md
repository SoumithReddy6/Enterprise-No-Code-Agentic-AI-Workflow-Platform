# A5.3 Part 1 / TA08 independent implementation review

Reviewed commit: `2ad952acb1dde1ba88dee977aab7bdf35c8ce874`.
Date: 2026-11-06.
Report: `docs/reviews/a53-part1-review-2026-11-06.md`.

Verdict: request changes, A53-I01 (P1). A newer usage generation can remain pending after a successful older save while an Agent repair dispatches another provider request.

Reproduce without changing application files or the user's database:

```sh
PYTHONPATH=. .venv/bin/python evals/results/a53-part1-review-2ad952a/probe_pending_spend.py
PYTHONPATH=. .venv/bin/python evals/results/a53-part1-review-2ad952a/probe_pending_spend.py --control
```

The default run asserts the current defect: dispatch at generation 2, acknowledgement 1, pending true, stored spend 5. The control installs a test-only rechecking wrapper and asserts dispatch at generation 2, acknowledgement 2, pending false, stored spend 10. Both finish with 15 tokens, so the provider-boundary assertion matters. Only model responses and persistence failures/timing are controlled; the Worker, Store, Agent repair and accounting paths are real.

`pending-spend.json`, `pending-spend-control.json` and `repeat-results.json` retain these outcomes. `preserved-inputs.json` records 77 inputs for the preservation check. `checks.json` records independent command results. `hosted-ci.json` records the hosted run on the exact commit. Storage outputs use a separate folder; the initial sandbox-blocked attempt and its rerun are kept apart.

No application edits, commits, pushes or messages to other people were made by this review. No raw credentials or user database contents are included.
