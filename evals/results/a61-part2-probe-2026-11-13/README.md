# A6.1 Part 2 design probe: the editor with a Repeat, before any Part 2 change

Baseline: `10bf057`. Throwaway Playwright run from the scratchpad against the browser-test
Vite server (127.0.0.1:3317) with a mocked API. The `/api/nodes` catalog is
`frontend/tests/browser/request-nodes.json` (it already contains `repeat` and `for_each`). No
repository file was changed by the probe; the copies here are for the design review.

| File | Content |
|---|---|
| `gen_events.py`, `events.json` | A real two-iteration Repeat run (draft rejected, then accepted) recorded from the backend test helpers (`backend/tests/test_repeat.py`: `Script`, `standard`, `run_mem`): its workflow, 16 events and the Repeat summary. `seq` and `timestamp` are added for the mock |
| `probe.spec.ts`, `playwright.config.ts` | The probe. It imports that workflow, reads the palette, the Repeat card, the drawn edges, the inspector and the binding lists, runs it with the recorded events (first through iteration 1, then all), and drags the Repeat's handle to the Judge |
| `notes.json` | What it recorded (console messages deduplicated) |
| `canvas.png`, `inspector-repeat.png`, `events.png` | The canvas after import, the Repeat inspector, the execution log after the run (1600 × 1100) |

## Observed

| Question | Result |
|---|---|
| Repeat or For each in the palette | No. Control lists Condition, Join, Calculate, Switch, Request reader |
| Repeat card | Registry description; one target handle and one source handle, neither with an id. No `body` or `check` port |
| Loop edges from the import | 2 of 4 edges drawn. React Flow warns `Couldn't create edge for source handle id: "body"` (and `"check"`), error 008. The Writer and Judge float with no visible connection, so they cannot be seen as attached or deleted as edges |
| Inspector: Body, Check | Free text fields (`writer`, `judge`), not tied to the edges |
| Inspector: `until` | A one-line text input showing an empty value. The stop rule object is not shown, and typing would replace it with a string |
| Bindings after the Repeat (`out`) | Chat input → message, Repeat → text, feedback, summary. Correct (parity with the backend) |
| Bindings of the Writer and Judge | Empty, as for an attached callable. Correct |
| Drag the Repeat's handle to the Judge | A third edge is added. It can only be a normal flow edge, because the card has no loop port |
| Card status, run stopped after iteration 1's `loop_item` | Repeat **Info**, Writer Success, Judge Success. The `loop_item` event (status `info`) sets the card status |
| Card status, finished run | All Success |
| Execution log | `writer running`, `writer success`, … twice, with no iteration number; `r info` twice with no explanation |

Not probed (code reading): an imported For each draws its `body` edge the same way, because
the card code is the same (no `body` handle for any type). A new Repeat from the palette
would have no `until`, which `RepeatConfig` requires (no default), so Validate would refuse it
with `invalid configuration`.

## Design review check (rev 2, at `16c1f45`)

`observed_check.py` and `observed_check.txt`: the review's finding R3, checked again. A Repeat
`loop_item` passes through `storage.observed()` (the bounded copy that is stored and streamed,
`PREVIEW_CHARS` 2,000). With an escape-heavy 6,000-character draft and a 3,000-character note,
`item_result` becomes a string for both `met: true` and `met: false`, so `met` cannot be read.
With a 3,800-character draft the object is kept and only the draft is shortened. The design
(E7) shows "Verdict details are unavailable." in that case and never reads a missing `met` as
false.
