# TA05 independent implementation review

Commit: `51a8318bffc0998b5973d9d6dc7e8bb3dce1d8b6`.
Report: `docs/reviews/ta05-implementation-review-2026-11-06.md`.

## Fresh independent checks

```bash
.venv/bin/python -m pytest backend/tests/test_token_admission_limit.py backend/tests/test_token_budget.py backend/tests/test_token_budget_recovery.py backend/tests/test_accounting_durability.py backend/tests/test_approvals.py backend/tests/test_durable.py backend/tests/test_usage_status.py backend/tests/test_cache_input_tokens.py -q
```

Result: **146 passed**, exit 0.

```bash
.venv/bin/python -m pytest evals/results/ta05-implementation-review-51a8318/probe_terminal_edges.py --import-mode=importlib -q --tb=short --show-capture=no
```

Result: **14 passed**, exit 0. The probes cover the remaining administrative exits,
terminal cleanup independent of resume, bad accounting, stale leases and failed commits.
They use real Store/approval transactions in temporary databases and send no requests.

## Records and inspected evidence

- `preserved-inputs.json`: hashes of 89 original application, design and evidence files.
- `checks.json`: preservation, fresh-test results and builder-evidence source comparisons.
- `hosted-ci.json`: exact-SHA job states and selected log summaries.

Inspected without repeating: the builder's baseline (five failures/four controls), nine
mutant kills, five full 2,021-test seed runs, six-file isolation proof and 24-row deployed
report. The review explains why combined mutant M9 is a valid end-to-end check, while
separately probing terminal cleanup itself.

Hosted run `42232453634` passed all five jobs on the exact reviewed SHA. Backend ran
twice with 2,019 passed and two skipped each time (2,021 collected). Schema checks passed
25, frontend 115, browser-layout 49 and native-macos 16. Frontend tooling and the final
fault-profile check passed too.
