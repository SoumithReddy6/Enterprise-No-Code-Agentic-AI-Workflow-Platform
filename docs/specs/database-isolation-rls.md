# Spec: database-level tenant isolation with PostgreSQL row-level security (audit W11; gap S3)

Status: ready to implement after W08 (tenant column on every child table) and the `Principal` of
`docs/specs/multi-tenancy-core.md`. Written 2026-11-16 against source `24e659c`.

Goal: when application code forgets a tenant filter, the database still returns no row of another
organization. The application filter stays; this is the second barrier.

## 1. Current behavior

| Topic | Code | Behavior |
|---|---|---|
| Scope | `backend/app/storage.py:350,368,372,378,383,391,404,420,427,455,472,487,495,501,509,771,866` | Each `Store` method takes `tenant_id='local'` and adds its own `WHERE` |
| Sessions | 54 `Session(...)` calls in `storage.py` (32), `approvals.py` (10), `auth.py` (5), `tool_service.py` (3), `agent_memory.py` (2), `auth_security.py`, `operator_metrics.py`, `readiness.py`; plus `engine.begin()`/`connect()` at `storage.py:258`, `auth.py:175`, `readiness.py:26` | Each opens a session on the one engine, with no tenant context |
| Database role | `compose.yaml`, `.github/workflows/ci.yml:106-109` | One role `relay`, which creates and **owns** every table, and is the superuser of the test container |
| Startup | `storage.py:258-280` | Every process runs schema upgrade, two data migrations, and a cross-tenant scan for runs with no job |
| Cross-tenant readers | `storage.py:669-686` (claim), `approvals.py:113-118` (expiry scan), `operator_metrics.py:99-125` (deployment-wide metrics), `readiness.py:25-30` | They must see more than one organization |
| Tables without `tenant_id` | `run_loop_items`, `run_action_results`, `run_token_usage`, `run_guard_decisions`, `run_admission_metrics`, `execution_jobs` (`storage.py:54-67,99-119`; `operator_metrics.py:9-50`) | W08 adds the column; the concurrency spec adds it to `execution_jobs` |

PostgreSQL facts this design depends on (check them against the PostgreSQL 17 documentation,
chapter "Row Security Policies", before the first commit):

1. A superuser and a role with `BYPASSRLS` are never subject to row-level security.
2. The **owner** of a table is not subject to its policies unless the table has
   `FORCE ROW LEVEL SECURITY`.
3. With row-level security enabled and no policy that applies to the role, the default is deny.
4. `set_config(name, value, true)` and `SET LOCAL` last until the end of the current transaction.
5. `current_setting(name, true)` returns `NULL` when the setting does not exist; a custom setting
   that was set and then reset reads as the empty string.

## 2. Target design

### 2.1 Roles

| Role | Attributes | Used by | Owns tables |
|---|---|---|---|
| `relay_owner` | `NOSUPERUSER NOBYPASSRLS NOCREATEROLE` | Alembic only (`MIGRATION_DATABASE_URL`) | **Yes**, all |
| `relay_app` | `NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB`, `LOGIN` | API processes (`DATABASE_URL`) | **No** |
| `relay_worker` | the same as `relay_app` | Worker processes (`WORKER_DATABASE_URL`, default `DATABASE_URL`) | **No** |

Role creation needs a privileged login, so it is **not** an Alembic revision. It is a script:
`scripts/postgres_roles.sql` (idempotent; `CREATE ROLE … ` guarded by `pg_roles` checks; passwords
from `psql` variables). CI and `compose.yaml` run it once as the container's superuser. The
revision of 2.3 then runs as `relay_owner`.

Grants (in the revision): `GRANT USAGE ON SCHEMA`; for `relay_app` and `relay_worker`
`SELECT, INSERT, UPDATE, DELETE` on the tenant tables and the global tables they use; **no**
`TRUNCATE`, no `REFERENCES`, no DDL. `audit_log`: `SELECT, INSERT` only (the multi-tenancy spec).
`ALTER DEFAULT PRIVILEGES FOR ROLE relay_owner` gives the same grants to future tables, and the
catalog test of 6.2 still fails when a new tenant table has no policy.

### 2.2 Session context

Two settings, both set for **every** transaction on PostgreSQL:

- `relay.tenant_id`: the organization id, or `''`.
- `relay.user_id`: the user id (for the membership list before an organization is chosen), or `''`.

One factory replaces every direct `Session(...)`:

```python
# backend/app/storage.py
@dataclass(frozen=True)
class Scope:
    tenant_id: str = ''
    user_id: str = ''

GLOBAL = Scope()      # reaches only global tables and what a policy allows with no tenant

class Store:
    def session(self, scope, **kwargs):
        if not isinstance(scope, Scope):
            raise TypeError('A database session needs a Scope.')
        s = Session(self.engine, **kwargs)
        s.info['scope'] = scope
        return s

@event.listens_for(Session, 'after_begin')
def _apply_scope(session, transaction, connection):
    if connection.dialect.name != 'postgresql':
        return
    scope = session.info.get('scope')
    if scope is None:
        raise RuntimeError('Database session opened without a Scope.')
    connection.execute(text("SELECT set_config('relay.tenant_id', :t, true), set_config('relay.user_id', :u, true)"),
                       {'t': scope.tenant_id, 'u': scope.user_id})
```

`after_begin` fires for each new transaction of the session, so a session that commits and
continues sets the context again. `set_config(..., true)` ends with the transaction, so a pooled
connection carries nothing to the next user. A session with no scope raises: there is no default.

Changing the tenant **inside** a transaction is allowed in exactly one place, the claim (2.5):
`Store._enter_tenant(s, tenant_id)` runs the same `set_config` statement and updates
`s.info['scope']`.

### 2.3 Policies (one revision, `rls_policies`, PostgreSQL only; SQLite: `upgrade()` returns)

Helper expression, written out in each policy (no function, so nothing can be redefined):

```sql
tenant_id = NULLIF(current_setting('relay.tenant_id', true), '')
```

`NULL` compares as not true, so a missing or empty setting matches no row.

**Class T — tenant tables.** `ALTER TABLE t ENABLE ROW LEVEL SECURITY; ALTER TABLE t FORCE ROW LEVEL SECURITY;`
and one policy `tenant_isolation FOR ALL TO relay_app, relay_worker USING (<helper>) WITH CHECK (<helper>)`:

`workflows`, `runs`, `run_events`, `run_loop_items`, `run_action_results`, `run_write_intents`,
`run_approvals`, `run_token_usage`, `run_guard_decisions`, `run_admission_metrics`,
`run_participants`, `credentials`, `tool_connections`, `allowed_models`, `approval_rate_limits`,
`agent_memories`, `service_accounts`, `audit_log`, `scheduler_tenants`.

**Class J — the job queue.** `execution_jobs`, `ENABLE` + `FORCE`:

- `tenant_isolation FOR ALL TO relay_app USING (<helper>) WITH CHECK (<helper>)`.
- `worker_queue FOR ALL TO relay_worker USING (true) WITH CHECK (true)`. The table holds ids,
  status, owner, lease, attempts and the tenant id. It holds no payload.

**Class M — memberships and organizations.**

- `memberships`: `FOR SELECT … USING (<helper> OR user_id = NULLIF(current_setting('relay.user_id', true), ''))`;
  `FOR INSERT/UPDATE/DELETE … USING (<helper>) WITH CHECK (<helper>)`.
- `organizations`: `FOR SELECT … USING (id = NULLIF(current_setting('relay.tenant_id', true), '') OR id IN (SELECT tenant_id FROM memberships))`
  (the subquery is itself filtered by the `memberships` policy, so it lists only the user's
  organizations); `FOR UPDATE/DELETE … USING (id = <tenant setting>)`; `FOR INSERT … WITH CHECK (true)`
  (creating an organization is a registration step; the application authorizes it).

**Class A — approval expiry scan.** `run_approvals` gets one more policy:
`worker_expiry_scan FOR SELECT TO relay_worker USING (status IN ('pending','approved'))`, and the
revision revokes table-wide `SELECT` from `relay_worker` on `run_approvals` and grants column
`SELECT (id, run_id, tenant_id, status, expires_at, kind, invocation, node_id, checkpoint_owner, digest, payload, execution, result, created_at, owns_marker)`
— all columns, because after the scan the worker reads the row under the tenant context anyway.
The scan (`approvals.py:113-118`) selects `id, run_id, tenant_id` under `GLOBAL`; each close
(`approvals.py:119-125`) then runs in a session with that row's tenant scope.

**Class G — global tables, no row-level security, listed by name in the test of 6.2:**
`users`, `auth_sessions`, `api_tokens`, `auth_invites`, `auth_oidc_states`, `auth_login_budgets`,
`auth_bootstrap`, `worker_heartbeats`, `schema_migrations`, `alembic_version`. They are read
before a tenant is known (a session cookie or a token hash is looked up first). They hold hashes
and identity rows, no workflow or run data. A source test (6.4) allows queries on them only from
`auth.py`, `auth_security.py`, `readiness.py` and the migrations.

### 2.4 Application changes

1. Replace all 54 `Session(self.engine…)` / `Session(store.engine…)` calls with
   `store.session(scope…)`. The scope is: the method's tenant for tenant methods; `GLOBAL` for
   auth lookups and the two scans; the run's tenant for fenced worker methods (see 2.5).
2. Remove the `tenant_id='local'` defaults (`storage.py:350…866`, `tool_service.py:164…293`,
   `kb/rpc.py:56,62`). A call with no tenant becomes a `TypeError`. Keep the explicit `WHERE`
   filters: they are the first barrier.
3. Startup (`storage.py:258-280`): in multi-user mode (`RELAY_MULTI_USER=true`) the application
   does **not** upgrade the schema or run data steps. It checks that the schema is at head
   (`auth.py:175-177` already does) and that 2.6 holds. Migrations run as `relay_owner` with
   `python -m alembic upgrade head`. The scan for runs with no job (`storage.py:275-280`) moves
   into a one-time data migration.
4. Operator metrics (`operator_metrics.py:99-125`, `main.py:100-103`): in multi-user mode the
   query runs in the caller's tenant scope, so it returns that organization's numbers. The
   definition text `'scope': 'deployment-wide'` becomes `'organization'`. Cross-organization
   numbers come from Prometheus (audit W19), not from this endpoint.
5. Readiness (`readiness.py:25-30`): `worker_heartbeats` is global, so the probe still works. The
   live-lease query runs as `relay_app` under `GLOBAL` and sees no row; the probe then uses the
   heartbeat age, as it does today when no lease is live.

### 2.5 The worker

- `claim_next` opens `store.session(GLOBAL)`. As `relay_worker` it can read and update
  `execution_jobs` (policy `worker_queue`). After the take step it reads `tenant_id` from the job
  row, calls `_enter_tenant(s, tenant_id)`, and only then touches `runs`, `run_events` and the
  other tables in the same transaction. Order inside the transaction: pick → take → enter tenant
  → dispose or write the `running` event → commit.
- Every fenced method (`worker_event`, `finish_run`, `release`, `mark_write`, `settle_write`,
  `reserve_accounting`, `record_accounting`, `record_vector_dependencies`, `heartbeat`,
  `run_tenant`, `resolve_run_credential`, and in `approvals.py` `pause`, `resolve`, `begin`,
  `complete`, `consume_review`, `checked_review`) gains a required `tenant_id` argument, passed
  by the worker from the claimed run (`run['tenant_id']`, `storage.py:685`), and opens
  `store.session(Scope(tenant_id))`. The fence `UPDATE` itself is allowed by `worker_queue`
  regardless of the tenant; the writes after it are allowed only for the run's tenant.
- A worker that passes the wrong tenant gets a fence that matches (jobs are visible) and then
  reads `None` for the run row. Each fenced method must treat "run row not visible" as
  `LeaseLost` (never as an empty run).

### 2.6 Startup guard

In multi-user mode on PostgreSQL, `Store.__init__` runs these checks as its own role and refuses
to start (`RuntimeError`, message names the failed check) unless all hold:

```sql
SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;            -- both false
SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = current_schema() AND c.relkind = 'r'
   AND pg_get_userbyid(c.relowner) = current_user;                                    -- 0
SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = current_schema() AND c.relkind = 'r'
   AND c.relname = ANY(:tenant_tables) AND NOT (c.relrowsecurity AND c.relforcerowsecurity);  -- no row
```

`:tenant_tables` is the class T, J, M, A list, kept as one constant `RLS_TABLES` in `schema.py`
that the revision, this guard and the tests all import.

Multi-user mode on SQLite: `Store.__init__` raises
`RuntimeError('Multi-user mode needs PostgreSQL: SQLite has no row-level security.')`.

### 2.7 Knowledge-service databases

`kb_search_segments` and `kb_management_bases` have `tenant_id`; `kb_search_chunks`,
`kb_search_postings`, `kb_search_chunk_owners`, `kb_management_originals` do not
(`backend/app/kb/search_models.py`, `management_models.py`). This spec does **not** add row-level
security to them. Reason: these services are reached only through the RPC, their isolation is the
signed resolution (`docs/specs/signed-retrieval-resolution.md`), and they are outside Alembic
today (audit D4). Record it as a known limit. A follow-up can add `tenant_id` and policies after
D4.

## 3. Rejected alternatives

| Alternative | Why not |
|---|---|
| One schema for each tenant | Migrations × tenants; `search_path` handling in a pool; the claim must scan every schema |
| One database for each tenant | Operations cost; no shared queue |
| Policies without `FORCE` | The owner would bypass them; a deploy that connects as the owner by mistake would have no barrier. With `FORCE` and the startup guard, that mistake stops the process |
| A `SECURITY DEFINER` function for every cross-tenant read | More code that runs with the owner's rights. Only the two narrow policies (`worker_queue`, `worker_expiry_scan`) are needed |
| `SET` at connection checkout (session-level) | Leaks to the next user of the pooled connection if a reset is missed. Transaction-local settings cannot leak |
| A default tenant when the scope is missing | Hides the bug this spec exists to catch. Missing scope raises |
| Policy through a join to `runs` on child tables | Slow and fragile; W08's `tenant_id` column plus the composite foreign key makes the simple policy correct |
| Remove the application filters once policies exist | Then SQLite development and any policy mistake have no barrier. Keep both |

## 4. Invariants that must not break

| Id | Invariant | Risk here | Protection |
|---|---|---|---|
| I1 | Fence first, in the same transaction | The fence is now a statement on a table with a different policy than the rows it protects | The fence statement is unchanged; `set_config` runs in `after_begin`, before the fence; test L01 |
| I2, I3 | Marker before send; settle in one commit | A policy that hides the run row would make `mark_write` update nothing | Fenced methods treat an invisible run as `LeaseLost`; test L03 |
| I4 | Event, checkpoint, usage, accounting in one commit | The same | Test L02 runs the accounting SIGKILL checks as `relay_worker` |
| I7 | An unsettled write blocks resume | `check_resume_writes` reading an empty set under a wrong scope | The API's `resume_run` runs under the caller's tenant; a wrong tenant gets 404 before the check |
| — | Claim uniqueness and fairness | The claim reads across tenants | Policy `worker_queue`; `postgres-four-workers` as `relay_worker` |

## 5. Change list, in commit order

| # | Commit | Files |
|---|---|---|
| 1 | Role script; CI and compose create the three roles; CI's PostgreSQL job connects as `relay_app` / `relay_worker` for tests and `relay_owner` for migrations | `scripts/postgres_roles.sql`, `compose.yaml`, `.github/workflows/ci.yml`, `scripts/check_postgres.py`, `scripts/check_milestone_storage.py:33-49` |
| 2 | Tests first (red): 6.1–6.4 | `backend/tests/test_rls.py`, `test_scope.py`, `test_session_factory_source.py` (new) |
| 3 | `Scope`, `store.session`, the `after_begin` listener; no caller changed yet (the listener accepts a missing scope with a `DeprecationWarning` in this commit only) | `storage.py` |
| 4 | Convert `storage.py` (32 call sites), remove its `'local'` defaults | `storage.py`, `main.py`, `worker.py` |
| 5 | Convert `approvals.py`, `tool_service.py`, `agent_memory.py`, `operator_metrics.py`, `readiness.py`, `auth.py`, `auth_security.py`; the listener now raises on a missing scope | those files |
| 6 | Worker: `tenant_id` on every fenced method; claim enters the tenant | `storage.py`, `approvals.py`, `worker.py` |
| 7 | Revision `rls_policies` with `RLS_TABLES`; grants; downgrade | `backend/migrations/versions/…`, `schema.py` |
| 8 | Startup guard and multi-user startup path | `storage.py`, `main.py`, `worker.py` |
| 9 | Storage checks and scenarios run under the roles; scenario S33 | `scripts/` |
| 10 | Mutants, evidence, docs | `evals/results/…`, `docs/operations.md`, `docs/security-hardening.md` |

Commits 3–6 change no behavior on SQLite and none on PostgreSQL before commit 7. Run the full
gate after commit 6 and again after commit 8.

## 6. Acceptance tests

All PostgreSQL tests open **their own engine as `relay_app` or `relay_worker`**. A session
fixture `app_connection` asserts first:

```python
row = conn.execute(text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
assert row.current_user in ('relay_app', 'relay_worker') and not row.rolsuper and not row.rolbypassrls
assert conn.scalar(text("SELECT count(*) FROM pg_tables WHERE schemaname = current_schema() AND tableowner = current_user")) == 0
```

A test run as the owner or a superuser proves nothing, so this fixture fails such a run; it does
not skip.

### 6.1 `backend/tests/test_rls.py` (PostgreSQL only; skipped with a visible reason on SQLite)

Seed two organizations A and B with one row in every table of `RLS_TABLES`, through the owner
connection.

| Id | Test name | Asserts |
|---|---|---|
| L01 | `test_fenced_write_works_under_the_runs_tenant` | As `relay_worker`: claim, `worker_event`, `finish_run` succeed for A's run |
| L02 | `test_every_tenant_table_hides_other_tenants_rows` | Parametrized over `RLS_TABLES`, as `relay_app` with tenant A: `SELECT count(*)` for B's rows is 0; `UPDATE` and `DELETE` on B's rows affect 0 rows; the rows still exist (owner connection) |
| L03 | `test_insert_for_another_tenant_is_refused` | Parametrized: `INSERT` with `tenant_id = B` under tenant A raises a row-level security error |
| L04 | `test_no_setting_sees_nothing` | With scope `GLOBAL`: count 0 on every class T table |
| L05 | `test_the_setting_does_not_outlive_its_transaction` | Engine with `pool_size=1`: a transaction with tenant A; then a new transaction on the same connection with `GLOBAL`: `current_setting('relay.tenant_id', true)` is `''` and the count is 0 |
| L06 | `test_raw_sql_without_a_filter_is_still_isolated` | `SELECT * FROM runs` (no `WHERE`) under tenant A returns only A's rows |
| L07 | `test_worker_sees_all_jobs_and_no_other_table` | As `relay_worker` with `GLOBAL`: `execution_jobs` shows A and B; `runs`, `run_events`, `credentials` show 0 rows |
| L08 | `test_worker_expiry_scan_sees_only_open_approvals` | As `relay_worker` with `GLOBAL`: pending and approved rows of A and B are visible; completed and rejected rows are not |
| L09 | `test_a_fenced_method_with_the_wrong_tenant_raises_lease_lost` | Claim A's run; call `mark_write(run, owner, 'n', tenant_id=B)`: `LeaseLost`; nothing written |
| L10 | `test_membership_list_before_a_tenant_is_chosen` | Scope with `user_id` only: the user's memberships in A and B are visible; another user's are not |
| L11 | `test_app_role_cannot_run_ddl_or_truncate` | `TRUNCATE runs`, `DROP TABLE runs`, `ALTER TABLE runs DISABLE ROW LEVEL SECURITY` each raise a permission error |

### 6.2 Catalog tests (same file)

| Id | Test name | Asserts |
|---|---|---|
| C01 | `test_every_table_is_classified` | Every table in `schema.target_metadata` is in `RLS_TABLES` or in `GLOBAL_TABLES`; none in both. A new table in neither list fails |
| C02 | `test_every_rls_table_has_force_and_a_policy` | For each name: `relrowsecurity` and `relforcerowsecurity` true; at least one row in `pg_policies` |
| C03 | `test_every_rls_table_has_a_tenant_column` | `tenant_id` exists and is `NOT NULL` (except `organizations`, keyed by `id`) |
| C04 | `test_app_roles_own_nothing_and_have_no_bypass` | The three queries of 2.6 |
| C05 | `test_startup_refuses_an_owner_or_superuser_connection` | `Store(owner_url)` in multi-user mode raises; `Store(app_url)` starts |
| C06 | `test_startup_refuses_a_table_without_force` | As the owner, `ALTER TABLE runs NO FORCE ROW LEVEL SECURITY`; `Store(app_url)` raises and names `runs` |
| C07 | `test_multi_user_mode_refuses_sqlite` | `RuntimeError` |

### 6.3 `backend/tests/test_scope.py` (both databases)

| Test name | Asserts |
|---|---|
| `test_a_session_without_a_scope_cannot_be_opened` | `store.session(None)` raises `TypeError` |
| `test_store_methods_need_a_tenant` | Parametrized over the public `Store` methods that took `tenant_id='local'`: a call with no tenant raises `TypeError` |
| `test_scope_is_set_again_after_commit_in_one_session` | PostgreSQL: commit, then a second statement in the same session still sees the tenant's rows |

### 6.4 `backend/tests/test_session_factory_source.py`

| Test name | Asserts |
|---|---|
| `test_no_direct_session_construction` | An `ast` walk of `backend/app/**/*.py`: no call named `Session` except inside `Store.session`. (The knowledge services under `backend/app/kb/` are excluded by an explicit list, per 2.7) |
| `test_global_tables_are_queried_only_by_auth_modules` | An `ast` walk: the model classes of `GLOBAL_TABLES` are referenced only in `auth.py`, `auth_security.py`, `readiness.py`, `schema.py` |

### 6.5 Deployed and storage checks

- All of `ci.yml:126-130` pass with the worker processes connected as `relay_worker` and the API
  as `relay_app`.
- Scenario S33: two organizations; each cross-read of a run, its events, an approval, a
  credential list and a knowledge base returns 404 or an empty list.
- S12 (existing tenant isolation scenario) passes unchanged.

### 6.6 Mutants (M00 control first; PostgreSQL)

| Id | Mutant | Must be killed by | Must **still pass** |
|---|---|---|---|
| M01 | Remove `WHERE tenant_id` from `Store.run` | the application-level matrix test of the multi-tenancy spec | L02, L06 (the policy still isolates): this is the proof that the second barrier works |
| M02 | Policy on `runs` created without `FORCE` and tests run as owner | the `app_connection` fixture | — |
| M03 | `after_begin` listener sets nothing | L01 (the worker sees no run) | — |
| M04 | `set_config(..., false)` (session-level) | L05 | — |
| M05 | `worker_queue` policy also added to `runs` | L07 | — |
| M06 | One table removed from `RLS_TABLES` | C01 | — |
| M07 | The claim touches `runs` before `_enter_tenant` | L01 | — |
| M08 | Fenced method treats an invisible run as an empty document | L09 | — |

## 7. Failure-mode analysis

| Failure | Result |
|---|---|
| A developer adds a query with no tenant filter | Policy returns only the scope's rows (L06) |
| A developer adds a table and forgets the policy | C01 fails in CI |
| A deploy connects the application as the owner | Startup refuses (C05) |
| Someone disables `FORCE` by hand | The next start refuses (C06) |
| A session is opened with no scope | `RuntimeError` at the first statement |
| A pooled connection is reused | The setting ended with the transaction (L05) |
| The worker passes a wrong tenant | `LeaseLost`, nothing written (L09) |
| A long transaction changes tenant | Only `_enter_tenant` can, and only the claim calls it (source test: one call site) |
| `pgbouncer` in transaction mode | Works: the setting is transaction-local. Statement mode is not supported; say so in `docs/operations.md` |
| Policy cost | One equality on an indexed column for each statement. The load harness reruns after commit 8; record the difference. Unmeasured until then |

## 8. Rollback

- Before commit 7 nothing changes in the database.
- Revision `rls_policies` downgrade: `DROP POLICY` each, `NO FORCE`, `DISABLE ROW LEVEL SECURITY`,
  revoke the grants. Application code from commit 6 works with or without the policies.
- To return to one role: point `DATABASE_URL` at the owner and set `RELAY_MULTI_USER=false`; the
  guard is off in single-user mode.
- The roles themselves stay; they are harmless.

## 9. Done when

- L01–L11, C01–C07, the scope and source tests pass; the `app_connection` fixture proves the role
  for every PostgreSQL test; mutants M01–M08 behave as the table says and M00 passes.
- `grep -rn "tenant_id='local'" backend/app` returns nothing.
- The deployed gate and all PostgreSQL checks pass under the roles; S33 passes.
- `docs/gap-audit.md` §11 marks W11 done; `docs/resume-evidence.md` gets the W09–W11 line with
  its measured matrix size.
