# Editor: Delete acts only on the last selection (2026-11-12)

Baseline: `0423b66`. Found in A5.3 Part 2: B01 (`tests/browser/parallel-branches.spec.ts`)
cleared the node selection before it deleted an edge, to work around this defect.

## Defect

The editor keeps one selected node in its own state (`selected`; the Inspector shows it) and
forces React Flow's `selected` flag from it in `renderedNodes`. React Flow's node `select`
changes are filtered out in `onNodesChange`. Two ways then left a node and an edge selected
together, and Delete (or Backspace) removed both:

1. **Click an edge while a node is selected.** React Flow tries to deselect the node, the
   editor ignores the change, and the node stays selected. The editor starts with `agent`
   selected, so this happened on the first edge click with no other action (D02).
2. **The editor selects a node itself while an edge is selected** (a palette drop, an issue
   link). React Flow deselects edges only on its own clicks, so the edge stayed selected (D05).

## Fix (`frontend/components/workflow-editor.tsx`)

| Change | Effect |
|---|---|
| `onEdgesChange`: an edge `select` change with `selected: true` calls `select('')` | Selecting an edge clears the editor's node selection, as a pane click does |
| `select(id)` replaces every `setSelected(...)` call (9 sites) | A node selection also deselects every edge, for a click, a drop or an issue link |

The first version cleared edges in a `useEffect` on `selected`; oxlint's React Compiler rule
(`EffectSetState`) refused it, so the clear happens in the event path instead.

## Evidence

| File | What it shows |
|---|---|
| `baseline-red.txt` | `tests/browser/edge-delete.spec.ts` on a clean `git archive 0423b66 frontend`: 4 failed (D01 × 2: the node stays selected after the edge click; D02: Delete removes the `agent` node; D05: the edge stays selected after the drop), 2 passed (controls D03: Delete still removes a selected node; D04: clicking a node after an edge selects that node) |
| `mutants.py`, `mutants.json` | Q1–Q4, each in a fresh scratch copy of `frontend/` (node_modules linked) against its oracle tests: 4 of 4 killed. Q1 restores the `0423b66` behaviour |
| `frontend.txt` | 118 unit, typecheck, lint, build, audit gate (passed; 4 moderate = the accepted residual vulnerability), 60 Chromium (54 + 6) |

## Mutants not kept

- "Clear the node selection on any edge `select` change, a deselect too": no observable difference. React Flow emits the edge deselect in the same click that selects the next node, and the editor's own node selection is set after it.
- "`select('')` also clears the edges": no observable difference. In `onEdgesChange` the clear is queued before React Flow's own selection change, which is applied after it.

Both are equivalent in every flow the editor has; they were removed rather than counted.

## Other changes

- `tests/browser/parallel-branches.spec.ts`: the B01 comment no longer says Delete removes a selected node; the pane click is kept.
- `README.md`: 54 → 60 Chromium tests.
- No backend file changed; the backend suite, storage rows and deployed gate were not run.
