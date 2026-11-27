# A6.1 Part 3: deployed scenarios for Repeat, and the approval rule (2026-11-16)

Design: `docs/superpowers/specs/2026-11-16-bounded-loops-scenarios-design.md`, revision 2
(`b1c2bb5`), approved for implementation with three details (I1–I3, now in the design's
"Recheck details"). Probe: `evals/results/a61-part3-probe-2026-11-16/`.
Baseline for the red runs: `b1c2bb5`. Its backend, scripts and frontend equal `8705b7f`; it
differs only in the design document.

## Change

| File | Change |
|---|---|
| `backend/app/compiler.py` | `after_repeat` finds the flow steps that can run after a Repeat: everything reachable from its edges except its own `error` route, with a visited set. `repeat_errors` (now given the flow graph) refuses, for each Repeat with `on_limit: last`, every write among those steps and their attachment closure (tools, tools behind specialists, For each bodies) that does not have the literal `approval: true` |
| `scripts/scenarios/repeat_loops.py` (new) | `LoopModels` (the worker's Ollama endpoint, keyed by model name, with held answers) and rows S25–S29 |
| `scripts/check_production_scenarios.py` | A third lab with the model fixture and `RELAY_RUN_TOKEN_LIMIT=100`, after the Switch lab. The two other labs keep their environment |
| `docs/operations.md` | "Bounded loops (Repeat)": the approval rule, its reason, and where a reviewer finds the stop reason (the execution log; the approval card shows only the outgoing request) |
| `docs/production-scenario-testing.md` | Rows S25–S29; S01–S29 use no stubs; the third lab's realism note |
| `README.md`, `docs/platform-reference.md` | A6.1 Part 3 done (A6.1 complete), A6.2 next; S28 in §2.2; S01–S29; the counts |
| Design document | The recheck details I1–I3: G7's expected failure, V-A6's list input, the error-route walk, V-A12, V-A13, G14, G15; the V-A file name |

The refusal, shown at Validate and at run submission (422):

```
send: a write after r needs approval: true, because r can return an answer that did not meet its stop rule (on_limit: last).
```

## Tests (written first)

| File | Tests |
|---|---|
| `backend/tests/test_repeat_write_approval.py` (new) | V-A1–V-A13: refused shapes (a direct write with approval false or unset, a write two steps later, a POST attached to a later Agent, behind a specialist, in a later For each body over a Jira list, on a later step's error route), accepted shapes (approval true on each, `on_limit: fail`, a write before the Repeat, a write only on the Repeat's own error route), a cycle after the Repeat that must terminate (a thread with a 10 s limit), and submission through the API |
| `scripts/scenarios/repeat_loops.py` | S25, S26a, S26b (three parts), S26c, S27, S28, S29, with the asserts of the design |

### Red on the baseline (`baseline/`)

`git archive b1c2bb5` + the three test and gate files, committed in a throwaway repository as `9d78956`.

| Check | Result |
|---|---|
| V-A (`baseline-red-va.txt`) | 9 failed (V-A1–V-A6, V-A11, V-A12, V-A13): each refused shape is accepted with no error. 4 passed (V-A7–V-A10): the control, the valid shapes are already valid |
| Gate (`report.json`, `gate-console.txt`) | 34 of 35. S26b fails at part 1: `/api/validate` accepts the write without approval (`valid: true`). S25, S26a, S26c, S27, S28 and S29 pass: the behavior exists (control). S26b parts 2 and 3 were not reached on the baseline, because part 1 fails first; the rev 1 review ran both fallbacks with approval on `8705b7f` |

## Mutants (`mutants/`)

`mutants.py` applies one exact replacement to a fresh `git archive` of the isolation commit for
each mutant (the mutation point must occur once). Oracles: `va` (every V-A result recorded) and
`lab` (`repeat_lab.py`: the Repeat lab of the gate, every row recorded). An oracle that selects
nothing stops the run. M00 (no change) passes all 13 V-A tests and all 7 rows.

| Id | Mutant | Planned oracle | Killed by (every failing oracle) |
|---|---|---|---|
| G1 | The next body input is the Repeat input only | S25 | S25 |
| G2 | The stop rule reads the body output | S25 | S25, S26a, S26b, S27, S28, S29 (`the value is not JSON`) |
| G3 | The first iteration always counts as met | S25, S26a | S25, S26a, S26b, S27, S28, S29 |
| G4 | `on_limit: fail` returns the last answer at the limit | S26a | S26a |
| G5 | The in-loop write rule is removed | S26c | S26c |
| G6 | A checker stopped by the budget counts as met | S27 | S26b, S27 |
| G7 | Resume ignores saved rows | S28 | S28: row 0 is overwritten with draft 2 (I1: the call counts are unchanged) |
| G8 | A replacement claims 25 s before the lease ends | S28 | S28 (`claimed_before_lease_end_seconds: 24.977`) |
| G9 | The resume does not restore a Repeat checkpoint (rebuilt from rows) | S29 | S29: no `cached: true` event for `r` |
| G10 | The approval rule is removed | S26b, V-A1 | S26b, V-A1–V-A6, V-A11–V-A13 |
| G11 | The rule walks flow steps only | V-A4–V-A6 | V-A4, V-A5, V-A6 |
| G12 | The rule accepts an unset approval | V-A2 | V-A2 |
| G13 | The rule also walks the Repeat's own error route | V-A10 | V-A10 |
| G14 | The rule skips every error edge | V-A12 | V-A12 |
| G15 | The walk has no visited set | V-A13 | V-A13 (validation does not finish) |

15 of 15 killed by their planned oracle (`summary.json`, `results/<id>/`).

## Gates

| Gate | Result |
|---|---|
| Deployed scenario gate, 3 consecutive runs (`production-1..3/`) | 35 of 35 each, head = isolation commit `921cedc`, `source_files_changed: []`. About 135 s per run; S28 takes 30.3 s (the real lease). The replacement worker's claim came 0.055 s, 0.097 s and 0.026 s after the killed worker's `lease_until`, by another worker |
| Backend suite (`suite/seed-*.txt`) | 2,157 passed at PYTHONHASHSEED 0–4 (2,144 + 13 V-A), none skipped: the PostgreSQL cases ran on a throwaway container on 55433, in the isolation copy |
| Frontend unit (`frontend/unit.txt`) | 128 passed |
| Typecheck, lint, build (`frontend/`) | pass |
| Browser, all specs (`frontend/browser.txt`) | 68 passed. The recorded validator fixtures (K01–K03) did not change: `test_repeat_editor_contract.py` passes without regeneration |
| Isolation (`isolation.txt`) | `git archive b1c2bb5` + the changed files; every code, test and doc file has the working tree's hash, except `README.md`, whose three counts were updated after the gates |

The frontend gates ran in the working tree. The frontend has no change.

## Not claimed

- A checker's judgment quality: the models are a fixture.
- PostgreSQL in the gate (it is SQLite); `postgres-repeat-sigkill` covers a Repeat crash on PostgreSQL.
- The approval card does not show the Repeat's stop reason; `docs/operations.md` says where to find it.
- A truncated For each batch before a write (S09 shape) is not changed.

## Not run

- Hosted CI: after the push.
