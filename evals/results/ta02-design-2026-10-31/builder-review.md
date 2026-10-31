# TA02 design builder check

Baseline: `f3e0923`. Reviewed the proposed specification and its current code seams.
An independent builder reviewer reported no blocking must-fix finding and judged the
selected atomic snapshot approach buildable without a per-model-call ledger.

The review checked:

- Snapshot freshness before normal, nested, pause and cancellation events.
- Preservation of a paid answer when the fast save fails but the outcome commit works.
- Explicit durable acknowledgment; silent lost-fence returns cannot count as success.
- Legacy trust staying separate from snapshot contents through the shared model boundary.
- The explicit limit at the provider-response/database-commit boundary.

Builder-resolvable details were incorporated into the design:

1. Put the contract marker in `run.data`, outside snapshot reconstruction.
2. Centralize capture at the root emitter and cover `Context.emit` as well as explicit emitters.
3. Acknowledge the frozen version actually committed; cancellation of a thread await does
   not prove rollback.
4. Make `AccountingUnavailable` a `ValueError`-derived control-flow error, preserving the
   Worker-visible explanation.

The one user policy question was asked and answered: block further model calls for older
interrupted runs whose past usage cannot be verified, while retaining their saved results.
The user also chose atomic result/accounting commits over a shared per-model-call ledger.
The complete written design still requires review before an implementation plan is prepared.
