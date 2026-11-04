# R01 revision-3 plan verification

Plan commit: `9801c46`; application baseline: `812fefd`.
Report: `docs/reviews/r01-plan-r3-review-2026-10-31.md`.

Application/plan/previous evidence files were not edited. Candidate changes live only in
temporary git-archive exports. The builder reuses the two prior committed prototype builders
and adds revision 3's saved dependencies, both owner-registration points and union check.

`probe-results.json`: eleven real-Worker cases plus two direct contract probes. Expected
refusals and unchanged-source controls pass on the candidate. The provider and Jira HTTP
responses are synthetic; KB ingestion, removal, verification, Worker and SQLite are real.

The mixed partial-progress case uses different documents for the reused and fresh items.
It asserts exactly two provider calls total, with the old item not repeated, and checks the
reused document at the Response boundary even though that document is not displayed.

`mutants.json`: N14 is caught by both full-checkpoint and partial-progress cases; N15 is
caught by the partial-progress case while the full-checkpoint control still passes.
The checker rejects unrelated import failures as invalid mutant evidence. Logs stay local.

Reproduce from the repository root without overwriting the saved evidence:

```sh
candidate_dir=$(mktemp -d /private/tmp/relay-r01-plan-r3-XXXXXXXX)
git archive 9801c46 | tar -x -C "$candidate_dir"
.venv/bin/python evals/results/r01-plan-r3-review-9801c46/prepare_candidate.py "$candidate_dir"
.venv/bin/python evals/results/r01-plan-r3-review-9801c46/probe_plan.py "$candidate_dir"
```

Individual cases can be selected with `--case cached_loop_after_reuse` or
`--case cached_partial_after_reuse`. The final mutant checker is:

```sh
.venv/bin/python evals/results/r01-plan-r3-review-9801c46/check_mutants.py
```

That command writes new local logs and refreshes this review's `mutants.json`. It does not
touch earlier evidence or the application's working tree. Full acceptance tests and CI
remain required after implementation; this is approval of the plan only.
