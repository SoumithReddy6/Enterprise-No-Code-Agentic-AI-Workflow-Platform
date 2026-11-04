# A5.2 Task 4 independent recheck

Reviewed commit: `8f1f44a21a73022f3bd1d9aa0517879c9d9abc0f`.
The four original findings E01-E04 pass requirement-based probes on this commit.
One follow-up remains in the malformed-case repair actions.

## Original findings and probe substitutions

- E01: importing `cases: [null]` and selecting the node no longer crashes. Passed.
- E02: compare each connection's start with the actual outer handle anchor in both x and
  y. A right-side handle uses its right edge, not its centre. Passed after reorder; the
  committed browser test also covers add, remove and Undo.
- E03: inspect the 16 route handles and labels regardless of whether they are on the
  bottom or right edge. Explicit count assertions prevent an empty selector from passing.
  No label or handle overlaps were measured. Passed.
- E04: scope the error locator because both an issue-list entry and its link contain the
  same message. Independently assert the Validate request count stays unchanged and Run
  makes no unexpected request. Passed.

The old probes encoded superseded layout and locator assumptions. The substitutions are
accepted; no implementation requirement depends on bottom-facing handles or unique
occurrence of an error message. Original evidence was not edited: SHA256 checks confirmed
every file in the original audit folder remained unchanged.

The two disclosed layout limitations are ordinary manual-canvas behavior within this
scope. Automatic node placement and automatic obstacle avoidance were not Task 4
requirements. They do not reopen the original crowding or stale-anchor findings.

## E05 — P2: repairing a malformed case can orphan its existing connection

The new repair code assumes a malformed entry has no route ID. That is false for a case
whose ID and label are valid but whose rule is malformed:

```json
{"id":"case_billng","label":"Billing","when":null}
```

Its workflow can still contain an edge with `sourceHandle: "case_billng"`. The case is
correctly shown as malformed, but its repair actions only change configuration:

1. **Remove** deletes the case and leaves its edge behind.
2. **Replace with an empty case** allocates a new ID and leaves the edge pointing to the
   old ID.

Both paths were reproduced in the actual editor. The resulting Validate payload contains
the old connection and no case with its handle. The backend will refuse the dangling
route; this finding does not demonstrate an unauthorized send. It breaks the promised
connection handling and leaves the draft with an additional problem after repair.

Locations:

- `frontend/lib/switch.ts:79-90`: `replaceEntry` and `removeEntry` change config only. The
  comment above `removeEntry` incorrectly says no connection can belong to a malformed entry.
- `frontend/components/switch-cases.tsx:66-70`: these actions use `onChange`, bypassing the
  editor's existing config-and-edge transaction for connected case removal.

Fix: inspect a malformed entry's available ID separately from whether its whole shape is
editable. For replacement, preserve a valid unique ID and its connections, or explicitly
update/remove affected connections. For removal, use the connected-case removal action
and wording. Change configuration and edges together so one Undo restores both. Do not
silently discard the entry or unrelated edges. Apply the same policy consistently to
other repair/reset actions that replace route IDs.

The two new browser probes assert no edge points at a removed/replaced case ID. They do
not require a particular repair strategy: preserving the ID, moving the connection to
the replacement, or explicitly removing it can satisfy the invariant.

## Fresh verification

- Frontend unit tests: 112 passed.
- Committed Switch browser tests: 8 passed.
- Backend example/registry and route-contract fixtures: 62 passed.
- Independent adapted original probes: 4 passed.
- New malformed-connected-case probes: 2 failed as expected, reproducing E05.
- `git diff --check`: clean; no application code changed.
- Full browser suite, full backend suite, build and hosted CI were not repeated here.

Evidence: `evals/results/a52-switch-editor-recheck-8f1f44a/` contains the six probe tests,
summary, repair payloads and measurements. Raw browser output is ignored for Git.

Run from `frontend/`:

```bash
npx playwright test --config ../evals/results/a52-switch-editor-recheck-8f1f44a/playwright.config.mts
```

On the reviewed commit, the expected result is four passes and two failures. Close E05
before declaring the editor task complete; the four earlier findings need no further
work based on this recheck.
