# Accounting refusal: no claim that saved results were kept (2026-11-12)

Baseline: `55a49f0`. Found in `storage-downgrade-check-2026-11-12` ("Observation, not changed").

## Defect

`accounting_state.INVALID` is the refusal for a run under the accounting contract whose
saved accounting is missing or malformed. It ended "Saved results were kept." After a schema
downgrade to 0001 and an upgrade back, revision 0003 has dropped `run_loop_items` together
with `runs.accounting`, so the run's loop item results are gone too. The sentence was false
there. The run still stopped safely; only the text was wrong.

## Fix

| File | Change |
|---|---|
| `backend/app/accounting_state.py` | `INVALID` now ends "This run cannot continue; start a new run." The refusal comes before any node runs, and the run's accounting is never repaired, so a resume refuses again: starting a new run is the only way on, in every case that raises it |
| `backend/tests/test_accounting_durability.py` | T11 pins the new text |
| `scripts/check_milestone_storage.py` | `populated_downgrade` also asserts that the refusal does not say "were kept" and does say "start a new run" |
| `docs/operations.md` | The accounting paragraph says the run cannot continue and why the message does not claim results were kept |

"No further model request was started." is unchanged: it is true in every case that raises it.
The other refusals (`LEGACY_MODEL`, `LEGACY_APPROVED_WRITE`, `PENDING`) are unchanged.

## Evidence

| File | What it shows |
|---|---|
| `baseline-red.txt` | `test_accounting_durability.py` with the new T11 on a clean `git archive 55a49f0`: 4 failed (T11 × 4 parameters), 20 passed |
| `suite/seed-*.txt` | Full backend suite in the isolation copy (`git archive 55a49f0` + the 4 changed files, one commit), PYTHONHASHSEED 0–4, PostgreSQL cases on a throwaway container on 55433: 2,141 passed at each seed |
| `ci-rows.txt`, `ci-rows-report.json` | The PostgreSQL CI job's eight storage rows in the isolation copy: 8 of 8 passed, `source_files_changed: []`. Its `head` is the isolation copy's throwaway commit |
| `fixed/unfinished.json` | The two unfinished runs after the downgrade round trip: `failed` with the new text |
| `mutant-N1-unit.txt`, `mutant-N1-storage.txt` | N1 puts "Saved results were kept." back, in a scratch copy: killed by T11 (4 failed) and by `populated-downgrade` |

An earlier suite run used `-p no:logging`, which removes pytest's `caplog` fixture, and gave 17
setup errors ("fixture 'caplog' not found"). It was discarded and the suite was run again
without that flag; the files here are from that second run.

## Not run

The deployed gate and the frontend checks: no frontend file changed, and no scenario script
compares this text.
