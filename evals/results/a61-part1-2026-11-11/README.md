# A6.1 Part 1: the Repeat node, backend (2026-11-11)

Design: `docs/superpowers/specs/2026-11-09-bounded-loops-design.md`, revision 5 (`c630aed`,
approved for Part 1). Baseline: `c630aed`. Part 1 is backend only: node, validation, runtime,
persistence, accounting, tests and storage rows. The editor (Part 2) and the deployed
scenarios (Part 3) are not in this change.

## What changed

| File | Change |
|---|---|
| `backend/app/repeat.py` (new) | `RepeatConfig`; the handler; `repeat_outcome` (one function derives the outcome fields from the rows, on the fresh path and on restore); row checks; the typed verdict check; the point-of-use source checks; `restored_outputs` for a checkpointed Repeat |
| `backend/app/registry.py` | Registers `repeat` ("Repeat until", Control): input `input`; outputs `text`, `feedback`, `summary` |
| `backend/app/platform_graph.py` | Loop edges may start at a Repeat (`body` or `check` handle); exactly one body; at most one checker, an Agent; configured IDs must match; nesting refused in all three directions |
| `backend/app/compiler.py` | New cycle message; Repeat settings errors shown as written; `repeat_errors` (no write or memory Agent in the loop closure; typed checker rule); the restore branch checks a checkpointed Repeat after `validate_cached` |
| `backend/app/execution_policy.py` | `ExecutionIdentity.child_base` (default `None`) |
| `backend/app/agent_runtime.py` | A root Agent's child IDs use `child_base` when set; every truncation record gets `kind` (`tokens`, `action`, `steps`, `tool`) |
| `backend/app/platform_validation.py` | `kb_errors` treats `r:<i>` as a reused loop owner |
| `backend/app/worker.py` | `run_truncation`: one `loop` cause from a Repeat's summary; a recovered budget failure (only `_truncation`) keeps one `budget` cause |
| `backend/tests/test_repeat.py` (new) | 65 tests (V01–V12, R01–R26 and sub-cases) |
| `backend/tests/test_token_budget_recovery.py` | Three truncation-record assertions gain `'kind': 'tokens'` |
| `frontend/tests/browser/request-nodes.json` | The browser tests' copy of the node registry, regenerated (+155 lines, none removed). No frontend source change |
| `scripts/check_milestone_storage.py` | Rows `sqlite-repeat-sigkill` and `postgres-repeat-sigkill` |
| `README.md`, `docs/operations.md`, `docs/platform-reference.md` | Roadmap (A6 → A6.1 / A6.2), test count, a "Bounded loops (Repeat)" section |

## Evidence

| File | What it shows |
|---|---|
| `baseline-red.txt` | The final `test_repeat.py` on `c630aed` (scratch copy): 64 failed, 1 passed. The failures are `Unknown node type: repeat`, `Loop connections must start at a For each node.` and invalid `r.*` bindings. The one pass is R10b, a control: For-each child IDs must not change |
| `mutants.py`, `mutants.json` | M1–M33, each in a fresh scratch copy of the final tree, each against its oracle tests: 33 of 33 killed. Run: `.venv/bin/python evals/results/a61-part1-2026-11-11/mutants.py <scratch dir>` |
| `storage-rows.json` | The two SIGKILL rows in the isolation copy (`87f5273`), on SQLite and on a throwaway PostgreSQL (port 55433, via `MILESTONE_POSTGRES_URL`): both passed, `source_files_changed` empty |
| `storage-all.json`, `storage-baseline-populated-downgrade.json` | Every storage row in the isolation copy: 13 of 15 passed, including all existing loop, lease, accounting and parallel rows. The two failures are not from this change: `copied-workspace` reads the operator's own `.data/workflows.db`, which the isolation copy does not have (it was not run against the real workspace); `populated-downgrade` fails the same way on the unchanged baseline `c630aed` (downgrading to `0001_baseline` drops `run_loop_items`, so a For-each checkpoint that references its rows cannot be restored) |
| `isolate.py`, `isolation.txt` | Clean-export proof: `git archive c630aed` + the 15 changed files, one commit (`87f5273`); hashes match the working tree; no other tracked file differs |
| `production/report.json` | Deployed scenario gate in the isolation copy (`87f5273`): 28 of 28, `source_files_changed` empty |
| `suite/seed-*.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy (`87f5273`), PostgreSQL cases included (throwaway container on 55433, via `RELAY_TEST_POSTGRES_URL`) |
| `frontend.txt` | 118 unit tests, typecheck, lint, build, 54 Chromium tests |

## Gate results

| Gate | Result |
|---|---|
| New tests on the baseline | 64 red for the expected reason, 1 control green (R10b) |
| New tests on the change | 65 passed |
| Mutants | 33 of 33 killed |
| Storage rows | `sqlite-repeat-sigkill`, `postgres-repeat-sigkill` passed: row 0 saved before the kill and reused, only iteration 1 repeated, writer 3 requests (1 answered + 1 killed + 1 resumed), checker 2, tokens 30 before and 60 after (each answered request charged once) |
| Full backend suite | 2,115 passed at each of PYTHONHASHSEED 0–4 (2,050 + 65) |
| Frontend | 118 unit, typecheck, lint, build, 54 Chromium: unchanged results |
| Deployed scenario gate | 28 of 28 |

## Deviations from the design text, and why

1. **R08, R19b and R21a (the `calls: 0` replay) use a tool body without a checker.** Agents are not action-bearing (`execution_policy.charges_action`), and an Agent that starts with no action left makes no model call: it returns the no-evidence abstention marked truncated, now with `kind: action`. So "row 0 fully evaluated, then the action budget refuses row 1's body" cannot happen with an Agent checker after a tool body that used the last action. The tests as first written assumed it could; they failed on the implementation for that reason, and the test was wrong, not the code. **R08b** (new) covers the checker case: the checker stopped by the action budget is not judged; row 1 is interrupted with `calls: 2`, and `last` returns row 0's draft.
2. **M10 is reinterpreted** as "the body tool call is not charged" (oracle R08). The design's "the checker is not charged" has no effect, because the checker is an Agent and Agents are never charged as actions.
3. **`calls`** counts the body and checker invocations of the iteration that ran: an invocation that returned an output, or that made at least one attempt before a budget refused it. A call refused before its first attempt does not count. Range 0–2 (0–1 without a checker); the restore check refuses other values.
4. **Messages the design did not spell out:** `r: a checker's stop rule cannot use the empty operator.`; `Loop connections from a Repeat must leave the body or check port.` (a Repeat loop edge needs an explicit handle, because the handle says which attachment is the checker); and the handler's own guard `r has no attached body to run.` (validation normally prevents it).
5. **Truncation kind of a specialist.** A specialist that stops without evidence fails with `Nested agent budget exhausted without evidence.`, whose cause is not carried in the error. Its record gets `kind: steps`, so a Repeat whose body reports it fails loudly as incomplete instead of saving a budget stop. A specialist refused by a run-wide budget keeps the budget's kind.
6. **Provenance.** All three Repeat ports are labelled `guessed` (the registry default for a node without a rule), which is never stronger than the body output. The static label is guessed too, so a Switch on `r.text` is refused (R16).
7. **Entry size.** Without a checker the saved `verdict` is the body output, as the design states, so the 200,000-byte limit counts the text twice for a body-only Repeat.
8. **The Repeat is not in the editor palette.** `paletteCategory` (`frontend/lib/workflow.ts`) lists types by an allow-list without `repeat`; Part 2 adds it. A Repeat can be built through the API or JSON import.
9. **Test harness corrections before green:** `crash_on` stacked its wrappers, so an earlier crash rule fired again on resume (it now replaces the previous rule); R16 now requires the Switch refusal to be the only error, because on the baseline the unknown `repeat` type also produced it.
10. **Oracles strengthened for the mutants:** R02 runs three iterations, so "previous is the first draft" (M3) is visible; R17 and R18 assert that the pre-run check refused before the graph started, because the later point-of-use checks would otherwise hide M13 with the same message.

## Not claimed

- Editor support, run-view iterations and deployed scenarios (Parts 2 and 3).
- Writes, memory Agents, approvals or waits inside a loop; nested loops.
- Exact replay of the budget kind when the attempt stops before the interrupted row commits (R21b asserts only the guaranteed part, as the design states).
- A response lost before any durable save is paid again (the accepted TA02 limit).
