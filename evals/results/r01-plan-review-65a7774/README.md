# R01 plan audit

Plan commit: `65a7774`; application baseline: `812fefd`. Original files unchanged.

- `prepare_candidate.py`: applies the proposed three-file behavior in a disposable archive
  export only, refusing a `.git` checkout.
- `probe_plan.py`: real temporary KB/Worker/checkpoint counterexamples and controls, plus
  shape checks. Provider and Jira responses are synthetic. Exit 0 means its asserted
  observations held; it does not mean the plan's gaps are fixed.
- `probe-results.json`: retained results, including the actual application import root.
- `preserved-inputs.json`: original plan/code/evidence hashes captured before the audit.
- `probe-run.log`: local diagnostic log, ignored by git.

Reproduce from the repository root:

```bash
candidate=$(mktemp -d /private/tmp/relay-r01-plan-XXXXXXXX)
git archive 65a7774 | tar -x -C "$candidate"
.venv/bin/python evals/results/r01-plan-review-65a7774/prepare_candidate.py "$candidate"
.venv/bin/python -I evals/results/r01-plan-review-65a7774/probe_plan.py "$candidate"
```

The prototype intentionally mirrors the plan's current behavior, including the explicit
no-platform shortcut and unchanged loop-owner dependency filter. It is not a complete fix.
