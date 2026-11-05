# TA07 fix evidence: Claude cache input counts in the prompt total once; cache counts are a breakdown

Design (approved, P2 tier): `docs/superpowers/specs/2026-11-05-cache-input-tokens-design.md`.
Design review: `docs/reviews/ta07-design-review-2026-11-05.md` (TA07-D01, TA07-D02 and the
C01 ceiling extension, all incorporated in the design before the tests). Baseline:
`6c2ea5b` (the design draft; application = `dce6f66`). No separate implementation plan:
§6 of the design is the case allocation, gate list and stop rule.

## What changed

| Area | Change |
|---|---|
| `providers.py` | Claude `prompt_tokens` = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` (`_claude_input`). An absent or null cache part is 0; a present invalid one leaves the total a known subtotal and marks the input incomplete; with no valid part there is no total (no fabricated 0). OpenAI and Ollama totals are unchanged; `_cache_parts` keeps `cached_tokens` / `cache_write_tokens` / `prompt_eval_cached_count` as a breakdown only when every present detail is valid and the sum fits in the total. `record_usage` takes the breakdown and the completeness; `CallUsage.complete` makes a known-but-incomplete input `partial` (TA07-D01). |
| `registry.py` | `account_usage` adds `cache_read_tokens` / `cache_write_tokens` to the same totals as the counts (checkpoint owner, invocation, `by_model`), omitted while zero, and passes them to the `model.usage` journal line. |
| `observability.py` | The two breakdown fields pass the journal filter only as valid integer counts. `usage_status` passes only as one of the four status strings: `None`, a list or a dict is dropped and never raises (TA06 re-check note). |
| Tests | New `backend/tests/test_cache_input_tokens.py`: 10 cases, the design's cap. No existing test changed. |
| Docs | `docs/operations.md`: a **Prompt tokens and the cache breakdown** paragraph after **Call status**. |

Not changed, as designed: `token_budget.py` (it sums only prompt and completion, so the
breakdown is never counted twice), `operator_metrics.py` and migrations (SQL rows read fixed
fields), the frontend, runtimes, the compiler and the Worker.

## Files

| File | Content |
|---|---|
| `baseline-6c2ea5b.txt` | The final new tests on a clean archive of `6c2ea5b`: 6 failed, 4 passed. |
| `isolation.txt` | Clean-export proof (git archive `6c2ea5b` + the 6 TA07 files, hashes match the working tree). |
| `mutants.py`, `mutants.txt` | M1–M9 on fresh copies of the isolation base; all 9 planned kills. |
| `seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy: 2,012 passed at each seed (2,002 + 10). |
| `production/report.json` | Deployed scenario gate: 24 of 24 rows passed, no source file changed during the run. |

## Baseline (red) classification

All 6 failures are value mismatches on real Worker/Store runs or real journal output; none
is a missing interface.

| Test | Baseline result | Fixed result |
|---|---|---|
| C01 the finding's response (10 / 400 / 600 / 5), 1,000-token ceiling, two model nodes | 10 + 5 recorded; the second call is admitted | 1,010 + 5, `reported`, breakdown read 600 / write 400 on event, `by_model` and journal; `tokens_spent` 1,015; SQL row without breakdown; m2 refused by the typed budget error (`budget_exhausted`) before any second request |
| C03 Claude `cache_read_input_tokens: "600"`, creation 400 | 10 + 5, `reported` | 410 + 5, `partial`, write 400 only |
| C04[read-write], C04[read-only] OpenAI 100 + 50 with details | no breakdown | 100 + 50 unchanged; read 60 (and write 20); `read-only` keeps the read with no write key (TA07-D02) |
| C06 Ollama 6,024 with 6,016 cached | no breakdown | 6,024 unchanged; read 6,016 |
| C07 journal filter | `usage_status: null` kept (the first check to fail; a list would raise `TypeError`) | non-string statuses dropped; breakdown passes only as valid integers |

Controls passing on the baseline (4): C02[absent], C02[null] (no cache fields: 10 + 5,
`reported`), C05 (OpenAI cached 150 > prompt 100: no breakdown), C08 (Claude `usage: {}`
stays `unreported`; the TA07-D01 counterexample).

The design said C07 fails because a list raises `TypeError`; on the baseline it fails one
step earlier, because `None` is kept. Both are the same defect in the same check.

## Mutants

| Mutant | Planned oracle |
|---|---|
| M1 the Claude total is `input_tokens` only (the finding) | C01, C03 |
| M2 the breakdown is added to an OpenAI and an Ollama total | C04, C06 |
| M3 an absent or null Claude cache field is treated as invalid | C02 |
| M4 an invalid Claude cache field is treated as 0 and the call stays `reported` | C03 |
| M5 the subset check is removed | C05 |
| M6 `tokens_spent` sums the breakdown | C01, C04 |
| M7 the TA06 journal status check is reverted | C07 |
| M8 absent Claude defaults fabricate `prompt_tokens: 0` (TA07-D01) | C08 |
| M9 a missing OpenAI write detail drops the read (TA07-D02) | C04[read-only] |

The design budget is 9 mutants (7 planned in the draft, plus one for each review rule).

## Deviations (recorded, not hidden)

1. **C08 oracle.** Node totals always start with `prompt_tokens: 0`, so the absence of a
   fabricated count is proved by the `unreported` status, the SQL counters and the call's
   own `model.usage` journal line (no count fields), not by the event totals.
2. **`record_usage` signature.** Two optional arguments (`cache`, `input_complete`); a
   direct caller that passes neither gets the TA06 behavior unchanged.

## Not claimed

Relay still does not send `cache_control`, so live Claude calls have no cache tokens until
caching is enabled; this fix is for correctness when they appear. A cache read counts as
one token in the ceiling, though providers price it lower: tokens are not money. The Claude
TTL split, OpenAI reasoning/audio details and streaming are not recorded. Old runs keep
their old counts; no backfill. Metrics columns and a cache hit ratio (option B) are not in
scope. Strict reservation (TA05) and concurrent settlement (TA08) are unchanged.
