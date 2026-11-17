# Spec: leases, heartbeat, claim, poison runs, fair queue (audit W06, W07, W15; defects DEF-04, DEF-05, DEF-07, DEF-09)

Status: ready to implement. Written 2026-11-16 against source `24e659c`.
Order: do the load baseline (`docs/specs/load-baseline-harness.md`) first. Every step below
reports a before and an after number from that harness.

## 1. Current behavior

| Topic | Code | Behavior |
|---|---|---|
| Job row | `backend/app/storage.py:112-119` | `run_id, status, owner, lease_until (float epoch), cancel_requested, created (float)`. No attempt count, no tenant |
| Claim | `storage.py:669-686` | `timestamp = time.time()` in Python. Read up to 20 eligible ids ordered by `created`, with no lock. For each: `UPDATE … WHERE run_id=? AND eligible`; on `rowcount == 1` set the run `running`, write a `running` event, commit, return the document |
| Fence | `storage.py:688-691` | `UPDATE execution_jobs SET owner=owner WHERE run_id=? AND owner=? AND status='running' AND lease_until > time.time()` — Python clock |
| Heartbeat | `storage.py:693-696`; `backend/app/worker.py:244-247` | Every 0.2 s for each run: `cancel_requested` (one `SELECT`, `storage.py:698-701`), then fence `UPDATE`, then `lease_until = time.time() + 30`, commit |
| Lease length | `storage.py:669,693` | 30 s, a default argument. Not a setting |
| Poll | `worker.py:291-300` | Each worker process calls `claim_next` until its 4 slots are full, then sleeps 0.1 s |
| Supervisor loop | `worker.py:242-274` | The graph task is cancelled only in the `CancelledError` branch (`:265`) |
| Indexes | `backend/migrations/versions/0001_baseline_baseline.py:106` | `ix_execution_jobs_status` only |

Defects in this code (tests in `backend/tests/defects/`): DEF-04 (no attempt limit), DEF-05 (a
cancelled run is claimed), DEF-07 (a job with no run blocks the queue), DEF-09 (a heartbeat error
leaves the graph task running). Audit gaps: C1 (Python clock), C2 (heartbeat rate), C3 (claim
contention), C6 (no `SIGTERM` handling), C7 (no fairness).

## 2. Target design

### 2.1 One clock: the database

All lease arithmetic and comparison happens in SQL. `lease_until` **stays a float epoch column**
(no type migration).

Add to `storage.py`:

```python
def db_epoch(dialect_name):
    """Seconds since the epoch on the database's clock, as a SQL expression."""
    if dialect_name == 'postgresql':
        return func.extract('epoch', func.clock_timestamp())
    # SQLite: its clock is this host's clock. SQLite is a single-host store.
    return (func.julianday('now') - 2440587.5) * 86400.0
```

`Store` keeps `self.now_sql = db_epoch(self.engine.dialect.name)`. Rules:

- `claim_next`, `_fence`, `heartbeat`, `readiness.database_probe` (`readiness.py:29`) compare and
  set `lease_until` with `self.now_sql`. No `time.time()` remains in these four.
- Lease length: `self.lease_seconds`, from `RELAY_LEASE_SECONDS` (integer 5–600, default 30;
  outside the range `Store.__init__` raises `ValueError`). The `lease_seconds` arguments of
  `claim_next` and `heartbeat` stay (tests and `scripts/scenarios/milestone_worker.py:37` pass
  1); their default becomes `None` = `self.lease_seconds`.
- Approval expiry (`approvals.py:74,116,156,190,217`) keeps `time.time()`. It is a 24-hour
  policy time, not a lease; skew of seconds does not matter. State this in a comment.

### 2.2 Heartbeat: one statement, lease/3

`Store.heartbeat(id, owner, lease_seconds=None)` becomes one statement and one commit:

```sql
UPDATE execution_jobs
   SET lease_until = <now_sql> + :lease
 WHERE run_id = :id AND owner = :owner AND status = 'running' AND lease_until > <now_sql>
RETURNING cancel_requested
```

Return value: `None` when no row matched (lease lost); otherwise the boolean `cancel_requested`.
`Store.cancel_requested` stays for other callers (`worker.py:260,266`).

Worker loop (`worker.py:243-247`) becomes:

```python
interval = max(0.2, self.store.lease_seconds / 3)
try:
    while not task.done():
        state = await asyncio.to_thread(self.store.heartbeat, id, owner)
        if state is None or state is True:
            task.cancel(); break
        await asyncio.wait({task}, timeout=interval * random.uniform(0.9, 1.1))
    output = await task
    ...
finally:
    if not task.done():                      # DEF-09: every exit path stops the graph task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
```

The existing `except` branches stay as they are; the `finally` is added around them. With a 30 s
lease the heartbeat runs every 10 s (±10%). A missed heartbeat leaves two more before expiry.
Cancel latency rises from 0.2 s to at most lease/3. The cancel route (`main.py:176-184`) waits
0.5 s today; change its loop to wait up to `lease/3 + 1` s in steps of 0.25 s.

### 2.3 Claim

New columns on `execution_jobs` (revision `0007_job_attempts`; use the next free number):

```
attempts    Integer NOT NULL DEFAULT 0      -- claims so far
tenant_id   String(64) NULL                 -- filled by create_run; backfilled from runs
INDEX ix_execution_jobs_queue (status, created)
```

`create_run` (`storage.py:478`) sets `tenant_id`. The revision backfills
`UPDATE execution_jobs SET tenant_id = (SELECT tenant_id FROM runs WHERE runs.id = run_id)`.

New table (same revision):

```
scheduler_tenants
  tenant_id            String(64) PRIMARY KEY
  last_served_at       Float NOT NULL DEFAULT 0
  max_concurrent_runs  Integer NULL            -- NULL = RELAY_TENANT_MAX_CONCURRENT_RUNS (default 8, range 1–1000)
```

`create_run` inserts the tenant's row when it is absent (`ON CONFLICT DO NOTHING`).

`claim_next(owner, lease_seconds=None)` — one transaction, in this order:

1. **Pick.** PostgreSQL, strategy `skip_locked` (default):

   ```sql
   SELECT j.run_id FROM execution_jobs j
   LEFT JOIN scheduler_tenants t ON t.tenant_id = j.tenant_id
   WHERE (j.status = 'queued' OR (j.status = 'running' AND j.lease_until < <now_sql>))
     AND (SELECT count(*) FROM execution_jobs r
           WHERE r.tenant_id = j.tenant_id AND r.status = 'running' AND r.lease_until >= <now_sql>)
         < COALESCE(t.max_concurrent_runs, :default_limit)
   ORDER BY COALESCE(t.last_served_at, 0), j.created
   LIMIT 1
   FOR UPDATE OF j SKIP LOCKED
   ```

   SQLite, and PostgreSQL with strategy `cas`: the same `SELECT` without the `FOR UPDATE` clause
   and with `LIMIT 20`, then the compare-and-set `UPDATE` of today for each candidate
   (`storage.py:676`). Strategy comes from `RELAY_CLAIM_STRATEGY` (`skip_locked` | `cas`;
   SQLite always uses `cas`).

   A job whose `tenant_id` is `NULL` (a row from before the revision that the backfill missed)
   is treated as tenant `'local'`.

2. **Take.** `UPDATE execution_jobs SET status='running', owner=:owner,
   lease_until=<now_sql>+:lease, attempts=attempts+1 WHERE run_id=:id AND <eligible>`. On
   `rowcount == 0` go to the next candidate.

3. **Dispose without executing** (each case commits and continues with the next pick; the claim
   returns a job only from case d):

   | Case | Condition | Action in this transaction |
   |---|---|---|
   | a. Orphan (DEF-07) | The run row does not exist | `status='failed'`, `lease_until=0`, `owner=''` on the job; journal `job.orphan` |
   | b. Cancelled (DEF-05) | `cancel_requested` is true | The same writes as `request_cancel` does for a queued job (`storage.py:784-788`): job and run `cancelled`, `finished_at`, token-limit record, operator values, a `cancelled` run event |
   | c. Poison (DEF-04) | `attempts > RELAY_MAX_CLAIM_ATTEMPTS` (default 5, range 1–100) **and** the run has no unsettled write | Job and run `failed`, error `'This run was started 5 times and never finished. It was stopped (max_attempts).'`, `finished_at`, token-limit record, operator values, a `failed` run event with `'reason': 'max_attempts'` |
   | c2. Poison with an unsettled write | As c, and `check_resume_writes` would raise | The same as c with the uncertain-write message appended. Never clear a marker |
   | d. Normal | otherwise | Today's writes (`storage.py:678-681`) plus `UPDATE scheduler_tenants SET last_served_at=<now_sql> WHERE tenant_id=:t`; commit; return the document |

   `resume_run` (`storage.py:876`) and `approvals.decide` (`approvals.py:167`) set `attempts = 0`
   when they queue the job again: a person asked for a new start.

4. If the document cannot be built after the commit (`_document` raises), journal
   `job.unreadable` and finish the job and run as `failed` in a new fenced transaction. Do not
   raise out of `claim_next`.

### 2.4 Idle polling and shutdown

- `Worker.serve` (`worker.py:291-300`): when a claim returns `None`, sleep `idle` and double it
  (start 0.1 s, maximum 2 s); reset to 0.1 s after a successful claim. No `LISTEN/NOTIFY` in this
  spec (rejected below).
- `worker.main` (`worker.py:305-316`): install handlers for `SIGTERM` and `SIGINT` with
  `loop.add_signal_handler` that cancel the `serve()` task. The existing `finally` in `serve`
  (`worker.py:301-303`) cancels the runs; each run's `CancelledError` branch releases its job
  (`worker.py:264-269`). Exit code 0.
- Slots: `RELAY_WORKER_SLOTS` (1–64, default 4) replaces the constant at `worker.py:58,310`.

## 3. Rejected alternatives

| Alternative | Why not |
|---|---|
| Change `lease_until` to `timestamptz` | A type migration on the hottest row for no gain: an epoch computed in SQL is the same clock |
| NTP discipline as the answer to skew | Cannot be tested or proven in the repository |
| A monotonic fencing token passed to remote systems | Remote systems here do not take one; the marker protocol covers the send. It also does not stop an early takeover |
| `SKIP LOCKED` with no comparison | The claim is the least measured part. Keep `cas` behind a setting and publish both numbers |
| `LISTEN/NOTIFY` for wake-up and cancel | A second delivery path with its own failure modes (lost notifications on reconnect). Backoff to 2 s is enough at this scale. Reconsider only if the harness shows idle polling above 5% of statements |
| A counter column for running jobs per tenant | Drifts after a crash. The count subquery uses the `(status, created)` index and is exact |
| Redis for the queue or fairness | A second source of truth (`README.md:72`) |
| Weighted fair queuing with virtual time | More than this needs; least-recently-served with a cap gives bounded wait |
| A dead-letter table | The failed run with the cause `max_attempts` is the dead letter |

## 4. Invariants that must not break

| Id | Invariant | Check |
|---|---|---|
| I1 | The fence is an `UPDATE` in the same transaction as each worker write | `_fence` keeps its shape; only its clock expression changes |
| — | One claim gives one owner id | `worker.py:292` unchanged |
| — | The claim writes the `running` event in its commit | Case d keeps `storage.py:678-681` |
| I7 | An unsettled write blocks resume | Cases c and c2 never clear a marker; a poison stop is a `failed` run, and resume still calls `check_resume_writes` |
| I8 | A pause releases the lease in its own commit | `approvals.pause` unchanged; `decide` only adds `attempts = 0` |
| — | Recovery time after a kill equals the lease | S15, S16, S24, S28 assert it; they must pass with the default 30 s |

## 5. Change list, in commit order

| # | Commit | Files | Notes |
|---|---|---|---|
| 1 | Tests first (red) | `backend/tests/test_lease_clock.py`, `test_claim_rules.py`, `test_worker_supervision.py` (new) | §6. Record the failures on the parent commit |
| 2 | Database clock | `storage.py`, `readiness.py` | §2.1. Removes the `xfail` of nothing yet |
| 3 | Lease setting | `storage.py` | `RELAY_LEASE_SECONDS` |
| 4 | One-statement heartbeat + supervisor `finally` | `storage.py`, `worker.py`, `main.py:176-184` | §2.2. Remove the `xfail` marker of DEF-09 in this commit |
| 5 | Revision: `attempts`, `tenant_id`, index, `scheduler_tenants` | migration, `storage.py` models, `create_run` | Extend `postgres-migrations` and `populated-downgrade` |
| 6 | Claim disposal cases a, b, c | `storage.py`, `approvals.py:167` | Remove the `xfail` markers of DEF-04, DEF-05, DEF-07 in this commit |
| 7 | Fair pick (order and cap), strategy `cas` | `storage.py` | Works on SQLite and PostgreSQL |
| 8 | Strategy `skip_locked` on PostgreSQL | `storage.py` | Default on PostgreSQL only after the harness comparison is saved |
| 9 | Idle backoff, `SIGTERM`, slots setting | `worker.py` | §2.4 |
| 10 | Storage checks and scenario | `scripts/check_milestone_storage.py`, `scripts/scenarios/` | `postgres-clock-skew`, `postgres-poison-run`, `sigterm-release`, `postgres-fair-queue`; scenario S36 |
| 11 | Harness rerun, evidence, docs | `evals/results/…`, `docs/operations.md`, `README.md` §2.1 | Before and after numbers |

## 6. Acceptance tests

### 6.1 `backend/tests/test_lease_clock.py`

| Test name | Asserts |
|---|---|
| `test_a_live_lease_is_not_taken_when_the_python_clock_is_one_hour_ahead` | Claim as A. `monkeypatch.setattr('backend.app.storage.time.time', lambda: real() + 3600)`. `claim_next('B')` returns `None`. (Fails today: B takes it) |
| `test_the_fence_holds_when_the_python_clock_is_one_hour_ahead` | Claim as A; patch the clock +3600; `worker_event(run, 'A', event)` returns `True`. (Fails today) |
| `test_an_expired_lease_is_taken_when_the_python_clock_is_one_hour_behind` | Claim as A with `lease_seconds=1`; wait until `SELECT <now_sql>` passes `lease_until`; patch the clock −3600; `claim_next('B')` returns the run |
| `test_heartbeat_returns_none_false_true` | No row match → `None`; live lease → `False`; after `request_cancel` → `True`; exactly one SQL statement and one commit (SQLAlchemy `before_cursor_execute` counter) |
| `test_lease_seconds_setting_bounds` | 4 and 601 raise `ValueError`; 5 and 600 are accepted |

### 6.2 `backend/tests/test_claim_rules.py`

| Test name | Asserts |
|---|---|
| `test_attempts_increase_once_for_each_claim` | 3 claim/expire cycles → `attempts == 3` |
| `test_a_run_over_the_attempt_limit_is_failed_in_the_claim_and_not_returned` | With the limit 5: the 6th `claim_next` returns `None`; run `failed`; error contains `max_attempts`; the last run event has `reason == 'max_attempts'` |
| `test_a_poison_run_with_an_unsettled_write_stays_unresumable` | As above with `mark_write` before the kills; `resume_run` raises the uncertain-write message |
| `test_resume_and_approval_reset_attempts` | `resume_run` sets 0; `approvals.decide(approve)` sets 0 |
| `test_a_cancelled_running_job_is_finished_as_cancelled_by_the_claim` | The DEF-05 sequence; `claim_next` returns `None`; run `cancelled`; one `cancelled` run event; no `running` event after the cancel |
| `test_an_orphan_job_is_failed_and_the_next_run_is_claimed` | The DEF-07 sequence; `claim_next` returns the valid run; the orphan job is `failed` |
| `test_claim_serves_the_least_recently_served_tenant_first` | Tenant A has 5 queued runs created first, tenant B has 1. Claims in order: A, B, A, A, A, A |
| `test_claim_respects_the_tenant_concurrency_cap` | Cap 2 for A, 5 queued A runs, 1 queued B run: after two A claims the next claim is B, then `None` while both A leases are live |
| `test_claim_strategy_setting` | `cas` and `skip_locked` accepted; another value raises; SQLite uses `cas` for both |

Parametrize the last three and the two poison tests over SQLite and PostgreSQL
(`RELAY_TEST_POSTGRES_URL`), and on PostgreSQL over both strategies.

### 6.3 `backend/tests/test_worker_supervision.py`

| Test name | Asserts |
|---|---|
| `test_a_heartbeat_error_cancels_the_graph_task` | The DEF-09 test, moved here without `xfail` |
| `test_heartbeat_interval_is_a_third_of_the_lease` | With lease 30 and a fake `asyncio.wait`, the timeouts are within 9–11 s |
| `test_sigterm_releases_runs_in_flight` | Start `Worker.serve` in a subprocess with a held model fixture; send `SIGTERM`; the job is `queued` with `owner == ''` within 2 s; exit code 0 |
| `test_idle_worker_backs_off_to_two_seconds` | With no jobs and a counted `claim_next`, at most 8 calls in the first 5 s (0.1, 0.2, 0.4, 0.8, 1.6, 2, 2) |

### 6.4 Storage checks (`scripts/check_milestone_storage.py`)

| Check | Asserts |
|---|---|
| `postgres-clock-skew` | Two worker processes started with `faketime`-free skew: each process patches `backend.app.storage.time.time` by +60 s and −60 s through a test entry point in `scripts/scenarios/milestone_worker.py`. 20 runs: 20 unique claims, no run claimed while its lease is live (read from `run_events`: no second `running` event before `lease_until`) |
| `postgres-poison-run` | A worker that exits (`os._exit`) at a failpoint on every attempt: the run ends `failed` with `max_attempts` after exactly 5 claims; a second, healthy run finishes meanwhile |
| `sigterm-release` | `SIGTERM` during a held model call: a second worker claims the run in under 2 s (not 30 s) and the model is called once more, not twice |
| `postgres-four-workers` (extend) | 16 processes, 500 runs: 500 unique claims, no duplicated item row. Run it for `cas` and for `skip_locked` |
| `postgres-fair-queue` | Tenant A queues 1,000 runs, then tenant B queues 10, 4 workers, cap 8: every B run is claimed before the 60th A run is claimed (read the claim order from `run_events`) |

Existing checks and scenarios must pass unchanged: `postgres-stale-lease` (5 refusals), S15, S16,
S24, S28 (recovery time equals the lease), all `*-sigkill-*`, all `*-accounting-*`.

### 6.5 Mutants (scratch copies; M00 first)

| Id | Mutant | Killed by |
|---|---|---|
| M00 | none | control |
| M01 | `_fence` compares with `time.time()` | `test_the_fence_holds_…` |
| M02 | `claim_next` eligibility uses `time.time()` | `test_a_live_lease_is_not_taken_…` |
| M03 | Heartbeat without the owner condition | `postgres-stale-lease`, `test_heartbeat_returns_none_false_true` |
| M04 | Heartbeat ignores `RETURNING` (always `False`) | `test_heartbeat_returns_none_false_true` |
| M05 | No `attempts` increment | `test_attempts_increase_…` |
| M06 | Limit compare `>=` in place of `>` | `test_a_run_over_the_attempt_limit_…` (5th claim must still execute) |
| M07 | Case b removed | `test_a_cancelled_running_job_…` |
| M08 | `ORDER BY j.created` only | `test_claim_serves_the_least_recently_served_…` |
| M09 | Cap subquery removed | `test_claim_respects_the_tenant_concurrency_cap` |
| M10 | `SKIP LOCKED` removed from the PostgreSQL pick | `postgres-four-workers` duration assertion is **not** a kill; this mutant must be killed by a test that holds a row lock in one session and asserts that a second `claim_next` returns a different run within 1 s (`test_skip_locked_does_not_wait_on_a_locked_candidate`, PostgreSQL only) |
| M11 | Supervisor `finally` removed | `test_a_heartbeat_error_cancels_the_graph_task` |

## 7. Measurements to record (from the load harness)

For the parent commit and after commits 4, 6, 8: runs/min at 1, 4, 16 and 32 workers for the
three harness workflows; claim latency p50/p95/p99; commits for each run; heartbeat commits/s for
each active run; idle statements/s for each worker; for commit 8 also `cas` against `skip_locked`
at 4, 16 and 32 workers. Write "unmeasured" where a number is missing. Never estimate.

## 8. Failure-mode analysis

| Failure | Result with this design |
|---|---|
| Host clocks differ | No effect: only the database clock is used |
| The database clock jumps backward (failover to a replica with an older clock) | Leases last longer by the jump; no early takeover. Recovery is slower by the jump. State it in `docs/operations.md` |
| The database clock jumps forward | Live leases expire early: a second worker takes a run whose first worker is alive. The owner check refuses the first worker's writes; a marked write makes the run fail as uncertain. This is the residual risk; it now needs a database clock fault, not a host clock fault |
| A heartbeat is lost (one failed statement) | Two more arrive before expiry |
| The heartbeat statement raises | The `finally` cancels the graph task; the generic branch finishes the run as failed, or, if the database is down, the lease expires and another worker takes it. The first worker's task is already cancelled |
| A worker dies on each attempt | 5 claims, then `failed` with `max_attempts` |
| The claim transaction fails after the `UPDATE` | It rolls back; `attempts` is not increased; the job is eligible again |
| One tenant queues 10,000 runs | Other tenants are picked first by `last_served_at`; the cap bounds the tenant's running count |
| A tenant's cap is 0 or negative | Not accepted: the setting and the column are validated to ≥ 1 |
| `scheduler_tenants` row missing | `LEFT JOIN` + `COALESCE`: treated as never served, default cap |
| Old workers (no `attempts` code) run beside new workers during a restart | Old claims do not increase `attempts`; the limit is reached later. No wrong stop |

## 9. Rollback

- Commits 2–4 and 6–9 are code only: deploy the previous build.
- Commit 5 (revision): `downgrade()` drops `scheduler_tenants`, the index and the two columns.
  Nothing else reads them. Old code ignores the columns, so the schema can stay upgraded while
  the code is rolled back.
- `RELAY_CLAIM_STRATEGY=cas` returns to the old claim without a deploy.
- `RELAY_LEASE_SECONDS` and the heartbeat interval: setting only.

## 10. Done when

- All tests in §6.1–6.3 pass on SQLite and PostgreSQL; the checks of §6.4 pass; mutants M01–M11
  are killed and M00 passes.
- `backend/tests/defects/` no longer contains DEF-04, DEF-05, DEF-07, DEF-09 tests with `xfail`
  (they moved to the regular suite and pass).
- `grep -n "time.time()" backend/app/storage.py backend/app/readiness.py` shows no lease use.
- The numbers of §7 are saved under `evals/results/` with the commit hashes, and
  `docs/resume-evidence.md` has the new verified rows.
