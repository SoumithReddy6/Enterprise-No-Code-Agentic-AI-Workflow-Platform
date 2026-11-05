# TA06 draft review evidence

Reviewed baseline: `bcc8d9b1e1e421a144b2738ce0630a21150c9061`.
Date: 2026-11-05, America/New_York.
Report: `docs/reviews/ta06-design-review-2026-11-05.md`.

`probe_boundaries.py` runs the original missing-usage reproduction and instruments the
draft's proposed request/response marker positions in memory. It uses the current real
adapters, `httpx.MockTransport` and disposable SQLite storage. The original probe's main
is never run, and its saved results are preserved.

`boundary-results.json` records the existing 0-call/0-token defect and seven marker cases.
An invalid URL has zero transport invocations despite the proposed sent marker. Claude
and OpenAI malformed usage containers have a completed, decoded response despite the
missing response-read marker. HTTP errors and invalid JSON also bypass `record_usage`.
A normal reported response demonstrates the extra marker keys in a direct caller's
usage dictionary. These are observations about literal draft instrumentation, rather
than an implemented TA06 change.

`baseline-tests.txt` records 80 passing existing tests. `preserved-inputs.json` verifies
the original source, draft and original evidence hashes after the review.

Reproduction commands from the repository root:

```bash
PYTHONPATH=. .venv/bin/python evals/results/ta06-design-review-bcc8d9b/probe_boundaries.py
.venv/bin/python -m pytest -q backend/tests/test_providers.py \
  backend/tests/test_token_budget.py backend/tests/test_usage_attribution.py \
  backend/tests/test_accounting_durability.py
```

The first command exits zero when the diagnostic counterexamples reproduce. It is not
a product correctness gate. All external responses are synthetic and no live model is
called.
