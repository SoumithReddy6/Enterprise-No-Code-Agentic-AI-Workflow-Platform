# A6.1 Part 1: fix for the recheck of `936e6c8` (2026-11-11)

Baseline: `750adde` (`936e6c8` + the React 19.2.9 upgrade; no backend change). The recheck
confirmed the three earlier findings fixed and found one more.

## Finding and fix

| Finding | Cause | Fix |
|---|---|---|
| **P2.** With the Repeat bound to `retrieve.query` after the guarded retrieval abstained, the Repeat called the writer and the checker and published a generated answer. A standalone Agent with the same binding abstained with zero model calls | The compiler's upstream check (`guarded_dependency`, applied through `guard_refusal`) covers only `llm`, `agent` and `query` nodes. The Repeat dispatches its models itself, and `retrieve.query` carries no evidence envelope for the Agent to honour | The compiler hands the same check to the Repeat (`Context.upstream_abstained`). When it is set and the loop would call a model (an Agent or Query body, or any checker), the handler makes no call and saves the refusal as the existing guard row `{index, status: 'abstained', value}`, so a resume and a checkpoint restore return it without a call. A tool body without a checker runs, as a standalone tool does |

## What changed

| File | Change |
|---|---|
| `backend/app/registry.py` | `Context.upstream_abstained` (default `False`; set only by the compiler) |
| `backend/app/compiler.py` | For a Repeat, `node_ctx` gets `upstream_abstained` from the same `guarded_dependency` walk as `guard_refusal` |
| `backend/app/repeat.py` | `MODEL_TYPES`; before an iteration, the upstream refusal replaces the calls when the loop would call a model |
| `backend/tests/test_repeat.py` | 5 tests: R27g (with a standalone-Agent control), R27h, R27i (control), R27j × 2 |
| `docs/operations.md` | The guard bullet: the upstream check inside the Repeat |
| `README.md` | Test count 2,127 → 2,132 |

## Evidence

| File | What it shows |
|---|---|
| `baseline-red.txt` | The final `test_repeat.py` on a clean `git archive 936e6c8`: 4 failed (R27g: the writer was called; R27h: the tool was called; R27j × 2: the writer was dispatched, failed as an unscripted call (`r: iteration 1: Iteration call failed.`), and no guard row was saved), 78 passed (including the R27i control) |
| `mutants.py`, `mutants.json` | O1–O5, each in a fresh scratch copy against its oracle tests: 5 of 5 killed. O1 restores the `936e6c8` behaviour |
| `isolate.py`, `isolation.txt` | Clean export: `git archive 750adde` + the 6 changed files, one commit (`ec1d48e`); hashes match the working tree; no other tracked file differs |
| `suite/seed-*.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy, PostgreSQL cases included (throwaway container on 55433) |
| `storage-rows.json` | `sqlite-repeat-sigkill` and `postgres-repeat-sigkill` in the isolation copy |
| `production/report.json` | Deployed scenario gate in the isolation copy |

## Gate results

| Gate | Result |
|---|---|
| New tests on `936e6c8` | 4 red for the reported reason; R27i control green |
| `test_repeat.py` on the change | 82 passed |
| Mutants | 5 of 5 killed (first run: O1 deleted the whole statement and left an empty block, and O5 skipped `record_progress`, which does not persist the row; both mutants were corrected, not the tests) |
| Full backend suite | 2,132 passed at each of PYTHONHASHSEED 0–4 (2,050 + 82) |
| Storage rows | `sqlite-repeat-sigkill`, `postgres-repeat-sigkill` passed (head `ec1d48e`, `source_files_changed` empty) |
| Deployed scenario gate | 28 of 28 (head `ec1d48e`, `source_files_changed` empty) |
| Frontend | Not run again: no frontend file and no public registry field changed (`Context` is runtime only; the backend suite checks `request-nodes.json`). The frontend at `750adde` passed 118 unit, typecheck, lint, build, 54 Chromium (`evals/results/react-19.2.9-2026-11-11/`) |

## Decisions

1. **A tool body before a checker does not run** under an upstream abstention. Its result could only reach the checker, which refuses, so the call would be paid for nothing.
2. **A tool body without a checker runs** (R27i). That is the standalone behaviour: the compiler's check does not refuse tool nodes. Its text is not a model answer, and a downstream model node still refuses, because the guard walk follows the Repeat's input.
3. **The refusal text** is `immediate_abstention(GUARD_DECISION)`, the same text a standalone Agent returns (R27g compares them).
