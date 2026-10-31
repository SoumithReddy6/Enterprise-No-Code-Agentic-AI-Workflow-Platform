# R01 revision-2 plan audit

Plan commit `e50fa65`; unchanged application baseline `812fefd`.
Report: `docs/reviews/r01-plan-r2-review-2026-10-31.md`.

`prepare_candidate.py` applies the submitted revision-2 behavior in a disposable git-archive
export. It reuses the prior review's committed prototype builder and adds strict sources,
fatal refusal handling, reused-loop dependency checks, caller progress arguments and the
named final-output guard. It does not edit the repository's application.

`probe_plan.py` uses real temporary KB management/search/indexes, ingestion/removal, Worker
and SQLite. Only the LLM response and Jira HTTP transport are substituted. Nine Worker
cases and two shape probes confirm the previous corrections and reproduce one remaining
publication gap. Logs are local. `probe-results.json` includes the removal/check timeline.

Reproduce from the repository root (prints results; does not overwrite saved evidence):

```sh
candidate_dir=$(mktemp -d /private/tmp/relay-r01-plan-XXXXXXXX)
git archive e50fa65 | tar -x -C "$candidate_dir"
.venv/bin/python evals/results/r01-plan-r2-review-e50fa65/prepare_candidate.py "$candidate_dir"
.venv/bin/python evals/results/r01-plan-r2-review-e50fa65/probe_plan.py "$candidate_dir"
```

For the two unchanged-source controls on the unmodified baseline:

```sh
baseline_dir=$(mktemp -d /private/tmp/relay-r01-plan-baseline-XXXXXXXX)
git archive e50fa65 | tar -x -C "$baseline_dir"
.venv/bin/python evals/results/r01-plan-r2-review-e50fa65/probe_plan.py "$baseline_dir" --control-only
```

`baseline-controls.json` records that run. Exact output/source replay equality is not a
baseline control: uninterrupted execution publishes one source; loop-checkpoint resume
publishes none and replaces the citation. Both use exactly one model call.

Exit 0 means the expectations, including the remaining **unsafe success**, were observed.
It is not a passing security gate. No application implementation, full-suite run or CI
result is implied. `preserved-inputs.json` records before/after hashes of the submitted
plan, selected application files and the two earlier evidence folders.
