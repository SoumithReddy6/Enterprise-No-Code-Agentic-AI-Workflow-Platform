# A5.3 design revision 2: evidence

Design: `docs/superpowers/specs/2026-10-24-parallel-branches-design.md` (revision 2).
Review of revision 1: `docs/reviews/a53-parallel-design-review-2026-10-25.md`, with its
probe in `evals/results/a53-design-review-2026-10-25/` (not changed).

Application tree: `fb32e95`, identical to `main` at `36bac03` (`git diff --quiet` checked).

## TA08 probe

`ta08_probe.py` calls `Store.reserve_accounting` and `Store.record_accounting` on a temporary
SQLite database. A delayed thread commits an older snapshot after a newer one has committed.

```sh
.venv/bin/python evals/results/a53-design-r2-2026-11-06/ta08_probe.py
```

Result (`ta08_probe.txt`):

| Writer | Older snapshot | Outcome |
| --- | --- | --- |
| both | lower `tokens_spent` (10 after 30) | Refused with `AccountingUnavailable(STALE)`. Tokens stay 30. In a worker this fails the run. |
| both | equal `tokens_spent` (30), older action counter (2 after 3) | Accepted. The action counter goes back from 3 to 2: one action is refunded. |

TA02's monotonic check turned the review's token loss (30 → 10) into a refusal. The action
refund is still possible. Sequential execution cannot reach either case. Parallel branches can,
because the worker's three persistence paths commit in threads with no ordering.

## Why the review's own probe was not re-run

`evals/results/a53-design-review-2026-10-25/probe.py` calls `Store.worker_event` without
accounting. Since TA02, a contract run refuses that (`AccountingUnavailable(MISSING)`), so the
probe stops before its accounting section. It was run on a scratch copy only; the auditor's
folder was not changed.

## Sources

The published sources read on 2026-11-06 are listed in the design under "Token limits under
concurrency, and ordered state writes".
