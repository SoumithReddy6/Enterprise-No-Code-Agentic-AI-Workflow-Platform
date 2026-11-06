# TA05 fix evidence: the run token limit is an admission limit, and overshoot is recorded on the run

Design (approved, P2 tier): `docs/superpowers/specs/2026-11-05-token-admission-limit-design.md`.
Design review: `docs/reviews/ta05-design-review-2026-11-05.md` (TA05-D01, TA05-D02 and the
wording clarifications, all incorporated in the design before the tests). Baseline:
`599a98d` (the design draft; application = `38f3509`). No separate implementation plan:
§6 of the design is the case allocation, gate list and stop rule.

## What changed

| Area | Change |
|---|---|
| `token_budget.py` | The module docstring names the contract: an admission limit, not a hard cap. The limit never aborts a call in flight, and the one-call overshoot bound holds for sequential execution, an unchanged limit and the known reported total. `overshoot(spent, limit)` returns `{"policy": "admission", "limit", "spent", "over"}` when spend is above a non-zero limit, otherwise `None`. Admission arithmetic is unchanged. |
| `storage.py` | `Store._settle_token_limit(row)` removes any earlier `token_limit`, reads the durable accounting through `Store._accounting`, reads the limit, and attaches the record when there is one. A bad limit, `AccountingUnavailable` or a malformed legacy copy gives no record and never raises. `finish_run` and the terminal branch of `request_cancel` call it inside their existing transactions; resume removes `token_limit` with the other terminal fields. |
| `approvals.py` | `_close` (rejection and expiry) calls the same helper in its transaction (TA05-D01). |
| Tests | New `backend/tests/test_token_admission_limit.py`: 9 cases, the design's cap. No existing test changed. |
| Docs | `docs/operations.md` **Token ceiling**: the admission-limit name, an **Overshoot** paragraph (rules 3–6, the `token_limit` record, the strict cap not provided). |

Not changed, as designed: `execution_policy.py`, `registry.py`, `agent_runtime.py`,
providers, metrics, migrations and the frontend. No refusal, retry, status, truncation or
error type changes.

## Files

| File | Content |
|---|---|
| `baseline-599a98d.txt` | The final new tests on a clean archive of `599a98d`: 5 failed, 4 passed. |
| `isolation.txt` | Clean-export proof (git archive `599a98d` + the 6 TA05 files, hashes match the working tree). |
| `mutants.py`, `mutants.txt` | M1–M9 on fresh copies of the isolation base; all 9 planned kills. |
| `seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy: 2,021 passed at each seed (2,012 + 9). |
| `production/report.json` | Deployed scenario gate: 24 of 24 rows passed, no source file changed during the run. |

## Baseline (red) classification

All 5 failures are the missing `token_limit` value on real Worker/Store/approval paths;
none is a missing interface.

| Test | Baseline result | Fixed result |
|---|---|---|
| L01 the finding: limit 100, calls of 80 and 30 | `success`, not truncated, 110 spent, no record | the same, plus `token_limit` over 10 |
| L02 third call refused, then resume under limit 200 | `failed`, refusal typed, no record | `failed` with over 10; the queued resumed run has no record; it finishes `success` at 120 with no record (TA05-D02) |
| L06 cancellation requested while the crossing response returns | `cancelled`, 110 spent, no record | the same, plus over 10 |
| L07 queued cancellation of paid work (`request_cancel`) | `cancelled`, 110 spent, no record | over 10, without `finish_run` (TA05-D01) |
| L08 rejected approval of an over-limit run (`approvals._close`) | `cancelled`, 110 spent, no record | over 10, without `finish_run` (TA05-D01) |

Controls passing on the baseline (4): L03 (exactly 100: the third call is refused by the
`>=` rule, no record), L04[unlimited], L04[under] (no record), L05 (an invalid limit fails
the run before any request; the run still reaches `failed`).

## Mutants

| Mutant | Planned oracle |
|---|---|
| M1 the record only when the worker finishes with success | L02, L06 |
| M2 a record at exactly the limit | L03 |
| M3 a record when the limit is 0 | L04[unlimited] |
| M4 the limit read outside the guard (an invalid setting makes `finish_run` raise) | L05 |
| M5 `over` reported as the whole spend | L01 |
| M6 spend read from the run data instead of the accounting column | L01 |
| M7 no hook in `request_cancel` (TA05-D01) | L07 |
| M8 no hook in `approvals._close` (TA05-D01) | L08 |
| M9 an earlier record kept: neither resume nor the helper removes it (TA05-D02) | L02 |

## Deviations (recorded, not hidden)

1. **M9 removes both layers.** Resume clears `token_limit`, and the terminal helper removes
   it again. Resume always runs before a resumed attempt reaches a terminal writer, so no
   reachable path brings an earlier record to the helper. A mutant of the helper's
   removal alone is therefore equivalent under current paths; it is not run or counted.
   The helper's removal stays as the design requires (defense in depth).
2. **The guard is wider than `ValueError`.** A legacy run whose document accounting copy
   is malformed (not a dict) makes `restored_tokens` raise `AttributeError`; the helper
   also catches `TypeError` and `AttributeError`, so unreadable accounting never stops a run
   from finishing (rule 5). Lease fencing and database errors are outside the guard.
3. **L07 and L08 are Store-level.** They use the TA02 fixtures (`claimed`, `snapshot`,
   `pause_request`) with a durable 110-token snapshot, as the design review's probes did,
   because these two writers run without a worker.

## Not claimed

The limit is not a strict cap: it does not bound one call's size, consumption hidden by
missing counts or HTTP retries, or concurrent branches (none exist yet; TA08, A5.3).
Reservation (input + `max_tokens`, then settle) and an output allowance remain options C
and B. The record compares with the limit in force at finish. The editor does not show
it yet. Old runs are not backfilled.
