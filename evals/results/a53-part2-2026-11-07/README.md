# A5.3 Part 2 evidence: build parallel branches in the editor

Design (revision 2, approved by the design review for tests-first implementation):
`docs/superpowers/specs/2026-11-07-parallel-branches-editor-design.md`. Design probe:
`evals/results/a53-part2-probe-2026-11-07/`. Baseline: `4db70a7` (the Part 2 design commit;
application code identical to `main` `3e5ae46`). Editor only: no runtime, validation,
storage, accounting or API change.

## What changed

| Id | Change |
|---|---|
| E1 | `paletteCategory('join')` is `'Control'`; the Join card has the `GitMerge` icon. Adding it uses the existing `addNode`. |
| E2 | `joinArrivals(edges, join)` counts the distinct sources of normal flow edges into a Join (not error routes, attachments or repeated sources). The Join card shows "Waits for N branches" ("1 branch" for one). |
| E3 | Quick guide: how to fork after the Request reader and end every branch at one Join; the future-release note now names only team permissions. |
| E4 | `docs/operations.md` (**Parallel branches → In the editor**), `docs/platform-reference.md` (scope and roadmap), `README.md` (roadmap, counts). |
| E5 | `.workflow-card.cancelled`: dashed slate border; `.node-status.cancelled`: slate text. The label stays. |
| Tests | `frontend/tests/parallel.test.ts` (U01, U02), `frontend/tests/browser/parallel-branches.spec.ts` (B01–B05), `backend/tests/test_parallel_editor_contract.py` (K01) and its fixture `frontend/tests/browser/parallel-issues.json`. No existing test changed. |

## The shared fixture

`parallel-issues.json` holds the fork B01 builds and three broken forks with their real
`/api/validate` results. The `valid` graph was authored once by `author_valid_fork.ts`,
which applies the editor's own steps (the default Q&A draft, `loadDocument`'s model, `addNode`
defaults, `flowBinding`) with one authorized Ollama fixture model. K01 validates it against
the real endpoint with that model enabled and regenerates or checks the broken forks' issues
(`RELAY_REGENERATE_FIXTURES=1`). B01 builds the graph with real drags and requires the
submitted workflow to equal `valid` after mapping generated ids to labels, configs included.
So the editor's fork is the one the validator accepts.

## Files

| File | Content |
|---|---|
| `author_valid_fork.ts` | Authors the fixture's `valid` graph. |
| `baseline.py`, `baseline-4db70a7.txt` | The new tests on a clean archive of `4db70a7`. |
| `isolate.py`, `isolation.txt` | Clean-export proof: git archive `4db70a7` + the 11 changed files, one commit. |
| `frontend.txt` | Unit, typecheck, lint, build and every browser test in the isolation copy. |
| `seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4, PostgreSQL cases on a throwaway container. |
| `mutants.py`, `mutants.txt` | M21–M25 on fresh copies of the isolation base. |
| `production/report.json` | Deployed scenario gate. |

## Baseline (red) classification

| Case | On `4db70a7` |
|---|---|
| U01 | fails by value: `paletteCategory('join')` is `null` |
| U02 | fails: `joinArrivals` is not a function (a new helper; the design accepts this; B02 is the behavior oracle) |
| B01 | fails: no Join button in the palette (the drag waits until the timeout) |
| B02 | fails: the card shows the registry description |
| B03 | **passes (control)**: real fork messages are listed and mark exactly their nodes |
| B04 | fails: the cancelled card has a solid border (the cancelled label already shows) |
| B05 | fails: the guide has the old text |
| K01 | **passes (control)**: the API contract |

## Mutants

All 5 planned kills; every run collected 2 unit and 5 browser cases.

| Mutant | Planned oracle | Also failed |
|---|---|---|
| M21 Join not in the palette | B01 | U01 |
| M22 every incoming edge counts | U02 | |
| M23 the editor's `bindableOutputs` intersects at a Join | B01 | |
| M24 the guide keeps parallel execution as future work | B05 | |
| M25 no dashed border on a cancelled card | B04 | |

## Gate results

| Gate | Result |
|---|---|
| New cases on `4db70a7` | U01, U02, B01, B02, B04, B05 fail; B03 and K01 pass (controls) |
| New cases on the final tree | 2 unit, 5 browser, 1 backend: all pass. The browser spec passed 3 consecutive local runs before the lint fix, then once in the working tree and once in the isolation copy after it |
| Frontend, isolation copy `2456d1a` | 118 unit tests, typecheck, lint, build, 54 Chromium tests |
| Full backend suite, PYTHONHASHSEED 0–4 | 2,050 passed at every seed (2,049 + K01), PostgreSQL included. Run in the first isolation copy `4dcb9d9`, which differs from `2456d1a` only in the browser spec (two sort calls fixed for lint); no backend test reads it |
| Mutants M21–M25 | all planned kills |
| Deployed scenario gate | 24 of 24 rows passed; `source_files_changed` empty |
| Hosted CI | `db48e52`, run 42428260501: 5 of 5 jobs passed (backend 2,045 + 5 PostgreSQL-only skipped = 2,050 at two seeds; frontend 118 unit; browser-layout 54 Chromium) |

`README.md` counts were edited after the isolation copy was built; no test reads them.

## Deviations (recorded, not hidden)

1. **B01 clears the node selection before it deletes an edge.** The editor keeps node selection in
   its own state, and an edge click does not clear it, so Delete removed the selected Agent with
   the clicked edge (seen while writing B01). This is an existing editor defect, outside Part 2;
   it is filed as a separate task. B01 clicks the empty canvas first, as a user would.
2. **Tests use the canvas's fit-view control** before they measure positions, and drop new nodes
   on an empty part of the visible canvas, so drags reach the intended handles.
3. **B04 drives the run through the editor's recovery path**: the mocked event stream sends both
   `running` events and ends without `done`; the editor then polls the run, which the test
   switches from stage 1 (running) to stage 2 (failed and cancelled).

## Review correction (P3, `docs/reviews/a53-part2-implementation-review-2026-11-07.md`)

B01's graph comparison left out target handles, so an edge into the wrong port of the Join still
matched the fixture (the real validator rejects it: "flow must enter the input port"). `shape()`
now compares both ports of every edge. `m26.py`, `m26.txt`: mutant M26 serializes every edge into
a Join with `targetHandle: 'tools'` while the canvas still draws it. With the old spec B01 passes
(the gap); with the corrected spec B01 fails on the two `Join:tools` edges; without the mutant B01
passes. After the change: lint, typecheck and the five browser cases pass. Only the test file
changed, so no other gate was rerun.

## Not claimed

The editor does not check fork rules itself; Validate shows the backend's. No automatic binding
after a Join. No deployed-process scenario for forks yet (Part 3).
