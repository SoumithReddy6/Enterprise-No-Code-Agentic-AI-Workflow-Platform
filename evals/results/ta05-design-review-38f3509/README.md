# TA05 design-review evidence

Application baseline: `38f3509`.
Review: `docs/reviews/ta05-design-review-2026-11-05.md`.
The application and draft were not edited.

## Independent baseline tests

```bash
.venv/bin/python -m pytest backend/tests/test_token_budget.py backend/tests/test_token_budget_recovery.py backend/tests/test_accounting_durability.py backend/tests/test_approvals.py -q
```

Result: **106 passed**, exit 0.

## Terminal routing and retention probes

```bash
PYTHONPATH=. .venv/bin/python evals/results/ta05-design-review-38f3509/probe_terminal_paths.py
```

Result: exit 0; five terminal-path checks and three retention checks. The script uses
temporary databases, real Store/approval APIs and synthetic durable accounting fixtures.
It sends no model or business request.

The ordinary finish control calls finish_run once. Queued cancellation, paused
cancellation, rejection and expiry each terminate without calling it. Durable spend is
110 in every fixture. The spy only observes routing; it does not implement TA05.

The retention checks seed the prospective prior token_limit through the existing finish
updates API, resume with durable spend 120, and finish without a new record. The stale
limit 100/spent 110/over 10 survives under current limits 200, 0 and lots. This proves
the merge behavior a TA05 implementation must handle, not a released TA05 defect.

`probe-results.json` holds all rows. `preserved-inputs.json` holds six original-file
hashes; `checks.json` records final preservation and verification. `sources.json` records
the primary documentation checked for the policy comparison.
