# TA05 implementation review — 2026-11-06

**Commit:** `51a8318bffc0998b5973d9d6dc7e8bb3dce1d8b6`.
**Design:** `docs/superpowers/specs/2026-11-05-token-admission-limit-design.md`.
**Evidence:** `evals/results/ta05-implementation-review-51a8318/`.
**Verdict:** implementation approved, no blocking findings. All five hosted CI jobs passed
on this exact commit. TA05 is closed for the selected option A admission policy.

## Contract and implementation

- **D01 is closed.** Worker finish, queued/paused cancellation and approval/review
  rejection/expiry all call the shared metadata helper before their existing commits.
  The worker retains its lease fence; administrative and review paths retain their
  job-row locking and authorization. The helper introduces no nested transaction.
- **D02 is closed.** Resume removes an old record; terminal settlement independently
  removes it before recomputing. No record survives when the current limit is disabled,
  invalid, exactly met or above the saved spend. A lower finish-time limit produces the
  new comparison rather than preserving the prior one.
- The helper reads the authoritative accounting accessor. Contract snapshots are checked;
  legacy snapshots use the existing restoration rule. It does not sum usage rows or
  substitute a stale document copy for the accounting column.
- Admission arithmetic and typed refusal behavior are unchanged. Successful final-call
  overshoot remains complete when no work was refused. Names and documentation correctly
  distinguish admission from a strict cap and qualify unknown usage, retries and changes
  to the configured limit.

## The three deviations

1. **Combined cleanup mutant M9 is acceptable for the end-to-end invariant.** Removing
   only one redundant cleanup does not expose stale metadata along the normal resume
   path. The combined mutant demonstrates that the existing resume test detects loss
   of the overall behavior; it does not independently prove both lines. This review also
   directly seeded prior metadata before terminal settlement, bypassing resume's cleanup,
   and confirmed the helper's own removal/replacement under six settings.
2. **The wider validation guard is appropriate.** The additional exceptions originate
   from malformed accounting shapes, including legacy ones. The guard is confined to
   metadata calculation. Independent checks confirm that stale lease refusal and commit
   failure retain their normal behavior.
3. **Store-level tests suit the administrative exits.** Queued cancellation and approval
   rejection do not execute through a Worker. Their fixtures exercise real stores and
   approval transactions. The worker-based cases still cover model execution, refusal,
   cancellation and resume.

No existing test changed. The nine new cases and nine mutants match the approved limits.
No additional implementation or design round is needed.

## Independent verification

Fresh focused suite: **146 passed**, covering admission metadata, token budget/recovery,
TA02 durability, approvals, durable execution, TA06 status and TA07 cache accounting.

Additional edge probes: **14 passed**:

- paused cancellation and approval expiry;
- direct stale-record cleanup/replacement under a raised, disabled, invalid, equal,
  unchanged or lowered limit, with a contradictory document accounting copy;
- missing/list-shaped contract accounting, an invalid boolean total, and a malformed
  legacy copy, all allowing terminal completion without a record;
- a stale worker refused before it can publish terminal metadata;
- status and metadata rolled back together after a flushed transaction's commit fails.

These probes use temporary databases and real Store/approval transactions; they make no
model or business request. Application source and original evidence were left unchanged.

## Evidence and closing gate

The builder's five baseline failures/four controls, nine named mutant kills, five full
2,021-test seed runs, six-file isolation proof and 24-row deployed report were inspected.
They are distinguished from the fresh independent tests above. Preservation and source
hash checks are recorded in `checks.json`; exact-SHA hosted status is in `hosted-ci.json`.

Approval applies to the selected admission policy. This does not add strict reservation,
output allowances, pricing, historical backfill or a frontend badge.

[Hosted CI run 42232453634](https://github.com/SoumithReddy6/Enterprise-No-Code-Agentic-AI-Workflow-Platform/actions/runs/42232453634)
passed backend, frontend, browser-layout, native-macos and postgres-conformance. Both
hosted backend runs report 2,019 passed and two platform skips (2,021 collected). Schema
checks passed 25 tests; frontend passed 115, browser-layout 49 and native-macos 16.
Typecheck, lint, build and the final fault-profile check also passed.
