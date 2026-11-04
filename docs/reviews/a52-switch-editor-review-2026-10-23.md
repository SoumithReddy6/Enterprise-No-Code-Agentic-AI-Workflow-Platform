# A5.2 Task 4 editor review

Reviewed commit: `2ac224410d57263b3c05175e0bd878dc1a44f9ee`.
No application code was changed. Browser checks used the dedicated Vite test server on
127.0.0.1:3317 and intercepted API calls. The real Relay services and workspace were not used.

## E01 — P2: opening a malformed Switch draft crashes the editor

`frontend/lib/switch.ts:21-22` casts every array to `SwitchCase[]` without checking its
entries. A draft with `config.cases: [null]` survives Workflow document parsing, and
backend validation correctly returns a located configuration error. Invalid drafts of
known node types are intentionally importable for repair.

Import that draft and select the Switch. `unreachableCases` dereferences `c.when` at
`switch.ts:96`. The browser displays "This page couldn't load" and the runtime error
"Cannot read properties of null (reading 'when')". The author cannot repair the node
through the case inspector. The browser reproduction supplies `valid: false` on import;
this is not dependent on a validation service accepting the malformed case.

Fix: validate the case-entry shape before the inspector, duplicate detector and label
helpers dereference it. Keep malformed draft data visible and repairable, using a safe
fallback if needed. Do not silently discard malformed entries or reject every invalid
draft. Test null and other malformed entries through import/open and selection.

## E02 — P2: reordering preserves saved IDs but leaves drawn connections at stale positions

`frontend/components/workflow-editor.tsx:201-208` changes handle positions when case order
changes, but the node component does not refresh React Flow's cached handle measurements.
The installed React Flow provides `useUpdateNodeInternals` for forced remeasurement.

Browser reproduction: import the connected three-case workflow, then move Account above
Technical. Before the move the Technical edge's SVG start point meets the visible
Technical handle to within a tiny fraction of a pixel. After the move the line keeps its
old path while the handle moves. In the first measured run the horizontal mismatch was
14.43 screen pixels and remained after waiting for rendering.

The saved edge still refers to the correct case ID. The bug is that the displayed diagram
no longer accurately represents that connection. A user reviewing or editing a production
workflow can be misled about which case owns a line.

Fix: refresh node internals after route IDs/order/count or layout changes. Add browser
assertions comparing the actual SVG endpoints with the matching DOM handles after reorder,
add/remove and Undo. Comparing saved JSON alone does not catch this.

## E03 — P2: the supported 15-case canvas layout overlaps connection targets

The same rendering code squeezes all case handles and Default into 45% of a fixed 238px
card. With 15 cases there are 16 handles, about 7.14 unscaled pixels apart, while the
handles are wider. Every label is rendered on the same horizontal line.

At a 1440x1000 viewport, the browser measured 105 overlapping label pairs and 15 overlapping
handle pairs using short labels `Department 01` through `Department 15`. The screenshot
shows an unreadable cluster. This is not just a long-label edge case: the advertised
maximum makes the connection hit targets themselves overlap.

Fix: allocate sufficient layout space per route, for example rows with a growing card.
Retain support for all 15 cases and Default. Test nonoverlapping handles and readable
labels at the maximum count, including longer labels, on the canvas itself.

## E04 — P3: invalid JSON inside a case bypasses the existing local validation guard

`frontend/lib/workflow.ts:398-415` inspects only top-level node configuration properties.
The new Options field is nested at `cases[i].when.options`, so it is not inspected.

Browser reproduction: choose `in`, enter `["billing"]`, then edit it to `["urgent",` and
press Validate. The field correctly displays its JSON error and retains the visible text,
but a validation request is still sent containing the invalid string. The request count
increased from 2 to 3. A direct call to `unparsedJsonSettings` returns no errors for this
shape. The equivalent top-level Condition editor blocks locally.

The real backend still rejects the invalid list. This is not a stale-value execution or
an authorization bypass. The browser probe's accepting validation fixture demonstrates
the missing local guard only; it does not establish that the real backend accepts it.

Fix: include nested Switch predicate JSON settings in local validation, with a located
case/field error. Test that Validate and Run make no API requests while Options is invalid.

## Fresh verification and evidence

- All 110 frontend unit tests passed.
- All 4 existing Switch browser tests passed.
- Backend `test_request_examples.py`: 29 passed, including registry fixture conformance.
- Four additional browser probes reproduce the issues above (expected failures).
- `git diff --check`: clean.
- Hosted CI, full backend suite, build and full browser suite were not repeated here.

The existing Switch browser tests inspect `/api/validate` payloads. Their "saves" wording
does not mean they exercised a persisted Save/reload round trip, and the connected-case
test imports its connections rather than drawing them. Extend coverage where needed;
the saved-ID assertions are useful but do not establish visual connection correctness.

Audit files: `evals/results/a52-switch-editor-review-2026-10-23/` contains a portable
Playwright config, probe source, compact summary and measurements. Raw logs, browser
reports and screenshots are ignored for Git. Run from `frontend/`:

```bash
npx playwright test --config ../evals/results/a52-switch-editor-review-2026-10-23/playwright.config.mts
```

Exit 1 with four failed probes is expected on the reviewed commit. The probes should pass
after the respective fixes. Screenshots and error context remain in `browser-output/`.

## Plan adjustments

Using the whole registry fixture instead of another Switch-only file is reasonable and
its conformance test passes. Exact-equality duplicate warnings are a conservative,
nonblocking scope reduction; they intentionally miss some semantically equivalent rules.
Neither adjustment causes the four findings.

Close E01-E03 before declaring Task 4 complete. E04 is smaller but belongs in this editor
follow-up. No routing authorization bypass was demonstrated by these reproductions.
