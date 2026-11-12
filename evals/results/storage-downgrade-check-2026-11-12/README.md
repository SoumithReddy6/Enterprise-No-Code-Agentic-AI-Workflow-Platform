# Storage: the populated-downgrade check asserts the documented behaviour (2026-11-12)

Baseline: `35ba222`. Found in A6.1 Part 1 (`storage-baseline-populated-downgrade.json`, head `c1df8cd`).

## Defect

`scripts/check_milestone_storage.py` `populated_downgrade` asserted that a downgrade to
`0001_baseline` and a re-upgrade keep a run's loop item results and accounting. Revision
`0003_loop_storage` drops `run_loop_items` and `runs.accounting` on downgrade by design
("Nothing is copied"), and `docs/operations.md` says "Downgrades are tested, not promised to
be lossless". So the check failed on every head, and the docs called it a known-failing
diagnostic. `README.md` §2.12 also claimed the round trip compared populated loop results
and accounting, as though they were kept.

The real question is not "is the data kept" (it is not) but "is the loss silent". The check
did not ask that.

## What a downgrade does to a populated run (`probe/`)

A scratch probe on `35ba222` (throwaway PostgreSQL on 55433) put two unfinished runs through the
round trip, then resumed them:

| Run | After the round trip | On resume |
|---|---|---|
| Read loop, SIGKILL after item 0 committed, item 1 in flight | 0 progress rows, accounting `null` | `failed`: `Saved token accounting is missing or malformed. No further model request was started. Saved results were kept.` Item 0 sent once; no new request |
| Write batch, item 0 written, paused at item 1's approval | 0 progress rows | Approve item 1 → `failed` with the same refusal; 1 write in all |

The Part 1 comparison (`baseline-comparison.json`) shows the finished run: its results become the
marker `Loop each cannot be restored: 2 of 2 stored item results are missing. Start a new run.`,
not an empty list.

So the product fails closed. Only the check and the README were wrong.

## Fix

| File | Change |
|---|---|
| `scripts/check_milestone_storage.py` | `populated_downgrade` now runs three populated runs (finished loop; loop SIGKILLed after its first item; write batch paused at its second approval) through one downgrade to 0001 and back. It asserts: the finished run keeps its status and output, its results are the `unavailable` marker with the exact reason, its accounting is gone; each unfinished run fails on resume with `accounting_state.INVALID`; no request reaches the receiver after the downgrade; 1 write and 2 reads in all |
| `.github/workflows/ci.yml` | `populated-downgrade` added to the PostgreSQL job's check list |
| `README.md` | §2.12: revisions 0001–0006 (was 0001–0005); the evidence sentence says what the round trip proves |
| `docs/browser-layout-testing.md` | The check is described as passing and is in the supported list |
| `docs/operations.md` | The downgrade paragraph says the loss is visible, not silent |

No backend application file changed.

## Evidence

| File | What it shows |
|---|---|
| `baseline.txt`, `baseline-comparison.json` | The old check on `35ba222`: failed, `Downgrade/re-upgrade lost persisted loop results` |
| `fixed.txt`, `fixed/` | The new check: passed in 37.9 s. `finished.json`: before/after results and accounting. `unfinished.json`: both runs `failed` with the refusal. `receiver.json`: every request the receiver saw |
| `mutants.py`, `mutants.json` | Each mutant in a fresh scratch copy of the isolation tree, against the new check: 3 of 4 killed (see below) |
| `ci-rows.txt`, `ci-rows-report.json` | The CI job's eight storage rows in the isolation copy (`git archive 35ba222` + the changed files): 8 of 8 passed, `source_files_changed: []`. Its `head` is the isolation copy's throwaway commit, not a repository commit |
| `probe/` | The scratch probe and its output (table above) |

## Mutants

| Mutant | Result |
|---|---|
| M1 missing loop rows hydrate as an empty successful loop (`storage._hydrate_loop`) | Killed: the finished run's results are `[]` |
| M2 a contract run with no accounting is treated as an older run (`accounting_trusted`) | Killed: the paused run fails with the older-run refusal, not `INVALID` |
| M3 `accounting_trusted` gives missing accounting a zero baseline | **Survived**. Not equivalent: `Store._set_accounting` checks the saved snapshot again and refuses with the same `INVALID` text, so the run still fails before it sends anything. Two independent guards; M3 removes one |
| M3b both guards give missing accounting a zero baseline | Killed: the killed run reads item 0 again and succeeds; the paused run asks for a new approval |

A first M4 ("the 0003 downgrade keeps `runs.accounting`") was dropped: it fails because the
re-upgrade adds a column that already exists, which says nothing about this check.

## Observation, not changed

The refusal text ends "Saved results were kept." After a downgrade, the loop item results were
not kept. The message is still fail-closed; its last sentence is wrong for this case only.

## Not run

No backend application file changed, so the backend suite at PYTHONHASHSEED 0–4, the deployed
gate and the frontend checks were not run.
