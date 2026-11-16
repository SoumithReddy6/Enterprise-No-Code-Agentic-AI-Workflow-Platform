# A6.1 Part 2 review fix (2026-11-16)

The implementation review of `7c4aa62` found two issues. This change fixes both. Baseline
for the red runs: `7c4aa62`. Control for the new browser test: `590ae89` (before Part 2).
Editor only: no runtime, validation, storage, accounting or API change.

## Findings

| Id | Finding | Fix |
|---|---|---|
| P2 | The loop port handling applied to every card. A Switch case id may be `body`, `check`, `flow` or `output` (pattern `^[A-Za-z][A-Za-z0-9_-]{0,63}$`; only `default` and `error` are reserved). A drag from such a case was refused (`body`, `check`) or saved with no handle (`flow`), and an imported `output` route was drawn at the card's first route | The mapping and the serialization read the source card type. `connectionKind` gives `loop` for `body`/`check` only on a loop card; `savedConnection(connection, sourceType)` drops `flow` only on a loop card; `edgePort` keeps a Switch handle as saved |
| P3 | Removal of a malformed loop edge (`sourceHandle: "on_limit"`, target `last`) cleared the Repeat's `on_limit` | Cleanup and attachment read one list of attachment ports: `body` on For each, `body` and `check` on Repeat. Any other handle removes the edge and changes no setting |

## Changes

| File | Change |
|---|---|
| `frontend/lib/workflow.ts` | `ATTACHMENT_PORTS`, `loopPorts`, `isAttachmentPort`; `connectionKind`, `edgePort`, `savedConnection` scoped to the source type; `withLoopAttachment` and `withoutLoopAttachments` set or clear attachment ports only |
| `frontend/components/workflow-editor.tsx` | `onConnect` finds the source card first and passes its type to `savedConnection`; the loop ports row reads `loopPorts` |
| `README.md` | Test counts (2,144 backend, 128 unit, 68 browser); the Browser row and the A6.1 Part 2 roadmap item name the fix |

## Tests (written first)

| File | Cases |
|---|---|
| `backend/tests/test_repeat_editor_contract.py` | K03: a Switch whose case ids are `body`, `check`, `flow` and `output` is valid through the real `/api/validate`, and the normalized response keeps every case id as its edge's source handle. Writes the fixture `frontend/tests/browser/switch-port-names.json` with `RELAY_REGENERATE_FIXTURES=1` |
| `frontend/tests/repeat.test.ts` | U09: the four case ids connect as `flow`, save unchanged, and `edgePort`/`canvasEdge` keep each one; loop cards keep their mapping. U10: removal of the `on_limit` → `last` loop edge leaves `on_limit` as `last`; a For each `check` edge clears nothing; a real body edge is still cleared; `withLoopAttachment` with port `max_iterations` sets nothing and still adds the edge |
| `frontend/tests/browser/repeat.spec.ts` | B08: import the K03 Switch; every `to_out_<id>` line starts at its own route port; export keeps the handles; delete the `body`, `check` and `flow` lines and draw them again from the case ports; the saved edges keep their case ids, have kind `flow`, and no React Flow 008 warning names them |

### Two existing assertions changed

- U02 asserted `connectionKind('agent', 'agent', 'body', null) === null`. That encodes P2: off a
  loop card, `body` is an ordinary handle name. The assertion is removed; U09 holds the new rule.
  An Agent card has no `body` handle, so no drag can make this connection.
- U07 called `savedConnection(connection)`. The function now takes the source type. The calls
  pass `repeat`, `for_each`, `repeat` and `request_reader`, and a For each `flow` case is added.

### Red on the baseline

| File | Result |
|---|---|
| `baseline-red-unit.txt` | `git archive 7c4aa62` + the new tests: U09 and U10 fail, U01–U08 pass |
| `baseline-red-browser.txt` | Same copy: B08 fails ("to_out_output starts at its source port"), B01–B07 pass |
| `baseline-k01-k03.txt` | K01, K02, K03 pass: the backend already accepts these case ids (control for the premise) |
| `control-590ae89-B08.txt` | `git archive 590ae89` + B08 and its fixture: B08 passes. The bug came with Part 2 |

### Test defect found and fixed while writing B08

The first layout put the response cards at y = 640, under the Switch card. A click on the
`to_out_check` line hit the card, so no edge was selected and the 590ae89 control failed. The
responses moved to y = 860, right of the Switch; the fixture was regenerated and the control passed.

## Mutants

`mutants/mutants.py` applies each change to a scratch copy of `frontend/` and runs only its
planned oracles. An oracle that selects no test stops the run. `M00` (no change) passes every
oracle: 10 unit tests, 8 Repeat browser tests and the 16 tests of `switch.spec.ts`.

| Mutant | Change | Killed by |
|---|---|---|
| M53 | `connectionKind` reads `body`/`check` as loop ports on every card | U09, B08 |
| M54 | `savedConnection` drops a `flow` handle on every card | U09, B08 |
| M55 | `edgePort` maps a Switch `output` case to the unnamed bottom port | U09, B08 |
| M56 | Removal clears any setting a loop edge names | U10 |
| M57 | Attaching sets any setting a loop edge names | U10 |
| M58 | The editor saves every new connection as if from a loop card | B08 |
| M59 | For each also treats `check` as an attachment | U10, U02 |

7 of 7 killed, each by every planned oracle (`summary.json`).

## Gates

| Gate | Result |
|---|---|
| Frontend unit (`frontend/unit.txt`) | 128 passed (126 + U09, U10) |
| Typecheck, lint, build (`frontend/`) | pass |
| Browser, all specs (`frontend/browser.txt`) | 68 passed (67 + B08) |
| Backend suite (`suite/seed-*.txt`) | 2,144 passed at PYTHONHASHSEED 0–4 (2,143 + K03), none skipped: the PostgreSQL cases ran on a throwaway container on 55433, in the isolation copy |
| Deployed scenario gate (`production/`) | 28 of 28, head = isolation commit `051b672`, `source_files_changed: []` |
| Isolation (`isolation.txt`) | `git archive 7c4aa62` + the 7 changed files; no other tracked file differs; hashes equal the working tree |

The frontend gates ran in the working tree. Its frontend files have the same hashes as the
isolation copy (`isolation.txt`).

## Not fixed (reported)

- A Switch case named `agents` is still refused: `connectionKind` reads `agents` as the Agent
  attachment port on every card. This is older than Part 2 (the same on `590ae89`) and outside
  the review findings.
- The review noted that the K02 fixture workspace has no model and no Jira connection, so the
  imported workflows are not runnable as they are. Not changed here; the browser tests serve
  the recorded responses.

## Not run

- Hosted CI: after the push.
