# TA07 design-review evidence

Baseline: `dce6f66`. Design and application code are unchanged.
The draft was committed as `6c2ea5b` during review; no reviewed content changed.
Review: `docs/reviews/ta07-design-review-2026-11-05.md`.

## Independent checks

```bash
.venv/bin/python -m pytest backend/tests/test_usage_status.py backend/tests/test_providers.py backend/tests/test_token_budget.py backend/tests/test_usage_attribution.py backend/tests/test_accounting_durability.py backend/tests/test_observability.py -q
```

Result: **101 passed**, exit 0.

```bash
PYTHONPATH=. .venv/bin/python evals/results/ta07-design-review-dce6f66/probe_design.py
```

Result: exit 0; four adapter cases and two helper demonstrations.
The script uses real Worker/Store and synthetic HTTP responses in temporary databases,
with no paid calls or application edits. `probe-results.json` records the results:

| Probe | Current result |
|---|---|
| Claude input 10, creation 400, read 600, output 5 | 15 tokens; reproduces the finding |
| OpenAI prompt 100, output 50, cache read 60 with write absent | 150 tokens; annotation not yet retained |
| Ollama prompt 6,024, cached 6,016, output 5 | 6,029 tokens; annotation not yet retained |
| Claude empty usage object | Unreported, zero known tokens |
| Unchanged TA06 helper receives known subtotal 410 and output 5 | Reported; TA07 needs separate completeness evidence |
| Unchanged helper receives fabricated prompt zero and missing output | Partial; must not replace the empty-receipt behavior |

The helper rows illustrate implementation risks in the draft. They do not assert that
unimplemented TA07 code has already made these mistakes.

`preserved-inputs.json` records six original input hashes; `checks.json` records final
preservation and gate results. Sources and the APISIX merge verification are summarized
in `sources.json` and linked in the review.
