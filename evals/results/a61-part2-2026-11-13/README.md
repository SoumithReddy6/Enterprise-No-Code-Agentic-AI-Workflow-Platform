# A6.1 Part 2: build a Repeat in the editor (2026-11-13)

Design: `docs/superpowers/specs/2026-11-13-bounded-loops-editor-design.md`, revision 3,
approved for implementation (`590ae89`). Baseline for the red runs: `590ae89`. Editor only:
no runtime, validation, storage, accounting or API change.

## Changes

| File | Change (design id) |
|---|---|
| `frontend/lib/workflow.ts` | `paletteCategory('repeat')` → Control (E1); `newRepeatConfig` (E1); `connectionKind` returns `loop` for `body`/`check`, `flow` for `flow` (E3); `edgePort`, `canvasEdge`, `savedConnection` (E2); `portInUse`, `withLoopAttachment`, `removeElements` with the internal `withoutLoopAttachments` (E4); `unparsedJsonSettings` checks a Repeat's `until` with the Switch case helper (E10) |
| `frontend/lib/run-status.ts` | `nodeStatuses` (E6); `eventTitle`, Repeat notes in `eventNotes(event, workflow)` with the "Verdict details are unavailable." fallback (E7); `incompleteRun(run, workflow)` labels a Repeat `loop` cause (E8) |
| `frontend/components/condition-fields.tsx` (new), `switch-cases.tsx` | The Condition rule fields, moved out of the Switch case list unchanged and shared with the stop rule (E5) |
| `frontend/components/workflow-editor.tsx` | Repeat icon and new config; `body`/`check` ports and the named `flow` port on loop cards; "Up to N iterations"; edges drawn through `canvasEdge`, loop edges labelled by port; loop connections through `withLoopAttachment` with the occupied-port refusal; one `removeFromCanvas` for keyboard Delete (`onDelete`) and the inspector's Delete node; Body/Checker read-only, Stop rule section with helper lines; statuses, log titles and notes; quick guide paragraph (E2–E9) |
| `frontend/app/globals.css` | `.readonly-setting` |
| `README.md`, `docs/operations.md` | Roadmap (Part 1 done, Part 2 in review, Part 3 next), test counts, "In the editor" (E9) |

Names that differ from the design text: the design's `localProblems` is the existing
`unparsedJsonSettings`, and `withoutLoopAttachment` is the internal helper
`withoutLoopAttachments` used by `removeElements`. A `loop_item` row keeps the title of its
node (`r`); its iteration is in its note.

## Tests (written first)

| File | Cases |
|---|---|
| `backend/tests/test_repeat_editor_contract.py` | K01: the canonical B01 graph is valid via the real `/api/validate` (contract 2, fixture model), and four broken Repeats give the recorded located issues. K02: a fresh two-iteration run (through `storage.observed`, the stored and streamed copy) equals the fixture apart from `seq`, `timestamp`, `duration_ms`; the three B02 imports equal their recorded normalized `/api/validate` responses. `RELAY_REGENERATE_FIXTURES=1` rewrites both fixtures |
| `frontend/tests/browser/repeat-issues.json`, `repeat-run-events.json` | The K01 and K02 fixtures. The import files carry positions, because the normalized response places every node at (0, 0) otherwise |
| `frontend/tests/repeat.test.ts` | U01–U08 |
| `frontend/tests/browser/repeat.spec.ts` | B01–B07 |
| `frontend/tests/browser/switch.spec.ts` | One added assertion in the existing JSON-rule test: changing a case to `in` drops its `eq` operand. Added because the existing Switch tests did not kill M43 (below); it passes on `590ae89` (`baseline-switch-control.txt`) |

### Red on the baseline

| File | Result on `git archive 590ae89` + the new tests |
|---|---|
| `baseline-red-unit.txt` | 8 of 8 fail. U01, U02, U06, U08 by behavior; U03, U04, U05, U07 because the helper does not exist yet |
| `baseline-red-browser.txt` | 6 fail, 1 passes. B01: no Repeat in the palette; B02: 2 of 4 edges; B03: no read-only Body; B04: card class `info`; B06: old guide text; B07: no stop-rule field. B05 is the planned control (green) |
| `baseline-k01-k02.txt` | K01 and K02 pass: controls for the API contract and the event shapes |

### Test defects found and fixed while writing them

- B01 clicked the middle of `reader-agent`; that point is on the segment it shares with
  `reader-readerReply`, so the other edge was selected. The click is now near the Agent end.
- B02 counted every React Flow 008 warning. The default draft logs 008 for its reader routes
  while the catalog loads, on `590ae89` too. B02 now counts warnings for the imported edges only.
- B03(d) found no empty canvas area beside the inspector; it zooms out with the canvas control first.
- B03 changed `eq` to `gte` on a rule with no `compare_as`, so no stale operand could be
  dropped and M43 survived it. B03 now sets Compare as `number` first.

## Mutants

`mutants/mutants.py` applies each change to a scratch copy of `frontend/` and runs only its
planned oracles; an oracle that selects no test stops the run. `M00` is the control (no change):
every oracle passes. `summary.json` has the per-oracle result; `M*.txt` the output.

| Mutant | Change | Killed by |
|---|---|---|
| M34 | Repeat not in the palette | U01, B01 |
| M35 | No `check` handle on the card | B02, B01 |
| M36 | `body` connects as `flow` | U02, B01 |
| M37 | A loop connection does not set `body` | U03, B01 |
| M38 | Removal clears a setting that names another node | U03 |
| M39 | `nodeStatuses` keeps `loop_item` | U04, B04 |
| M40 | Iterations counted from 0 | U05, B04 |
| M41 | `limit` read as `rule` | U05 |
| M42 | `incompleteRun` ignores the node type | U06 |
| M43 | `ConditionFields` loses `withConditionOperator` (Switch too) | B03, `switch.spec.ts` |
| M44 | A new Repeat has no `until` | B01 |
| M45 | `targetHandle: 'input'` kept on the canvas | U07, B02 |
| M46 | The loop card's flow port has no id | B02 |
| M47 | Keyboard Delete through the old separate handlers | B03 |
| M48 | Occupied-port refusal removed from `onConnect` | B03 |
| M49 | A missing `met` read as false | U05 |
| M50 | `until` not checked for unfinished JSON | U08, B07 |
| M51 | `output` not mapped to `flow` | U07, B02 |
| M52 | `portInUse`/`removeElements` read the raw source handle | U03 |

19 of 19 killed, each by every planned oracle. `first-run-M43-invalid.txt`: the first mutant
run is discarded. Its browser filter (`-g '^(B01 )'`) selected no test, and its `switch.spec.ts`
"kill" was a missing `examples/` folder in the scratch copy. Both were corrected (filter,
no-test guard, folder copied, control M00 added) and every mutant was run again.

## Gates

| Gate | Result |
|---|---|
| Frontend unit (`frontend/unit.txt`) | 126 passed (118 + 8) |
| Typecheck, lint, build (`frontend/`) | pass |
| Browser, all specs (`frontend/browser.txt`) | 67 passed (60 + 7) |
| Backend suite (`suite/seed-*.txt`) | 2,143 passed at PYTHONHASHSEED 0–4 (2,141 + K01, K02), PostgreSQL cases on a throwaway container on 55433, in the isolation copy |
| Deployed scenario gate (`production/`) | 28 of 28, head = isolation commit, `source_files_changed: []` |
| Isolation (`isolation.txt`) | `git archive 590ae89` + the 14 changed files; no other tracked file differs; hashes equal the working tree |

`screens/`: the imported Repeat (`canvas.png`), its inspector (`inspector-repeat.png`), the card
while iteration 2 runs (`running.png`) and the log after the run (`events.png`), from
`screens/shots.spec.ts` with the K02 events. Seen there and not changed (the same on
`590ae89`): an Agent card's `tools` label overlaps its model name.

## Not run

- Hosted CI: after the push.
- A live model as the checker: the fixtures use a fixture model (Part 3 runs Repeat deployed).
