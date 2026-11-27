# For each: read the body's declared result port (2026-11-12)

Baseline: `00281fb` (A6.1 Part 1 closed). Found during the A6.1 Part 1 review (finding P3
was the same defect in Repeat, fixed in `936e6c8`).

## Defect

Validation accepts a Retrieve node as a For each body. Retrieve declares `query`, `context`
and `sources`, and no `text`. For each built every item as `value: output.get('text', '')`,
so each Retrieve item was saved as `""` with `status: success`. The run reported success and
the retrieved evidence was gone. The item's label was read from `text` too, so it was always
`guessed`.

## Fix

| File | Change |
|---|---|
| `backend/app/iteration.py` | `RESULT_PORTS` / `result_port` (moved here from `repeat.py`: one rule for both loops); a fresh item reads its value and label from the result port; a body without that port fails its item (`lookup: the body's context output is missing.`, `on_item_error` applies); `check_restored_item` refuses a Retrieve item saved as `""` by an earlier version, unless the size contract dropped it (`value_dropped`) |
| `backend/app/compiler.py` | The For each restore branch applies `check_restored_item` to a checkpointed loop's results |
| `backend/app/repeat.py` | `result_of` uses `iteration.result_port`; no behaviour change |
| `backend/tests/test_for_each_result_port.py` (new) | 9 tests: F01–F06 |
| `docs/operations.md` | "Loop result and event size bounds": the result port, the missing-port failure, the restore refusal |
| `README.md` | Test count 2,132 → 2,141 |

## Evidence

| File | What it shows |
|---|---|
| `baseline-red.txt` | The new test file on a clean `git archive 00281fb`: 6 failed, 3 passed. F01: the value is `""` (not JSON); F02: the label is `guessed`; F03 × 2: the item is `success` with `""`; F04 × 2: a blank Retrieve item resumes as success. The passes are the controls F05 × 2 (a dropped value restores) and F06 (an empty `text` result restores) |
| `mutants.py`, `mutants.json` | P1–P7, each in a fresh scratch copy against its oracle tests: 7 of 7 killed. P2 restores the `00281fb` behaviour (`''` default, no failure) |
| `isolate.py`, `isolation.txt` | Clean export: `git archive 00281fb` + the 6 changed files, one commit (`69cfe06`); hashes match the working tree; no other tracked file differs |
| `suite/seed-*.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy, PostgreSQL cases included (throwaway container on 55433) |
| `storage-rows.json` | 13 storage rows in the isolation copy, including the For each row `postgres-sigkill-loop` |
| `production/report.json` | Deployed scenario gate in the isolation copy |

## Gate results

| Gate | Result |
|---|---|
| New tests on `00281fb` | 6 red for the reported reason; 3 controls green |
| New tests, `test_iteration.py`, `test_repeat.py` on the change | 106 passed |
| Mutants | 7 of 7 killed |
| Full backend suite | 2,141 passed at each of PYTHONHASHSEED 0–4 (2,132 + 9) |
| Storage rows | 13 of 13 passed, `postgres-sigkill-loop` included (head `69cfe06`, `source_files_changed` empty) |
| Deployed scenario gate | 28 of 28 (head `69cfe06`, `source_files_changed` empty) |
| Frontend | Not run: no frontend file and no registry field changed |

## Test notes

- A For each checkpoint does not hold its results: it refers to its item rows by count and SHA-256 digest (`storage._hydrate_loop`). F04/F05 build "what an earlier version saved" by changing the row and recomputing the checkpoint digest; without the new digest, storage refuses the checkpoint as tampered before the new check is reached.
- F06 is the control for P7: only a Retrieve item is a lost result. A `text` body may return `""` legitimately.

## Not claimed

- Storage rows `copied-workspace` (reads the operator's own workspace; not run against it) and `populated-downgrade` (fails the same way on the baseline; separate task) were not run.
- A Retrieve item value is the whole evidence envelope; the size contract (1,000,000 bytes retained) applies to it as to any value.
