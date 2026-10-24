# A5.2 E05 recheck: fixed ordinary ownership, remaining Default ownership bug

Reviewed frontend commit: `14c70873f28ced06ab103bd98a0528b257438e5a`.
The original E05 Remove and Replace reproductions pass. All six prior recheck probes pass
after adapting only the connected Remove button's locator to its requested wording.
Original audit folders are unchanged, verified by SHA256.

## E06 — P2: a malformed entry cannot own the automatic Default connection

The new ownership helper treats any unique string ID as belonging to the malformed case,
including `default`. The Switch always has an automatic Default route independently of
its case entries. A malformed entry claiming that reserved identifier does not acquire
ownership of its connection.

Reproduction: import a draft with these cases and an existing Default → General Support
connection:

```json
[
  {"id":"default","label":"Broken imported case","when":null},
  {"id":"technical","label":"Technical","when":{"operator":"eq","compare_to":"technical"}}
]
```

- **Replace with an empty case:** creates a fresh case ID, then moves the automatic
  Default connection to that ID. The Default route loses its connection.
- **Remove case and its connection:** deletes the automatic Default connection along
  with the malformed entry.

Both operations were reproduced in the actual browser and directly through the helpers.
The browser probes compare the Default edge's ID, source handle and destination before
and after repair. They allow harmless serialization fields such as `kind: "flow"`.

This leaves an invalid fallback connection and makes validation refuse the repaired
workflow until the author reconnects it. No unauthorized write was demonstrated.

### Cause and bounded fix

- `frontend/lib/switch.ts:84-86`: `ownedId` does not exclude the automatic reserved route.
- `replaceEntry` refuses to keep `default` as a case ID, but then retargets its edge as
  though it belonged to the malformed entry (`113-114`).
- `removeEntry` uses the same ownership result and therefore deletes its edge (`125`).

Exclude automatic reserved routes from case-entry ownership. Repairing a malformed
`default` entry should create a fresh case while leaving the existing Default edge
untouched; removing the entry should also leave that edge untouched. The case-removal
button should not claim to remove a connection that belongs to the automatic route.
Keep the current handling for ordinary IDs and shared valid-case IDs.

The added unit test currently groups `bad id` and `default` together and expects both
connections to move. Separate those assertions: an ordinary invalid ID is not the same
as the Switch's automatic fallback route. Preserve Default across both actions and Undo.
Reset already preserves Default and should retain that behavior.

## Fresh verification

- Frontend unit tests: 113 passed.
- Committed Switch browser tests: 11 passed.
- Six earlier independent recheck probes: 6 passed.
- Two additional Default-preservation probes: both fail as expected, reproducing E06.
- `git diff --check`: clean.
- No application files were changed by this review. An unrelated in-progress change to
  `backend/requirements.txt` appeared during the review and was left untouched.
- Full browser suite, build, backend suite and hosted CI were not rerun here.

Evidence: `evals/results/a52-switch-editor-e05-recheck-14c7087/` contains probe source,
summary and before/after Default edges. Original evidence remains unchanged. Run from
`frontend/`:

```bash
npx playwright test --config ../evals/results/a52-switch-editor-e05-recheck-14c7087/playwright.config.mts
```

The expected result on the reviewed commit is six passes and two failures. The earlier
E01-E05 scenarios need no further work based on this recheck; close the reserved-Default
ownership follow-up before declaring Task 4 complete.
