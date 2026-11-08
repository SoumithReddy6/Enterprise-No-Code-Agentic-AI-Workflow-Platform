# A5.3 Part 2 design probe: the editor with a fork, before any Part 2 change

Baseline: `3e5ae46` (`main` after PR #7). Throwaway Playwright run from the scratchpad
against the browser-test Vite server (127.0.0.1:3317) with a mocked API. The `/api/nodes`
catalog is `frontend/tests/browser/request-nodes.json` (it already contains `join`). No
repository file was changed by the probe; the copies here are for the design review.

| File | Content |
|---|---|
| `probe.spec.ts`, `playwright.config.ts` | The probe. It imports a workflow `input → a, b → join → out`, reads the palette, the Join card, the edges and the binding lists, then drags two edges. |
| `notes.json` | What it recorded. |
| `canvas.png` | The canvas after the import (1440 × 900). |

## Observed

| Question | Result |
|---|---|
| Join in the palette | No. The Control group lists Condition, Calculate, Switch and Request reader. |
| Fork drawn from an import | Yes: 5 edges drawn; the input's one bottom handle carries both branch edges. |
| Join card | Generic icon and the registry description; 1 target handle, 1 source handle. No branch count. |
| Bindings after the Join (`out`) | Chat input → message, and every output of Branch A and Branch B (Part 1 parity). |
| Bindings inside a branch (`b`) | Chat input → message only; no sibling output. |
| Drag a duplicate edge (`a → join` again) | Ignored by React Flow: 5 edges stay. |
| Drag a new edge (`b → out`) | Added: 6 edges. Real handle drags work in this harness. |

Not probed: run statuses with concurrent branches (code reading: statuses come from the
latest event per node, and a cancelled sibling emits `cancelled`, `compiler.py` node
wrapper), and validation issue display (code reading: every branch error is prefixed with a
node id, so `/api/validate` returns located issues).
