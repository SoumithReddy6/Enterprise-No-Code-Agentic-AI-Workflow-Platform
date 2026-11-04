# A5.2 reserved-route recheck

Reviewed commit: `26c10ac67805e6ed55ade68db3a282ddc467e6c0`.
All eight earlier independent probes pass, including an additional assertion that
Replace generates a non-reserved `case_…` ID. The earlier malformed-rule repair paths
are fixed. One ordinary-deletion path still violates reserved-route ownership.

## E06 remaining path — P2: ordinary Remove still deletes Default

The new reserved-ID check is in `ownedId`, which is used by malformed-entry repair.
Ordinary `removeCase` still looks up case edges directly without that guard.

An imported case can have a forbidden ID but an otherwise well-formed comparison:

```json
{
  "id": "default",
  "label": "Imported case",
  "when": {"field": "category", "operator": "eq", "compare_to": "billing"}
}
```

`isSwitchCase` accepts the editing shape because its ID and label are strings and its
rule is an object. The case inspector consequently displays the ordinary case controls,
including Remove case. The reserved-ID prohibition still makes this an invalid workflow
configuration, but invalid drafts are intentionally editable.

Import that case alongside Technical and Account cases with an existing automatic
Default → `other` connection. Click the ordinary Remove button for the imported case.
The case disappears, and so does the automatic Default connection. Both direct helper
execution and an isolated browser reproduction confirm this.

### Root cause and fix

`removeCase` in `frontend/lib/switch.ts` still calls `caseEdges(edges, nodeId, caseId)`
without excluding reserved IDs. The inspector's ordinary connected-case label uses
the same unguarded lookup. The protected malformed-entry path is never reached here.

Apply reserved-route ownership to all case connection lookups/removals, including the
ordinary path, or classify forbidden IDs as malformed before choosing editor controls.
Either implementation can satisfy the invariant. Removing a case entry must never
delete the Switch's automatic Default connection. Retain the existing behavior for
ordinary valid IDs and Default-preserving reset.

Add a regression with `id: "default"` and a valid Condition-shaped `when` object, not
only `when: null`. The independent browser probe accepts either ordinary or malformed
Remove controls so it does not depend on which repair UI is chosen.

This remains an invalid-import editing defect. The backend rejects a missing Default
connection; no unauthorized send is demonstrated. No new routing feature is needed.

## Fresh checks

- 113 frontend unit tests passed.
- 13 committed Switch browser tests passed, including repair Undo behavior.
- All 8 prior independent probes passed.
- Typecheck and lint passed.
- One additional ordinary-Remove browser probe fails, reproducing the remaining path.
- Original evidence folders are unchanged, verified by SHA256.
- Application code was not changed by this review. Full build, full browser suite,
  backend suite and hosted CI were not repeated.

Evidence: `evals/results/a52-switch-editor-closure-26c10ac/` contains the eight-probe
success summary, the additional probe and its before/after connection data. Run the
remaining reproduction from `frontend/`:

```bash
npx playwright test --config ../evals/results/a52-switch-editor-closure-26c10ac/playwright.config.mts ordinary-remove.spec.ts
```

The expected result on this commit is one failed test because the existing Default edge
is missing after removal. The other eight probes need no further work based on this run.
