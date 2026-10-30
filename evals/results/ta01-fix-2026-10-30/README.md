# TA01 fix: usage counted when a provider answer is rejected (2026-10-30)

Plan: `docs/superpowers/plans/2026-10-25-token-usage-on-rejected-responses.md` (revised after
`docs/reviews/ta01-plan-review-2026-10-25.md`). Application code baseline: `347c82f`
(unchanged up to `236aa6a`, which added plan documents only).

## Change

- `backend/app/providers.py`: each adapter checks the usage shape and records usage right
  after it parses the body, before the content checks. A non-object `usage` still raises
  and records nothing.
- `backend/app/registry.py`: new `call_model` helper used by `llm_node`. It counts reported
  usage on success and on any `Exception`, then re-raises. On `asyncio.CancelledError` it
  keeps the usage in memory and does not persist it. A failure that returned no usage adds
  nothing, and makes no accounting write (as before). The `model.usage` journal line gets
  `error_type` for a rejected or cancelled response.
- `docs/operations.md`: two passages.

## Results

| Check | Result |
| --- | --- |
| New tests on `347c82f` application code (`baseline-347c82f-app-code.txt`) | 16 defect tests (D) fail, 7 controls (C) pass |
| New tests on the fix | 23 of 23 pass |
| Mutants (`mutants.py`, `mutants.json`, fresh scratch copy each) | 7 of 7 killed, each only by its planned tests |
| Backend `PYTHONHASHSEED` 0–4 | 1,851 passed each (1,828 before + 23 new) |
| Isolation: clean `HEAD` export + the 7 commit files (`isolation.txt`) | 1,851 passed |
| Deployed scenario gate (`production/report.json`) | 24 of 24 rows passed; source unchanged during the run |
| Revised probe (`probe_rejected_paid_response.py`, `rejected-response.json`) | 150 tokens accounted, 1 call, one durable snapshot of 150 |

`production/report.json` SHA-256: `b690d5b67a528b3bfe910aa43a44d92361783dd0d97b0e0075fa8b46d3c6dc34`.

The historical probe in `evals/results/token-accounting-review-2026-10-25/` asserts the old
result (0 tokens). It was not run and not changed; its file hashes were the same before and
after the revised probe ran.

## Limits

- Synthetic HTTP responses only; no live provider was called.
- A call that returned no usage (an HTTP error, a timeout) still adds nothing. Its real
  consumption is unknown (TA06).
- TA02, TA03, TA04, TA05, TA07 and the monotonic accounting snapshot are not changed.

Logs (`*.log`), `runs.json`, `provider-requests.json` and `production/switch/` stay local.
