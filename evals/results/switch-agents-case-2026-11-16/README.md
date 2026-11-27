# Switch case named `agents` (2026-11-16)

Reported as open after the A6.1 Part 2 review fix (`evals/results/a61-part2-review-fix-2026-11-16/`).
Baseline for the red runs: `05aca4a`. The isolation copy is on `5ed24f8`, which differs from
`05aca4a` in `README.md` and one review document only. Editor only: no runtime, validation,
storage, accounting or API change.

## Defect

A Switch case id may be `agents` (pattern `^[A-Za-z][A-Za-z0-9_-]{0,63}$`; only `default` and
`error` are reserved), and the backend accepts its route edge: only an Agent-to-Agent edge
(`kind: agent`) reads the `agents` handle (`backend/app/platform_graph.py`). The editor's
`connectionKind` read `agents` as the Agent card's specialists port on every card, so a drag from
a Switch case named `agents` to any node was refused. An imported `agents` route was drawn at
its own port. The defect is older than A6.1 Part 2 (the same code on `590ae89`). It is the same
kind of defect as review finding P2: a port name that is special on one card, read on every card.

## Change

| File | Change |
|---|---|
| `frontend/lib/workflow.ts` | `connectionKind` reads `agents` as the specialists port only when the source is an Agent card. On other cards it connects as `flow` |
| `README.md` | The A6.1 Part 2 roadmap item: a port name is special only on its own card; `agents` added to the case names |

## Tests (written first)

No new test: the three tests of the review fix are extended with a fifth case, so one Switch
covers every editor port name. The test counts do not change.

| File | Change |
|---|---|
| `backend/tests/test_repeat_editor_contract.py` | K03: case `agents` (value `sales`) before `output`; response `out_agents`, the later responses moved right; the test name says "editor ports". The fixture `frontend/tests/browser/switch-port-names.json` is regenerated |
| `frontend/tests/repeat.test.ts` | U09: `agents` in the case list; each case also connects as `flow` to an Agent. The Agent card keeps its port: `agent` → `agent` through `agents` is `agent`, `agent` → `response` through `agents` is refused |
| `frontend/tests/browser/repeat.spec.ts` | B08: `agents` imported and checked at its own port, then deleted and drawn again with `body`, `check` and `flow` |

### Red on the baseline

| File | Result on `git archive 05aca4a` + the changed tests |
|---|---|
| `baseline-red-unit.txt` | U09 fails ("agents connects"); U01–U08 and U10 pass |
| `baseline-red-browser.txt` | B08 fails at the `agents` drag: 8 edges, expected 9. The import, the line positions and the export pass first. B01–B07 pass |
| `baseline-k01-k03.txt` | K01–K03 pass: the backend accepts the case (control for the premise) |

## Mutants

`mutants/mutants.py` is the runner of the review fix with these mutants. `M00` (no change) passes
every oracle: 10 unit tests, 8 Repeat browser tests and the 16 tests of `switch.spec.ts`. An
oracle that selects no test stops the run.

| Mutant | Change | Killed by |
|---|---|---|
| A1 | `agents` is the specialists port on every card (the code before the fix) | U09, B08 |
| A2 | The Agent card's specialists port connects as `flow` | U09 |
| A3 | The Agent card's specialists port connects to any target | U09 |

3 of 3 killed, each by every planned oracle (`summary.json`).

## Gates

| Gate | Result |
|---|---|
| Frontend unit (`frontend/unit.txt`) | 128 passed |
| Typecheck, lint, build (`frontend/`) | pass |
| Browser, all specs (`frontend/browser.txt`) | 68 passed |
| Backend suite (`suite/seed-*.txt`) | 2,144 passed at PYTHONHASHSEED 0–4, none skipped: the PostgreSQL cases ran on a throwaway container on 55433, in the isolation copy |
| Deployed scenario gate (`production/`) | 28 of 28, head = isolation commit `4f292ff`, `source_files_changed: []` |
| Isolation (`isolation.txt`) | `git archive 5ed24f8` + the 6 changed files; no other tracked file differs; hashes equal the working tree |

The frontend gates ran in the working tree. Its changed files have the same hashes as the
isolation copy.

## Not changed

- The K02 fixture workspace still has no model and no Jira connection. The browser tests serve
  the recorded responses.

## Not run

- Hosted CI: after the push.
