# TA06 fix evidence: calls with missing usage are counted, and incomplete totals are marked

Design (approved, P2 tier): `docs/superpowers/specs/2026-11-05-unreported-usage-design.md`.
Design review: `docs/reviews/ta06-design-review-2026-11-05.md` (TA06-D01 to D03, all
incorporated). Baseline application: `bcc8d9b`. No separate implementation plan: §6 of the
design is the case allocation, gate list and stop rule.

## What changed

| Area | Change |
|---|---|
| `providers.py` | `CallUsage`, a `dict` subclass that holds only token counts and keeps the call evidence in attributes (dispatched, undelivered, received, valid counts). The three chat transports use `build_request` + `_send` instead of `post`: dispatch is counted after request preparation, receipt right after the non-streaming send returns, and `ConnectError`/`ConnectTimeout`/`PoolTimeout` mark the attempt undelivered. `record_usage` keeps its token behavior and records how many counts were valid. A plain `dict` from a direct caller gets no evidence and is unchanged. |
| `registry.py` | `call_model` passes a `CallUsage` and marks a returned answer as received. `account_usage` counts every call with a status (reported, partial, unreported, unanswered), adds only valid counts, and adds `partial_calls`/`unreported_calls`/`unanswered_calls` only when non-zero. `calls` excludes unanswered calls. The `model.usage` journal line carries `usage_status`. |
| `operator_metrics.py`, migration `0006_usage_status` | Three `run_token_usage` columns (`Integer`, not null, server default 0), read by `usage_rows` and summed by the metrics query. The `tokens` definition states the lower-bound and pre-0006 meaning. Downgrade drops only the three columns. |
| Frontend | `usageLabel` in `lib/run-status.ts`; the event row shows `… (incomplete)`, `tokens not reported` or `tokens unknown (no response)` instead of `0+0 tok`. The event `usage` type gains the three optional counters. |
| Tests | New `backend/tests/test_usage_status.py` (15 cases) and `frontend/tests/usage-status.test.ts` (1 case): 16 in total, the design's cap. No existing test changed. |
| Docs | `docs/operations.md`: a **Call status** paragraph, the `tokens` metric row, the token-ceiling note and the remaining-limits paragraph. |

## Files

| File | Content |
|---|---|
| `baseline-bcc8d9b.txt` | The final new tests on a clean archive of `bcc8d9b`: 11 backend failed, 4 passed; the frontend case fails on the missing export. |
| `mutants.py`, `mutants.txt` | M1–M8 on fresh copies of the isolation base; all 8 planned kills. |
| `seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy: 2,002 passed at each seed (1,987 + 15). |
| `isolation.txt` | Clean-export proof (git archive `bcc8d9b` + the 11 TA06 files). |
| `production/report.json` | Deployed scenario gate: 24 of 24 rows passed, no source file changed during the run. |

Also run on the working tree: frontend `npm test` (115 passed, 114 + 1), `typecheck`,
`lint` and `build`. CI runs the browser and PostgreSQL jobs.

## Baseline (red) classification

All 10 defect failures are count mismatches on real Worker/Store runs; none is a missing
interface. The test reads a column the baseline schema lacks as 0, so it fails on values.

| Test | Baseline result | Fixed result |
|---|---|---|
| V01 three Ollama responses with no counts, one-token ceiling | 0 calls, 0 tokens (the call vanished); run succeeds | 3 calls, 3 unreported, 0 tokens; run still succeeds (soft ceiling) |
| V02[ollama-partial], V02[claude-partial] one count missing | 1 call, 0+7 tokens, looks complete | 1 call, 1 partial, 0+7 tokens |
| V02[openai-null] `usage: null` | 0 calls | 1 call, 1 unreported |
| V03[exhausted] 3 read timeouts | nothing recorded | 1 unanswered, 0 calls, existing error |
| V04 cancellation after dispatch | nothing on the cancelled outcome | 1 unanswered on `m1`'s cancelled event |
| V06[claude-usage-list, openai-usage-list, openai-http-401, ollama-invalid-json] | 0 calls, no usage on the failed event | 1 call, 1 unreported on the failed event, same error |

Controls passing on the baseline (4): V03[then-zero] (one call, zero is a reported count),
V05[invalid-url, connect-error, connect-timeout] (nothing reached a provider, nothing counted).
Interface controls written for the change (2): V07 (migration 0006) and F01 (`usageLabel`).
They fail on the baseline because the column and the export do not exist yet, as the design
allows, and are recorded as such.

## Mutants

| Mutant | Planned oracle |
|---|---|
| M1 the call vanishes when no count was recorded | V01, V02[openai-null], V06 |
| M2 a partial response is recorded as reported | V02 partial rows |
| M3 a reported zero is treated as missing | V03[then-zero] |
| M4 a dispatched call with no response is not counted | V03[exhausted], V04 |
| M5 an earlier timed-out attempt decides the status of an answered call | V03[then-zero] |
| M6 dispatch recorded before request preparation (TA06-D01) | V05[invalid-url] |
| M7 receipt recorded only after usage parsing (TA06-D02) | V06 |
| M8 metric rows drop the new counters | V01, V02, V06 |

The optional frontend mutant was not run; F01 covers the label.

## Deviations (recorded, not hidden)

1. **One test-setup defect fixed after the first baseline run.** V07 inserted a run with
   empty data. Store startup rebuilds `runs.created_at` from the run data, so the row moved
   to 1970 and fell outside the metrics window. The data now carries `created_at`. The
   recorded baseline is the final module on a clean archive; V07 still fails there at the
   column check, before the metrics query.
2. **Metrics API rows omit the three counters while zero.** The first full-suite run had 5
   existing failures, all from the same additive change: exact-equality assertions on
   `metrics()['tokens']` rows that now also had three zero fields. Applying design rule 4.3
   (omitted while zero) to the API rows too keeps complete usage byte-identical and makes the
   change additive. Result: **0 existing tests changed** (stop rule: 10).
3. **Answer-returning stubs.** Rule 4.2 counts a stub that returns an answer without usage
   as an unreported call. No existing test asserted the old absence, so none changed.
4. **More accounting saves.** `record_spend` now runs after every counted call, including
   unreported and failed-with-receipt calls, where it ran only when usage existed. This is
   one extra accounting write for such a call, on the existing persistence path.
5. **Journal shape.** `model.usage` adds `usage_status` and omits a missing token field
   instead of logging `None`. `test_observability.py` passes unchanged.
6. **Isolation includes the uncommitted design.** The design and its review were not yet
   committed when the copy was built, so the copy is `bcc8d9b` + 11 files, including the spec.

## Corrections after the implementation review

Review: `docs/reviews/ta06-implementation-review-2026-11-05.md` (evidence
`evals/results/ta06-implementation-review-0b7e8c7/`, not edited). Two P2 findings, both
corrected without a design round. No new collected case: the checks are added to existing
cases, so the total stays at 16.

| Finding | Correction | Test |
|---|---|---|
| TA06-I01: `observability.journal` filters fields through `FIELDS`, which lacked `usage_status`, so the emitted JSON never had it (deviation 5 above was only partly true) | `usage_status` added to `FIELDS`; any value other than the four generated statuses is dropped | V01 reads the real `relay.journal` records after the filter: three `model.usage` lines with `unreported` and no token fields, and a forged value is dropped |
| TA06-I02: `httpx.UnsupportedProtocol` (a base URL without `http://`, or `ftp://`) is raised by the transport before any connection, but counted as one unanswered call | Added to `UNDELIVERED` | V05[invalid-url] also runs `localhost:11434` and `ftp://127.0.0.1:9` through the real HTTPX client with `httpcore.AnyIOBackend.connect_tcp` blocked: no connection, no counter, no outcome usage |

Read and write timeouts stay unanswered; other HTTP errors are not excluded.

| File | Content |
|---|---|
| `corrections-red-0b7e8c7.txt` | The corrected test module on a clean archive of `0b7e8c7`: V01 and V05[invalid-url] fail (no status in the JSON; one phantom unanswered call), 13 pass. |
| `corrections-mutants.py`, `corrections-mutants.txt` | M9 (status dropped by the filter), M10 (status not restricted), M11 (`UnsupportedProtocol` counted): all killed by the planned case. |
| `corrections-isolation.txt` | Clean-export proof (git archive `0b7e8c7` + the 3 corrected files). |
| `corrections-seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4 in that copy: 2,002 passed at each seed. |
| `production-corrections/report.json` | Deployed scenario gate on the corrected tree: 24 of 24 rows passed, no source file changed during the run. |

The approved design names three connection-phase errors in §4.2; it is not edited. This
section and `docs/operations.md` record the fourth.

## Not claimed

Relay cannot detect a wrong count. `unanswered` is a client-side classification, and
`unreported` includes error-status responses. Provider-internal retries stay uncounted
separately; a timed-out attempt before a successful one is not shown. The token ceiling is
unchanged and soft: missing counts add nothing to it (TA05 decides strict handling). Old
runs and rows are not backfilled. Streaming, cache tokens (TA07) and concurrent settlement
(TA08) are out of scope.
