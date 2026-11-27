# A6.1 Part 1: fixes for the implementation review of `011a9e0` (2026-11-11)

Baseline: `011a9e0` (A6.1 Part 1, the Repeat node). The review found three defects and
reproduced each one with a probe. This change fixes them. Design: revision 5 (`c630aed`); the
two design gaps that the fixes close are listed under "Deviations".

## Findings and fixes

| Finding | Cause | Fix |
|---|---|---|
| **P1.** On resume, a budget stop under `on_limit: last` returned the restored draft without a second source check. A source removed after the halt row committed reached the downstream model; only the Response refused, too late | The handler checked restored sources once (`verified`), before the next body. The return path skipped the check when it had already run | `repeat_node` checks the restored owners again before **every** return, also after a later body ran (`backend/app/repeat.py`) |
| **P2.** A writer honoured an upstream answerability abstention. The Repeat treated the refusal as a draft, called the checker, and the next writer (whose input wraps the envelope, so it does not see the decision) generated a draft that was published | The Repeat did not read the guard decision | A body or checker output that is a guard abstention (`answerability_guard.from_outputs`, or an Agent's grounding with `abstention_source: answerability_guard`) ends the Repeat at once with a saved row `{index, status: 'abstained', value}`. `text` is the refusal; `summary` has `stopped_by: guard` and `answerability: {decision: abstain, abstention_source: answerability_guard}`. `from_outputs` reads that summary, so a downstream LLM, Agent or Query node refuses without a model call |
| **P3.** A Retrieve body's result was lost: Retrieve returns `context`, the Repeat read `text` and used `''`. With an `empty` rule, the empty draft met the rule | `answer.get('text', '')` | `result_of` reads the declared result port (`context` for Retrieve, `text` for every other body type) and fails the Repeat when it is missing: `r: iteration 1: the body's context output is missing.` |

## What changed

| File | Change |
|---|---|
| `backend/app/repeat.py` | Source check before every return; `ABSTAIN_KEYS`, `GUARD_DECISION`, `guard_abstained`; the abstained row in `checked_rows` (keys exact, last row only, `value` a string) and in `repeat_outcome`; `RESULT_PORTS` and `result_of`; the 200,000-byte entry limit applies to an abstained row too |
| `backend/app/answerability_guard.py` | `from_outputs` also reads `summary.answerability` (only For each and Repeat have a `summary` port, and the runtime writes both) |
| `backend/tests/test_repeat.py` | 12 tests: R17d, R27a–f, R28a–b |
| `docs/operations.md` | "Bounded loops (Repeat)": result port, guard stop, `stopped_by: guard`, the return check |
| `README.md` | Test count 2,115 → 2,127 |

## Evidence

| File | What it shows |
|---|---|
| `baseline-red.txt` | The final `test_repeat.py` on a clean `git archive 011a9e0`: 12 failed (the new tests), 65 passed (Part 1). Each failure is the reviewed defect: R17d fails with the Response's refusal (`out: …`), not the Repeat's; R27a calls the checker; R27b–c fail on the judged refusal; R27d–e call the checker after the abstention; R28a fails by the limit on the empty draft; R28b returns no error |
| `mutants.py`, `mutants.json` | N1–N15, each in a fresh scratch copy against its oracle tests: 15 of 15 killed. N1 puts back the `011a9e0` return check and N15 the `''` default |
| `isolate.py`, `isolation.txt` | Clean export: `git archive 011a9e0` + the 5 changed files, one commit (`8982e79`); hashes match the working tree; no other tracked file differs |
| `suite/seed-*.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy, PostgreSQL cases included (throwaway container on 55433) |
| `storage-rows.json` | `sqlite-repeat-sigkill` and `postgres-repeat-sigkill` in the isolation copy |
| `production/report.json` | Deployed scenario gate in the isolation copy |
| `frontend.txt` | Frontend checks (no frontend file changed; the node registry did not change, so `request-nodes.json` is unchanged) |

## Gate results

| Gate | Result |
|---|---|
| New tests on `011a9e0` | 12 red for the reviewed reasons; 65 Part 1 tests green |
| `test_repeat.py` on the change | 77 passed |
| Mutants | 15 of 15 killed |
| Full backend suite | 2,127 passed at each of PYTHONHASHSEED 0–4 (2,050 + 77) |
| Storage rows | `sqlite-repeat-sigkill`, `postgres-repeat-sigkill` passed (head `8982e79`, `source_files_changed` empty): row 0 reused, writer 3 requests, checker 2, tokens 30 then 60 — the same as Part 1 |
| Deployed scenario gate | 28 of 28 (head `8982e79`, `source_files_changed` empty) |
| Frontend | 118 unit, typecheck, lint, build, 54 Chromium |

## Deviations from the design text, and why

1. **A fourth row kind and a fourth `stopped_by`.** Revision 5 defines fully evaluated rows and budget stops only, and `stopped_by` `rule | limit | budget`. A guard stop is neither: it is a complete answer (a refusal), so it is not truncated and adds no run truncation cause, and it must replay exactly on resume without a call. It gets its own row, `{index, status: 'abstained', value}`, and `stopped_by: guard`. `iterations` still counts fully evaluated rows only, so a guard stop at iteration 1 reports 0.
2. **A checker abstention returns the checker's refusal.** The draft was not checked, so it is never returned. The design has no checker abstention case.
3. **The result port.** The design says "the body output"; it is now defined as the declared result port: `context` for Retrieve, `text` for every other allowed body type (all tools, Query and Agent declare `text`).
4. **Part 1 mutant M29** (`011a9e0` evidence) edited the old return check, which no longer exists. N1 and N2 replace it. The Part 1 evidence folder is not changed.

## Not claimed

- A tool body (HTTP, Python, …) that receives an abstained envelope from upstream does not honour it, exactly as outside a loop; the Repeat stops only when the body or the checker returns an abstention. A downstream model node still refuses, because the guard walk follows the Repeat's input.
- For each has the same `text` read for a Retrieve body (`backend/app/iteration.py`). That is outside this change; a separate task is proposed.
- Editor support, run-view iterations and deployed scenarios (Parts 2 and 3).
