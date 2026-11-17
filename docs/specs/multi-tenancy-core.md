# Spec: multi-tenancy core (audit W09, W10, W12, W13, W23; gaps S1, S2, S4, S5, S7)

Status: ready to implement after W08 (data integrity). Written 2026-11-16 against source
`24e659c`. Database policies are a separate spec (`database-isolation-rls.md`). Retrieval grants
are a separate spec (`signed-retrieval-resolution.md`).

Scope of this file: organizations, memberships, roles, permission checks, the actor on runs and
approvals, separation of duties, the audit log, service tokens, OIDC, and the migration from the
single-user schema.

## 1. Current behavior

| Topic | Code | Behavior |
|---|---|---|
| Account | `backend/app/auth.py:24-29` | `auth_accounts (id, email UNIQUE, password_hash, tenant_id)`. One tenant column on the account |
| Registration | `auth.py:186-227` | Each new account gets `tenant_id = new_id()` (`:208`). The first account also takes every row with tenant `'local'` (`:209-216`) |
| Session | `auth.py:38-42,121-133,155-162` | `auth_sessions (token_hash, account_id, expires_at)`; opaque cookie; 7 days |
| Route authorization | `backend/app/main.py:105-195`; `tool_api.py:11-15`; `kb_gateway.py:97-146` | Every route depends on `auth.tenant`, which returns the account's tenant id. No role, no permission |
| Operator | `auth.py:144-153` | "Operator" = an account of the first tenant, or an id in `RELAY_OPERATOR_ACCOUNT_IDS` |
| Run creation | `main.py:155-161`; `backend/app/storage.py:472-479` | The body carries the whole workflow. The run stores no user |
| Approval decision | `main.py:164-174`; `backend/app/approvals.py:141-171` | `decide(store, run_id, tenant, approval_id, digest, action, choice)`. No user is passed or stored (`approvals.py:26-44`) |
| Approval visibility | `storage.py:491-493` | Only inside `GET /api/runs/{id}` |
| Journal | `backend/app/observability.py:18,27-35` | JSON lines on stdout; the field allowlist has no actor |
| Origins | `main.py:65-66`; `auth.py:112-119` | Trusted hosts and CORS origins are constants; `AUTH_ORIGIN` adds one origin |
| Tests of authorization | `backend/tests/test_auth.py`, `test_workspace_api.py`; scenarios S11, S12 | Tenant against tenant only |

## 2. Target design

### 2.1 Vocabulary

- **Organization**: the unit of isolation. Its id **is** the existing `tenant_id` value. The
  column name `tenant_id` stays everywhere. No run, event or credential row is rewritten.
- **Principal**: who acts. A user (person) or a service account (machine), in one organization.
- **Permission**: a string such as `runs:start`. Code checks permissions, never role names.

### 2.2 Tables (names are final; revision numbers: use the next free ones, in the order of §2.11)

```
organizations
  id                  String(64)  PK                    -- = tenant_id
  name                String(120) NOT NULL
  slug                String(64)  NOT NULL UNIQUE       -- ^[a-z0-9][a-z0-9-]{1,62}$
  self_approval       Boolean     NOT NULL DEFAULT false
  oidc_domain         String(253) NULL                  -- verified email domain that may join as viewer
  created_at          String(64)  NOT NULL
  deleted_at          String(64)  NULL

users                                                   -- renamed from auth_accounts
  id                  String(64)  PK
  email               String(254) NOT NULL UNIQUE
  password_hash       String(256) NULL                  -- NULL for OIDC-only users
  oidc_issuer         String(255) NULL
  oidc_subject        String(255) NULL
  platform_operator   Boolean     NOT NULL DEFAULT false
  disabled_at         String(64)  NULL
  UNIQUE (oidc_issuer, oidc_subject)
  CHECK (password_hash IS NOT NULL OR oidc_subject IS NOT NULL)

memberships
  tenant_id           String(64)  NOT NULL  FK organizations(id) ON DELETE CASCADE
  user_id             String(64)  NOT NULL  FK users(id) ON DELETE CASCADE
  role                String(16)  NOT NULL  CHECK (role IN ('owner','admin','builder','operator','approver','viewer'))
  created_at          String(64)  NOT NULL
  PRIMARY KEY (tenant_id, user_id)

service_accounts
  id                  String(64)  PK
  tenant_id           String(64)  NOT NULL  FK organizations(id) ON DELETE CASCADE
  name                String(120) NOT NULL
  role                String(16)  NOT NULL  CHECK (role IN ('builder','operator','viewer'))
  created_by          String(64)  NOT NULL                -- user id
  disabled_at         String(64)  NULL
  UNIQUE (tenant_id, name)

api_tokens
  id                  String(64)  PK
  service_account_id  String(64)  NOT NULL  FK service_accounts(id) ON DELETE CASCADE
  prefix              String(12)  NOT NULL                -- shown in lists
  token_hash          String(64)  NOT NULL UNIQUE         -- sha256 hex of the full token
  expires_at          Integer     NOT NULL                -- epoch seconds
  last_used_at        Integer     NULL
  revoked_at          Integer     NULL
  created_at          Integer     NOT NULL

auth_sessions  (+ columns)
  active_tenant_id    String(64)  NULL
  idle_expires_at     Integer     NOT NULL                -- now + 8 h, moved forward at most once a minute
  -- expires_at stays: absolute limit, 7 days

auth_invites  (+ columns)
  tenant_id           String(64)  NULL  FK organizations(id) ON DELETE CASCADE   -- NULL = bootstrap invite
  role                String(16)  NULL

auth_oidc_states
  state_hash          String(64)  PK                      -- sha256 of the state value
  nonce               String(64)  NOT NULL
  code_verifier       String(128) NOT NULL
  expires_at          Integer     NOT NULL                -- now + 600

runs  (+ columns)
  initiated_by_type   String(8)   NOT NULL  CHECK (initiated_by_type IN ('user','service'))
  initiated_by_id     String(64)  NOT NULL
  initiator_inferred  Boolean     NOT NULL DEFAULT false  -- true for rows migrated from single-user data
  workflow_id         String(64)  NULL                    -- set when the run started from a saved workflow

run_participants
  run_id              String(64)  NOT NULL
  tenant_id           String(64)  NOT NULL
  principal_type      String(8)   NOT NULL
  principal_id        String(64)  NOT NULL
  action              String(16)  NOT NULL  CHECK (action IN ('start','resume','reconcile'))
  at                  String(64)  NOT NULL
  PRIMARY KEY (run_id, principal_type, principal_id, action)
  FK (run_id, tenant_id) -> runs (id, tenant_id) ON DELETE CASCADE

run_approvals  (+ columns)
  initiator_id            String(64) NOT NULL DEFAULT ''   -- copied from the run at pause
  decided_by_id           String(64) NULL
  decided_at              Float      NULL
  self_approval_allowed   Boolean    NOT NULL DEFAULT false  -- the organization's flag, copied at the decision
  CHECK (decided_by_id IS NULL OR decided_by_id <> initiator_id OR self_approval_allowed)

audit_log        -- see 2.8
```

A rule that a table cannot express: an organization always has at least one `owner`. The
membership change runs `SELECT count(*) … WHERE role='owner'` in its transaction (after
`pg_advisory_xact_lock` keyed by the organization on PostgreSQL, `BEGIN IMMEDIATE` on SQLite, the
pattern of `auth_security.py:44-56`) and refuses the change that would leave zero.

### 2.3 Principal

```python
# backend/app/principal.py  (new)
@dataclass(frozen=True)
class Principal:
    type: str            # 'user' | 'service'
    id: str
    tenant_id: str
    role: str            # one of the six
    session_id: str = '' # token hash prefix, for the audit log
    @property
    def key(self): return f'{self.type}:{self.id}'
    @property
    def permissions(self): return ROLE_PERMISSIONS[self.role]
    def scope(self): return Scope(self.tenant_id, self.id if self.type == 'user' else '')
```

Resolution in `auth.py` (replaces `AuthController.tenant`, `auth.py:135-142`), in this order:

1. `Authorization: Bearer relay_…` present → token path (2.7). No cookie is read. The origin
   check is skipped (no ambient credential).
2. Otherwise the session cookie → user; `active_tenant_id` must be set and the user must have a
   membership there, else 409 `{'detail': 'Choose an organization.', 'organizations': [...]}`.
   For unsafe methods the origin check stays (`auth.py:114-119,136-137`).
3. Neither → 401.

With authentication disabled (`auth_enabled=False`, tests and local single-user use) the
principal is `Principal('user', 'local', 'local', 'owner')`, and the organization `local` exists
with `self_approval = true`.

### 2.4 Permissions and roles

`backend/app/permissions.py` (new) holds one dict. This table is the only source; the
documentation and the test matrix are generated from it.

| Permission | owner | admin | builder | operator | approver | viewer |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `org:manage` (delete, transfer, `self_approval`, `oidc_domain`) | ✔ | | | | | |
| `members:manage` (members, invites, service accounts, tokens) | ✔ | ✔ | | | | |
| `audit:read` | ✔ | ✔ | | | | |
| `models:write` | ✔ | ✔ | | | | |
| `models:read` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `credentials:write` (create, rotate, delete credentials and connections) | ✔ | ✔ | | | | |
| `credentials:read` (names and ids, never secrets) | ✔ | ✔ | ✔ | | | |
| `workflows:write` (save, edit, delete, validate) | ✔ | ✔ | ✔ | | | |
| `workflows:read` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `runs:start_inline` (run a workflow sent in the request) | ✔ | ✔ | ✔ | | | |
| `runs:start` (run a saved workflow by id) | ✔ | ✔ | ✔ | ✔ | | |
| `runs:control` (cancel, resume) | ✔ | ✔ | ✔ | ✔ | | |
| `runs:read` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `runs:reconcile` | ✔ | ✔ | | ✔ | | |
| `approvals:decide` | ✔ | ✔ | | | ✔ | |
| `kb:write` | ✔ | ✔ | ✔ | | | |
| `kb:read` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `kb:search` | ✔ | ✔ | ✔ | ✔ | | |
| `metrics:read` | ✔ | ✔ | | ✔ | | |

Why `runs:start_inline` exists: `POST /api/runs` takes a whole workflow today (`main.py:29-31`).
If an Operator could send any workflow, Operator would equal Builder. So `POST /api/runs` accepts
**either** `{"workflow_id": "...", "message": "..."}` (needs `runs:start`; the stored document is
loaded, validated and snapshotted) **or** `{"workflow": {...}, "message": "..."}` (needs
`runs:start_inline`). Exactly one of the two keys; both or neither is 422.

Service accounts can hold only `builder`, `operator` or `viewer` (the `CHECK` above), so a
machine can never decide an approval or manage members.

Fixed roles only. Rejected: custom roles (cannot be tested completely).

### 2.5 The one check

```python
def require(permission):                       # FastAPI dependency factory, in auth.py
    def dependency(request: Request) -> Principal:
        principal = controller.principal(request)          # 2.3
        if permission not in principal.permissions:
            audit.denied(principal, permission, request)    # 2.8
            raise HTTPException(403, 'Your role does not permit this action.')
        request.state.principal = principal
        return principal
    dependency.permission = permission                       # read by the route-walk test
    return dependency
PUBLIC = object()   # marker for routes with no principal; attached as dependency.permission = PUBLIC
```

Route map (every route that exists today, and the new ones):

| Route | Permission |
|---|---|
| `GET /api/health`, `GET /api/ready`, `GET /api/auth/status`, `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/oidc/login`, `GET /api/auth/oidc/callback` | `PUBLIC` |
| `GET /api/auth/me`, `GET /api/auth/organizations`, `POST /api/auth/switch`, `POST /api/auth/password`, `POST /api/auth/sessions/revoke-all`, `POST /api/organizations` | session only (a second marker `SESSION`: a signed-in user with or without an active organization) |
| `GET /api/operator/metrics` | `metrics:read` |
| `GET /api/nodes` | `workflows:read` |
| `GET /api/providers/ollama/models`, `GET /api/models` | `models:read` |
| `POST /api/models`, `PUT /api/models/{id}` | `models:write` |
| `POST /api/validate`, `POST /api/workflows`, `PUT /api/workflows/{id}`, `DELETE /api/workflows/{id}` (new) | `workflows:write` |
| `GET /api/workflows`, `GET /api/workflows/{id}` | `workflows:read` |
| `GET /api/credentials`, `GET /api/connections` | `credentials:read` |
| `POST /api/credentials`, `POST /api/connections`, `PUT /api/connections/{id}`, `DELETE /api/credentials/{id}` (new), `DELETE /api/connections/{id}` (new) | `credentials:write` |
| `POST /api/runs` | `runs:start`, and `runs:start_inline` checked in the handler when the body has `workflow` |
| `GET /api/runs`, `GET /api/runs/page`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events`, `GET /api/approvals` (new) | `runs:read` |
| `POST /api/runs/{id}/cancel`, `POST /api/runs/{id}/resume` | `runs:control` |
| `POST /api/runs/{id}/approve`, `POST /api/runs/{id}/reject` | `approvals:decide` |
| `GET /api/knowledge-bases`, `…/capabilities`, `GET …/{kb_id}`, `…/documents/{doc_id}/file`, `…/documents/{doc_id}/preview` | `kb:read` |
| `POST /api/knowledge-bases`, `PUT …/{kb_id}`, `…/rebuild`, `…/cancel`, `…/delete`, `…/documents`, `…/documents/{doc_id}/retry`, `…/documents/{doc_id}/remove` | `kb:write` |
| `POST /api/knowledge-bases/{kb_id}/search` | `kb:search` |
| `GET/POST /api/members`, `PUT/DELETE /api/members/{user_id}`, `POST /api/invites`, `GET/POST /api/service-accounts`, `DELETE /api/service-accounts/{id}`, `GET/POST /api/service-accounts/{id}/tokens`, `DELETE /api/tokens/{id}` (all new) | `members:manage` |
| `GET /api/organization`, | `workflows:read` |
| `PUT /api/organization`, `DELETE /api/organization` (new) | `org:manage` |
| `GET /api/audit` (new) | `audit:read` |

The event stream (`main.py:194-217`) checks the principal again on each tick, as it checks the
session today (`main.py:205-208`), and also that the role still holds `runs:read`.

Also in this work: CORS `allow_methods` adds `DELETE` (`main.py:66`); allowed origins and trusted
hosts come from `RELAY_ALLOWED_ORIGINS` and `RELAY_TRUSTED_HOSTS` (comma lists; defaults are
today's constants); the proxy forwards `X-Forwarded-For` only when `RELAY_TRUSTED_PROXY` names its
address, and `auth_security.client_address` (`auth_security.py:33-41`) reads the header only then.

### 2.6 Runs, approvals and separation of duties

**Run.** `create_run` (`storage.py:472`) takes the principal and writes `initiated_by_*`,
`workflow_id`, and a `run_participants` row `start`, in its single transaction. `resume_run`
(`storage.py:866`) adds a `resume` row. The run document also gets `'initiated_by': key` so that
the worker has it without a query.

**The worker acts as the initiator.** `claim_next` returns `initiated_by` with the document. The
worker builds `Principal` from it by reading the membership (or service account) **at claim
time**: a principal that was removed or disabled since the run started makes the run fail with
`'The account that started this run no longer has access.'` before any node runs. Credential and
connection use (`storage.py:462-470`, `tool_service.py:164-169`) needs `credentials:read` on that
principal; the role `operator` does not have it, so add the rule: a run started by `workflow_id`
uses the permissions of the **workflow's last editor** for credential and connection use
(`workflows.updated_by`, new column), and the initiator's permissions for everything else. This
is what lets an Operator run a workflow that a Builder wired to a credential.

**Pause.** `approvals.pause` (`approvals.py:80`) copies `run.initiated_by_id` into
`initiator_id`.

**Decide.** `decide(store, run_id, principal, approval_id, digest, action, choice)`. New checks,
placed **after** the lock and the existence check (`approvals.py:145-147`) and **before** the
digest checks, so that a refused decision never reveals whether a digest was right:

1. `principal.type == 'user'` else `PermissionError('Only a person can decide an approval.')`.
2. `participants = {principal_id for rows of run_participants of this run}`. If
   `principal.id in participants` and not `organization.self_approval`:
   `PermissionError('You started or resumed this run, so another person must decide.')`.
3. On approve or reject: set `decided_by_id`, `decided_at`,
   `self_approval_allowed = organization.self_approval`.

The route maps `PermissionError` to 403 (today's `ValueError` → 409 and `KeyError` → 404 stay,
`main.py:170-174`). The digest binding, expiry, rate limit and state machine do not change. The
rate limit key becomes the principal (`approvals.py:127-139`: primary key `tenant_id` becomes a
key string `<tenant>:<principal>`), with an organization ceiling of 300 a minute.

The `CHECK` on `run_approvals` is the database's copy of rule 2 for the initiator. A resumer who
is not the initiator is caught by the code rule only; state this in the docstring.

**Inbox.** `GET /api/approvals?status=pending` lists the organization's approvals (id, run id,
node, kind, payload preview, digest, initiator, created, expires), newest first, page size 50.

**`self_approval`.** `false` for a new organization. `true` for an organization made by the
migration (§2.11) and for `local`. **Temporary for the scenarios**: W23 rewrites S01, S02, S05,
S14 and S29 with two accounts and removes the flag from the scenario setup (`docs/gap-audit.md`,
W23). Only `org:manage` can change it, and the change writes an audit row.

Not included, by decision: "the workflow's author cannot approve". Record it as a known limit.

### 2.7 Service-account tokens

- Format: `relay_<prefix>_<secret>`; `prefix` = 8 characters of `[a-z0-9]`; `secret` = 43
  characters from `secrets.token_urlsafe(32)`. Regex: `^relay_[a-z0-9]{8}_[A-Za-z0-9_-]{43}$`.
- Stored: `sha256(full token)` hex. A slow hash is not needed for a 256-bit random secret.
- Shown once, in the response of `POST /api/service-accounts/{id}/tokens`. Body:
  `{"expires_in_days": 1–365}` (required).
- Check: regex → hash → row → `revoked_at IS NULL`, `expires_at > now`, service account not
  disabled, organization not deleted. Any failure: 401 with one message,
  `'Invalid or expired token.'`. On success update `last_used_at` at most once a minute.
- Rate limit: 600 requests a minute for each token (a row in a small table keyed by token id and
  minute, the pattern of `approvals.py:127-139`); over it: 429.
- A token never reaches a cookie route: `Bearer` requests to `/api/auth/*` (except `me`) are 400.

### 2.8 Audit log

```
audit_log
  tenant_id     String(64) NOT NULL
  seq           BigInteger NOT NULL
  at            String(64) NOT NULL          -- storage.now()
  actor_type    String(8)  NOT NULL          -- 'user' | 'service' | 'system'
  actor_id      String(64) NOT NULL
  action        String(48) NOT NULL          -- from the fixed list below
  resource_type String(24) NOT NULL
  resource_id   String(128) NOT NULL
  outcome       String(8)  NOT NULL  CHECK (outcome IN ('ok','denied','failed'))
  request_id    String(128) NULL
  ip            String(64) NULL
  detail        JSON NOT NULL                -- ids, digests, old and new role. Never content, never secrets
  prev_hash     String(64) NOT NULL          -- '' for seq 1
  hash          String(64) NOT NULL
  PRIMARY KEY (tenant_id, seq)
```

- `audit.record(session, principal, action, resource_type, resource_id, outcome='ok', **detail)`
  takes the **caller's session**, so the row commits with the change or not at all. Inside it:
  lock the organization's chain (`SELECT … FROM audit_log WHERE tenant_id=:t ORDER BY seq DESC LIMIT 1 FOR UPDATE`
  on PostgreSQL; SQLite has one writer), read the last `seq` and `hash`, insert `seq + 1` with
  `hash = sha256(prev_hash + '\n' + canonical_json(row without hash))`.
- Denied requests and failed sign-ins have no change to join. `audit.denied` opens its own short
  transaction.
- Append-only on PostgreSQL: the application roles get `INSERT, SELECT` only, and a trigger
  `audit_log_immutable BEFORE UPDATE OR DELETE` raises. SQLite: a trigger with `RAISE(ABORT, …)`.
- `python -m scripts.audit_verify --tenant <id>` walks the chain and exits non-zero at the first
  break. The chain makes a change by the owner role visible; it does not prevent it. Say so.
- Fixed action list (a `Literal` type; an unknown action raises): `auth.login`, `auth.login_failed`,
  `auth.logout`, `auth.password_changed`, `auth.sessions_revoked`, `org.created`, `org.updated`,
  `org.deleted`, `member.added`, `member.role_changed`, `member.removed`, `invite.created`,
  `service_account.created`, `service_account.disabled`, `token.created`, `token.revoked`,
  `credential.created`, `credential.rotated`, `credential.deleted`, `connection.saved`,
  `connection.deleted`, `model.allowed`, `model.changed`, `workflow.saved`, `workflow.deleted`,
  `run.started`, `run.cancelled`, `run.resumed`, `approval.approved`, `approval.rejected`,
  `kb.created`, `kb.changed`, `kb.deleted`, `authz.denied`.
- `GET /api/audit?after=<seq>&limit=1–200` returns rows in order.

### 2.9 Local passwords

Kept for development and for organizations without OIDC: the existing PBKDF2 code and login
budgets (`auth.py:71-80`, `auth_security.py`). Enabled by `AUTH_LOCAL_PASSWORDS` (default `true`;
`false` makes `/api/auth/login` and `/register` return 403). New: `POST /api/auth/password`
(current and new password; deletes every other session of the user); `POST
/api/auth/sessions/revoke-all`.

Registration (`auth.py:186-227`) changes: a new account creates a `users` row; then
- first account ever: organization from the bootstrap tenant, role `owner`;
- with an invite that has `tenant_id` and `role`: a membership there;
- mode `open` with no invite: a new organization named after the email's local part, role `owner`.

### 2.10 OIDC

One provider for each deployment, from settings: `OIDC_ISSUER`, `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`. Library: `authlib` (add the pinned version to
`backend/requirements.txt`; run `pip-audit`). Do not write JWT validation by hand.

| Step | Detail |
|---|---|
| `GET /api/auth/oidc/login` | Create `state` (32 bytes), `nonce` (32 bytes), PKCE verifier (64 bytes, method `S256`). Store `auth_oidc_states`. Redirect to the provider's authorization endpoint from discovery (`<issuer>/.well-known/openid-configuration`, cached 1 hour) with `scope=openid email` |
| `GET /api/auth/oidc/callback?code&state` | Look up and **delete** the state row (single use; missing or expired → 400). Exchange the code with the verifier. Validate the ID token: signature against the JWKS (refetch once on an unknown `kid`), `iss == OIDC_ISSUER`, `aud` contains the client id, `exp` in the future, `iat` not more than 5 minutes old, `nonce` equal. Algorithms allowed: `RS256`, `ES256`. `none` and `HS*` are refused |
| Identity | Key: `(iss, sub)`. Never the email. `email_verified` must be `true` |
| First sign-in | No user for `(iss, sub)`: (a) a pending invite for the email → create the user and the membership; (b) else an organization whose `oidc_domain` equals the email domain → membership `viewer`; (c) else 403 `'No organization has invited this account.'` An existing local user with the same email is **not** linked automatically (account takeover risk); linking is a later feature |
| Session | The same opaque cookie as local sign-in (`auth.py:155-167`) |
| Outbound requests | Discovery, JWKS and token calls use `httpx` with `trust_env=False`, a 5 s timeout, no redirects |

### 2.11 Migration from the single-user schema

Expand, move, contract. One revision each, with a tested downgrade. Names, in order:

| Revision | Change | Downgrade |
|---|---|---|
| `orgs_and_members` | Create `organizations`, `memberships`. For each distinct `auth_accounts.tenant_id`: organization (`id` = that value, `name` = the email's local part, `slug` = `org-<first 8 of id>`, `self_approval = true`) and an `owner` membership. If any table has rows with tenant `'local'` or no account exists: organization `local` (`self_approval = true`) | Drop both tables |
| `users_and_tokens` | Rename `auth_accounts` → `users`; add the OIDC, operator and disabled columns; `platform_operator = true` for accounts of the bootstrap tenant (`auth_bootstrap`, `auth.py:149-150`). Create a view `auth_accounts (id, email, password_hash, tenant_id)` over `users` for the previous build. Create `service_accounts`, `api_tokens`, `auth_oidc_states`. Add the session and invite columns (`idle_expires_at = expires_at` for existing sessions) | Reverse; refused when a user has no `password_hash` (an OIDC-only user cannot exist in the old schema) |
| `run_actors` | `runs.initiated_by_*`, `initiator_inferred`, `workflow_id`; `run_participants`; the approval actor columns and `CHECK`; `workflows.updated_by`. Backfill: each old run gets its organization's owner, `initiator_inferred = true`, and a `start` participant row; each old approval gets `initiator_id` from its run, and `self_approval_allowed = true` where it is already decided | Drop the columns and the table |
| `audit_log` | The table and its trigger | Drop |
| `drop_account_tenant` (contract; a later release) | Drop `users.tenant_id` | Re-add and fill from the earliest `owner` membership |

`users.tenant_id` stays readable until the contract revision, so the previous build still runs
against the migrated database (rolling restart, and rollback).

The first-account data move (`auth.py:209-216`) becomes: the first account becomes `owner` of the
organization `local` if it has rows; no row is updated.

`populated-downgrade` in `scripts/check_milestone_storage.py` runs the whole chain up, down to
`0006`, and up again on a database with two accounts, three runs and one pending approval, and
compares row counts and the pending approval's digest.

## 3. Rejected alternatives

| Alternative | Why not |
|---|---|
| Rename `tenant_id` to `org_id` | Touches 10 tables and every query for a name |
| Custom roles, or per-resource grants for workflows and runs | Cannot be tested completely; no interview question needs them. Knowledge bases get grants because retrieval needs them |
| JWT sessions | Cannot be revoked without a store; the opaque session works |
| API keys that belong to a user | A user's key would carry `approvals:decide`. Machines get their own principal with a reduced role set |
| Audit rows written by a background task or from the journal | A change could commit with no audit row |
| An external audit sink (SIEM) as the only store | Good as an addition; the table is the record that the tests can check |
| Enforce separation of duties only in the user interface | Not a control |
| Email as the OIDC identity key | Email can be reassigned at the provider |
| Auto-link an OIDC identity to a local account with the same email | Account takeover through a provider that does not verify email as you expect |
| Several OIDC providers for each organization (SSO) and SCIM | Cut (`docs/gap-audit.md` §8) |

## 4. Invariants that must not break

| Id | Invariant | Where this spec is near it | Protection |
|---|---|---|---|
| I6 | An approval is bound to a digest; the worker rebuilds and compares | `decide` gains checks | The new checks come before the digest checks and change none; `test_approvals.py` passes unchanged except for the new argument |
| I8 | Pause releases the lease in the same commit as its accounting | `pause` copies `initiator_id` | One more assignment on the row inserted in that commit (`approvals.py:80`) |
| I1, I4 | Fence; atomic checkpoint | `create_run` and `resume_run` write more rows | The same single transaction; no worker write path changes in this spec |
| I7 | Unsettled write blocks resume | `resume_run` | Unchanged check; the participant row is added after it, in the same transaction |
| — | Tenant scope on every query (S12) | Every route changes its dependency | The application filter stays; the matrix test adds the cross-organization cells |
| — | Existing deployed scenarios pass | S01, S02, S05, S14, S29 approve with the starting account | `self_approval = true` for `local` and migrated organizations, until W23 |

## 5. Change list, in commit order

Each numbered group is one pull request. The gate (full suite seeds 0–4, deployed gate,
PostgreSQL checks) passes after each.

**PR 1 — organizations and principal (W09 part 1)**

1. Tests first: `test_orgs_migration.py`, `test_principal.py`.
2. Revisions `orgs_and_members`, `users_and_tokens`; models.
3. `principal.py`, `permissions.py`; `AuthController.principal`; `auth.tenant` becomes a thin
   wrapper that returns `principal.tenant_id` (so no route changes yet).
4. Registration and session changes (§2.9); `GET /api/auth/organizations`, `POST /api/auth/switch`,
   `POST /api/organizations`.
5. Settings for origins, hosts, proxy (§2.5).

**PR 2 — permissions on routes (W10)**

6. Tests first: `test_authz_routes.py`, `test_authz_matrix.py` with `authz_matrix.json`.
7. `require()`; convert every route in `main.py`, `tool_api.py`, `kb_gateway.py` to the map of
   §2.5; remove `auth.tenant` and `auth.operator`.
8. `POST /api/runs` two shapes; `workflows.updated_by`; member, invite and organization routes.

**PR 3 — actors and separation of duties (W12)**

9. Tests first: `test_separation_of_duties.py`, `test_run_actors.py`.
10. Revision `run_actors`; `create_run`, `resume_run`, `pause`, `decide` changes; the worker's
    initiator check; `GET /api/approvals`.
11. Scenario S34.

**PR 4 — audit log (W13)**

12. Tests first: `test_audit_log.py`.
13. Revision `audit_log`; `audit.py`; a `record` call in each handler of the action list;
    `scripts/audit_verify.py`; `GET /api/audit`.

**PR 5 — service tokens (W09 part 2)**

14. Tests first: `test_service_tokens.py`.
15. Token routes, the bearer path, the rate limit.

**PR 6 — OIDC (W09 part 3)**

16. Tests first: `test_oidc.py` with a local issuer fixture.
17. `oidc.py`; the two routes; `authlib` pin.

**PR 7 — scenarios without `self_approval` (W23)**

18. Tests first: `test_new_org_self_approval_default_false`,
    `test_scenarios_do_not_set_self_approval`.
19. Two accounts in the scenario lab; S01, S02, S05, S14, S29 rewritten; the flag removed from
    the scenario setup.

## 6. Acceptance tests

### 6.1 `backend/tests/test_authz_routes.py`

| Test name | Asserts |
|---|---|
| `test_every_route_declares_a_permission_or_is_public` | Walk `app.routes` (skip `/docs`, `/openapi.json`, `/redoc`): each route's dependencies contain exactly one with a `.permission` attribute whose value is a key of the permission table, `PUBLIC` or `SESSION`. A route with none fails and is named |
| `test_public_routes_are_exactly_the_listed_ones` | The set of `PUBLIC` routes equals the constant list of §2.5. A new public route fails |
| `test_every_permission_is_used_by_a_route_or_handler` | No permission in the table is unused |

### 6.2 `backend/tests/test_authz_matrix.py` and `backend/tests/authz_matrix.json`

The JSON file has one entry for each route: method, path template, example path and body, and
the permission. The test **generates** the cells: for each route × each of the six roles ×
{own organization, other organization} plus {no session, service token with role operator}.
Expected status: permitted role in its own organization → not 401 and not 403 (the test accepts
2xx, 404, 409, 422 from the handler); any role against the other organization's resource → 404
(never 403: existence is not disclosed) or an empty list for list routes; role without the
permission → 403; no session → 401.

| Test name | Asserts |
|---|---|
| `test_matrix_file_lists_every_route` | The (method, path) set of the file equals the set from `app.routes`. A new route with no entry fails CI |
| `test_matrix_cell` (parametrized) | The expected status of each generated cell |
| `test_cross_organization_reads_return_not_found_or_empty` | For each route with a resource id, organization B's id used with organization A's session gives 404 |
| `test_permission_document_is_current` | `docs/permissions.md` equals the table rendered from `permissions.py` |

### 6.3 `backend/tests/test_separation_of_duties.py`

| Test name | Asserts |
|---|---|
| `test_the_initiator_cannot_approve_their_own_run` | 403; the approval stays `pending`; the receiver got 0 requests |
| `test_the_initiator_cannot_reject_their_own_run` | 403 |
| `test_a_second_approver_can_approve_and_the_write_is_sent_once` | 200; one request; `decided_by_id` set |
| `test_a_user_who_resumed_the_run_cannot_approve` | 403 |
| `test_a_refused_decision_does_not_reveal_the_digest` | The initiator sends a wrong digest and then the right digest: both answers are the same 403 body |
| `test_a_service_account_cannot_decide` | 403 (role lacks the permission) and, with the permission forced in a unit test of `decide`, `PermissionError` |
| `test_the_database_refuses_a_self_decision` | Direct `UPDATE run_approvals SET decided_by_id = initiator_id` raises an integrity error when `self_approval_allowed` is false |
| `test_self_approval_true_permits_and_records_it` | With the flag on: 200 and `self_approval_allowed = true` on the row |
| `test_only_the_owner_can_change_self_approval_and_it_is_audited` | Admin → 403; owner → 200 and an `org.updated` audit row with old and new value |
| `test_new_org_self_approval_default_false` | A newly created organization has `false` |
| `test_decide_keeps_every_existing_refusal` | Wrong digest 409, expired 409, double approve returns the same row, reject then approve 409 (the cases of `test_approvals.py`, called through the new signature) |

### 6.4 `backend/tests/test_run_actors.py`

`test_a_run_stores_its_initiator_and_a_start_participant`;
`test_resume_adds_a_resume_participant`;
`test_migrated_runs_are_marked_initiator_inferred`;
`test_an_operator_can_start_a_saved_workflow_but_not_an_inline_one` (403 for inline);
`test_a_run_by_workflow_id_uses_the_stored_document_not_the_request`;
`test_a_run_fails_before_any_node_when_its_initiator_lost_access` (remove the membership, then
claim: run `failed` with the stated message, no node event);
`test_an_operator_run_uses_the_editors_credential_permission`.

### 6.5 `backend/tests/test_audit_log.py`

`test_each_listed_action_writes_one_row` (parametrized over the action list: perform it through
the API, read one new row with the right actor and resource);
`test_a_failed_change_writes_no_ok_row` (force the handler's commit to fail: no row);
`test_update_and_delete_are_refused_by_the_database` (both databases);
`test_the_chain_verifies_and_a_changed_row_breaks_it` (change one row through the owner
connection: `audit_verify` exits non-zero at that `seq`);
`test_two_concurrent_writers_keep_one_chain` (PostgreSQL, 2 threads × 50 rows: `seq` 1–100 with
no gap, chain valid);
`test_a_denied_request_is_recorded`;
`test_audit_rows_hold_no_content` (the `detail` of every row from the full matrix run has only
keys from an allowlist);
`test_a_viewer_cannot_read_the_audit_log`.

### 6.6 `backend/tests/test_service_tokens.py`

`test_the_token_is_shown_once_and_only_its_hash_is_stored`;
`test_a_bearer_request_needs_no_cookie_and_no_origin`;
`test_an_expired_revoked_or_unknown_token_gets_the_same_401` (three cases, equal bodies);
`test_a_token_of_a_disabled_service_account_is_refused`;
`test_expiry_is_required_and_at_most_365_days`;
`test_a_service_account_cannot_have_the_approver_admin_or_owner_role` (API 422 and the `CHECK`);
`test_a_token_acts_only_in_its_organization` (other organization's run id → 404);
`test_token_rate_limit_returns_429`;
`test_a_run_started_by_a_token_records_a_service_initiator`.

### 6.7 `backend/tests/test_oidc.py`

A fixture issuer: an in-process HTTP server (the pattern of the scenario receivers) that serves
discovery, JWKS and a token endpoint, and signs ID tokens with a test RSA key. No network.

`test_login_redirect_carries_state_nonce_and_pkce`;
`test_callback_creates_a_session_for_an_invited_user`;
`test_state_is_single_use` (second callback with the same state → 400);
`test_expired_state_is_refused`;
`test_a_wrong_signature_is_refused`; `test_a_wrong_issuer_is_refused`;
`test_a_wrong_audience_is_refused`; `test_an_expired_token_is_refused`;
`test_a_wrong_nonce_is_refused`; `test_alg_none_and_hs256_are_refused`;
`test_an_unverified_email_is_refused`;
`test_identity_is_issuer_and_subject_not_email` (same `sub`, changed email → same user; same
email, other `sub` → not that user);
`test_an_uninvited_account_is_refused_with_no_user_row`;
`test_domain_mapping_gives_viewer`;
`test_no_automatic_link_to_a_local_account_with_the_same_email`;
`test_jwks_is_refetched_once_on_an_unknown_kid`.

### 6.8 `backend/tests/test_orgs_migration.py` and `test_principal.py`

`test_each_account_tenant_becomes_an_organization_with_an_owner`;
`test_no_run_row_changes_its_tenant_id` (row hashes of `runs` and `run_events` before and after);
`test_migrated_organizations_have_self_approval_true`;
`test_bootstrap_accounts_become_platform_operators`;
`test_round_trip_keeps_counts_and_the_pending_approval_digest`;
`test_the_last_owner_cannot_be_removed_or_demoted`;
`test_a_user_in_two_organizations_sees_each_separately_after_switch`;
`test_no_active_organization_returns_409_with_the_list`;
`test_disabled_user_and_deleted_organization_are_refused`;
`test_auth_disabled_gives_the_local_owner_principal`.

### 6.9 Deployed scenarios

| Id | Scenario | Asserts |
|---|---|---|
| S33 | Two organizations | Every cross-read returns 404 or an empty list (with the row-level security spec) |
| S34 | Separation of duties | The initiator's approve returns 403 and the receiver gets nothing; a second user's approve sends exactly one request; the audit log has `run.started` and `approval.approved` with two different actors |
| S01, S02, S05, S14, S29 (W23) | Rewritten with an initiator account and an approver account | Every assert they have today, plus "the initiator's own decision returns 403" |

### 6.10 Mutants (M00 control first)

| Id | Mutant | Killed by |
|---|---|---|
| M01 | One route loses its `require` | `test_every_route_declares_a_permission_or_is_public` |
| M02 | `viewer` gains `runs:start` in the table | `test_matrix_cell`, `test_permission_document_is_current` |
| M03 | Participant check removed from `decide` | `test_the_initiator_cannot_approve_their_own_run`; S34; and the `CHECK` test still passes |
| M04 | `CHECK` dropped | `test_the_database_refuses_a_self_decision` |
| M05 | New checks placed after the digest checks | `test_a_refused_decision_does_not_reveal_the_digest` |
| M06 | Audit row written in a second transaction | `test_a_failed_change_writes_no_ok_row` |
| M07 | Chain hash omits `prev_hash` | `test_the_chain_verifies_and_a_changed_row_breaks_it` |
| M08 | Token compared in plain text / stored unhashed | `test_the_token_is_shown_once_and_only_its_hash_is_stored` |
| M09 | Nonce check removed | `test_a_wrong_nonce_is_refused` |
| M10 | State not deleted at use | `test_state_is_single_use` |
| M11 | Cross-organization resource returns 403 in place of 404 | `test_cross_organization_reads_return_not_found_or_empty` |
| M12 | Worker skips the initiator check | `test_a_run_fails_before_any_node_when_its_initiator_lost_access` |

## 7. Failure-mode analysis

| Failure | Result |
|---|---|
| A new route with no rule | CI fails (6.1) |
| A new route not in the matrix file | CI fails (6.2) |
| A one-person organization needs an approval | With `self_approval = false` nobody can approve; the run waits until expiry. The run panel shows "another person must decide". The owner can invite an approver or turn the flag on (audited) |
| The only approver started the run | Same |
| The initiator is removed while the run waits for approval | After the approval, the claim fails the run with the access message; the write is not sent |
| A role is reduced while a stream is open | The next tick ends the stream (2.5) |
| Two requests change the last two owners at once | The advisory lock orders them; the second is refused |
| The audit chain lock under load | One row lock for each audited change in an organization. Unmeasured; the load harness reruns after PR 4 |
| The OIDC provider is down | OIDC sign-in fails with 503; local sign-in (if enabled) and existing sessions work |
| The provider's signing key rotates | One JWKS refetch on an unknown `kid` |
| A token leaks | Revoke it; it holds at most `builder` permissions in one organization and cannot decide approvals or manage members |
| The previous build runs against the migrated database (rollback) | The rename of `auth_accounts` would break the previous build. So revision `users_and_tokens` also creates a **view** `auth_accounts` over `users` with the old four columns (a simple updatable view on PostgreSQL). It stays until the contract revision. On SQLite the previous build is not supported after the migration: downgrade first. State this in `docs/operations.md` |

## 8. Rollback

| Step | Rollback |
|---|---|
| PR 1 | Downgrade the two revisions (refused when an OIDC-only user exists; delete or convert such users first). The previous build works against the upgraded schema through the `auth_accounts` view |
| PR 2 | Code only |
| PR 3 | Downgrade `run_actors`: actor data is lost; approvals keep their status and digest. Runs that wait for approval stay valid |
| PR 4 | Downgrade drops the audit table: export it first (`scripts/audit_verify --export`) |
| PR 5, 6 | Code only; tokens and states stay in their tables, unused |
| PR 7 | Scenario code only |

## 9. Done when

- Every test of §6 passes on SQLite and PostgreSQL; mutants M01–M12 are killed and M00 passes.
- `grep -rn "Depends(tenant)\|auth.tenant\|auth.operator" backend/app` returns nothing.
- S33 and S34 pass; S01, S02, S05, S14, S29 pass with two accounts; no scenario source contains
  `self_approval`.
- `docs/permissions.md` is generated and current.
- `docs/gap-audit.md` §11 marks W09, W10, W12, W13, W23 done; `docs/resume-evidence.md` has the
  measured matrix size and the audit action count.
