# TA02 implementation review — 2026-10-31

Reviewed commit: `f3afb25e6af8bbc24782390edb65321168f354a0`.
Approved plan: `docs/superpowers/plans/2026-10-31-token-accounting-durability.md`.
Independent evidence: `evals/results/ta02-review-f3afb25/`.

## Verdict

No actionable implementation defect found in the approved TA02 scope. The implementation
satisfies the result/accounting commit invariant, the monotonic total rule, the pending-save
barrier, and the selected legacy policy. All five hosted jobs have now passed on the exact
reviewed commit. TA02 can close within its approved scope.

## What was checked

- `Store._set_accounting` is the single checked assignment used by result publication,
  fast saves, action reservations and approval pauses. Its stored read follows the job
  fence. Marked runs use the strict column value; older runs use `restored_tokens`.
- `worker_event` commits the frozen snapshot with the event, usage records, loop row and
  checkpoint. Missing accounting on a marked publishing event raises the control-flow
  `AccountingUnavailable` rather than an error `on_error` can recover.
- The compiler refreshes the shared snapshot at every emission, including cancellation and
  nested events. The Worker captures it before dispatching its storage thread and
  acknowledges that generation only after persistence. Acknowledging an earlier
  generation leaves later known usage pending.
- `approvals.pause` consumes the supplied snapshot, adds its deferred action once, and
  commits it with approval, reusable frames and lease release. It does not reconstruct
  current spend from an older database snapshot.
- `call_model` gates the common provider entry point. Failed fast saves keep the paid
  answer but cannot authorize another provider request without a confirmed save. Grounded
  and structured repair handlers preserve the typed refusal.
- New runs acquire their marker and zero snapshot together. Marked malformed runs stop
  before node events; older runs are not promoted by cached or tool-only completion. An
  unstarted legacy approved write is refused at claim.
- Existing test edits retain the behavioral assertions. The two legacy-storage fixtures
  now actually omit the marker and accounting column. The rollback fixture supplies the
  newly required snapshot so its original JSON failure remains the tested failure.

## Independent verification

| Check | Result |
|---|---|
| Focused accounting, S07, token recovery, operations, approval and Response tests | 184 passed |
| Full backend suite, `PYTHONHASHSEED=0` | 1,974 passed, exit 0 |
| SQLite real-process SIGKILL before outcome commit | Neither checkpoint nor 15-token total survives; unsaved model response is repeated on resume, as documented |
| SQLite real-process SIGKILL after outcome commit | Checkpoint and 15 tokens survive together; one provider call across both attempts; next call refused |
| Lost fast-save acknowledgment after actual commit | Repair safely re-saves the pending snapshot; two calls total, 30 tokens stored |
| Marked publishing event with no snapshot | Typed refusal; no event, checkpoint or accounting change |
| SQLAlchemy 2.0.52 rollback-hook countercheck | Session hook with explicit flush rolls back; throwing in the connection commit hook leaves an uncommitted change visible on the pooled connection |
| `git diff --check` | Clean |
| Hosted CI, run `41249088972` | All five passed: backend, native-macos, frontend, browser-layout, postgres-conformance |

The 24-case module is included in both test runs. The implementer's five-seed, isolation,
12-mutant and 24-row production reports were inspected, not independently rerun. The
PostgreSQL checks were verified in hosted CI, including its two accounting process rows;
the user's local PostgreSQL container was not used. Existing evidence and source hashes
are checked separately in `preserved-inputs.json`.

## Plan deviations

Accepted. The rollback test explicitly flushes before raising in `SessionEvents.before_commit`;
the actual SIGKILL tests still hold at `ConnectionEvents.commit`. The independent probe
reproduces why these are different on pinned SQLAlchemy 2.0.52. Neither an exception from
the commit callback nor a lost acknowledgment is assumed to prove rollback.

The Task 1 intermediate count, strengthened claim-time assertion, internal missing-snapshot
message and legacy fixture changes do not weaken the shipped contract. Tasks 1 and 2 land
together, so the marker is never shipped without its runtime handoff.

## Comparison with documented production patterns

The appropriate pattern is committing dependent durable state together. PostgreSQL documents
that all updates in a transaction become durable and visible together. Relay applies this
to the reusable outcome and cumulative snapshot. See [PostgreSQL 17 transactions](https://www.postgresql.org/docs/17/tutorial-transactions.html).

AWS's transactional-outbox guidance likewise addresses inconsistent independent writes by
grouping related state in one transaction. Relay uses that consistency principle here;
it does not introduce an outbox or claim that a provider request and database commit are
atomic. See [AWS transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html).

The test hooks are documented separately by SQLAlchemy: [Session before_commit](https://docs.sqlalchemy.org/en/20/orm/events.html#sqlalchemy.orm.SessionEvents.before_commit)
and [Connection commit](https://docs.sqlalchemy.org/en/20/core/events.html#sqlalchemy.events.ConnectionEvents.commit).
The pooled-connection result above is a local observation of version 2.0.52, not a claim
that the documentation guarantees the same exceptional-hook behavior for every driver.

## Limits of this approval

A provider response lost before any durable save can still be paid for and repeated.
The pre-commit process test demonstrates that limit: two real model calls, but only the
resumed call's 15 tokens are recorded. TA02 guarantees that saved reusable work is not
ahead of its known saved accounting; it does not supply billing-grade exactly-once usage.

Provider-internal retries, missing provider usage, strict concurrent reservations and
overlapping outcome metrics remain separate findings. The monotonic guard closes the
stale lower-total write demonstrated here, not all parallel settlement concerns.

## Review execution notes

The first local process invocation used a file entry point instead of the module entry
point and failed to import `backend`. The corrected invocation initially hit the sandbox's
localhost restriction. Both unsuccessful attempts remain in this evidence directory; the
permitted module invocation ran both rows successfully. No product code was changed.
