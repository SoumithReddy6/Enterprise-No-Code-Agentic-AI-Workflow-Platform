# TA06 corrections recheck — 2026-11-05

**Commit:** `ee6b27e71247e10b2a3054d25642624d2ce8c933`.
**Verdict:** TA06-I01 and TA06-I02 are closed. No blocking implementation finding remains
from this recheck. All five hosted jobs passed on this commit, so TA06 is closed within
its approved scope.

## Confirmed corrections

- **I01:** `usage_status` now survives the real journal filter. The strengthened V01
  captures actual emitted JSON, confirms three unreported calls with no invented token
  fields, and refuses an invalid status string.
- **I02:** `UnsupportedProtocol` is now known not delivered. Both a missing scheme and
  `ftp://` fail before any TCP connection and leave no call counter or outcome usage.
  Read/write timeouts still count as unanswered. The exclusion remains specific; it does
  not swallow arbitrary HTTP errors or suppress a genuinely unknown request.
- The corrections stay within the existing 16 collected TA06 cases. Updating operations
  documentation and recording the fourth error type in the evidence README is a sufficient
  explanation of this small addition to the approved design.

## Independent verification

The original review's **18 probes all pass** on the correction. Combined with the 15
current backend TA06 cases, provider tests and observability tests, the fresh command
passes **68 tests**, exit 0. It uses real Store/Worker execution and the original review
probes unchanged. No live model calls are made.

The correction's deployed report has 24 passed rows and 69 recorded source hashes that
match the reviewed tree. Its three isolation hashes also match. The original eight
committed review-evidence files remain byte-identical to the commit. The builder's
five 2,002-test seed runs and three mutant kills were inspected rather than rerun.

[Hosted CI run 42115549627](https://github.com/SoumithReddy6/Enterprise-No-Code-Agentic-AI-Workflow-Platform/actions/runs/42115549627)
passed backend, frontend, browser-layout, native-macos and postgres-conformance on the
exact reviewed SHA. The hosted backend suite ran twice; each run reports 2,000 passed
and two platform skips. The frontend reports 115 unit tests, browser-layout 49 browser
tests, and native-macos 16 tests. Typecheck, lint, build and the final fault-profile check
also passed. Exact-SHA job metadata and selected log summaries are in
`evals/results/ta06-recheck-ee6b27e/hosted-ci.json`. The other evidence is in
`evals/results/ta06-recheck-ee6b27e/checks.json`.

## Nonblocking hardening note

At `backend/app/observability.py:28`, the new restriction handles invalid strings, but
`usage_status=None` is retained and a list or dict raises `TypeError` during set membership.
This was reproduced directly. The production caller in `registry.account_usage` generates
one of four fixed strings; no workflow/API path supplying arbitrary values to this
argument was found. This does not reopen I01 or I02 and is not a TA06 release blocker.

If hardening this internal helper, check that the value is a string before set membership
and drop the field otherwise. The existing V01 can loop over invalid values without
adding collected cases. No further design round is needed.
