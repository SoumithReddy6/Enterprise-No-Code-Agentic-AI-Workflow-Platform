# TA07 independent implementation review

Commit: `c3ee703be9c933442504580d4316ee0ecaaddf17`.
Report: `docs/reviews/ta07-implementation-review-2026-11-05.md`.

## Fresh independent checks

```bash
.venv/bin/python -m pytest backend/tests/test_cache_input_tokens.py backend/tests/test_usage_status.py backend/tests/test_providers.py backend/tests/test_token_budget.py backend/tests/test_usage_attribution.py backend/tests/test_accounting_durability.py backend/tests/test_observability.py -q
```

Result: **111 passed**, exit 0.

```bash
.venv/bin/python -m pytest evals/results/ta07-implementation-review-c3ee703/probe_cache_edges.py --import-mode=importlib -q --tb=short --show-capture=no
```

Result: **9 passed**, exit 0. Two table-driven checks cover 24 literal contract rows;
seven additional cases use real Worker/Store execution. HTTP is synthetic, and every
database is temporary. The probes cover parsing boundaries, rejected answers, nested
cancellation and resume without repeated charges.

## Records

- `preserved-inputs.json`: hashes of 89 original application, design and evidence files.
- `checks.json`: final preservation, independent-test results and builder-evidence checks.
- `hosted-ci.json`: exact-SHA hosted job status and selected log summaries.

Hosted run `42124859362` passed all five jobs on the reviewed commit. Backend ran twice
with 2,010 passed and two skipped each time (2,012 collected); schema checks passed 25,
frontend 115, browser-layout 49 and native-macos 16. The frontend tooling and final
fault-profile check also passed.

Builder evidence inspected, without rerunning: six baseline defect failures/four controls,
nine mutant kills, five full 2,012-test seed runs, the six-file isolation proof and the
24-row deployed report. The original evidence files remain untouched.
