# Dependency and authentication hardening

## Registration

`AUTH_REGISTRATION_MODE` is read at server startup. Restart the API after changing it.

| Mode | Policy |
| --- | --- |
| `closed` (default) | Only initial setup can create an account. Existing accounts and sessions remain valid. |
| `invite` | Every registration, including initial setup, requires an unused, unexpired invitation. |
| `open` | Explicit local-development choice; every valid registration may create a workspace. |

Invalid mode values fail startup. Unauthorized registrations receive the same 403 message before password hashing or checking email availability. The public status endpoint exposes setup/policy for the sign-in UI, not an account directory. A transaction protects initial ownership and invitation consumption; concurrent redemptions cannot create multiple accounts. Failed account creation rolls back token consumption.

From the repository root, generate a token for one person (shown once on stdout; send it privately):

```bash
.venv/bin/python -m scripts.auth_admin invite --email colleague@example.com --hours 24
```

Tokens contain 256 bits of randomness; only SHA-256 digests are stored. An optional email binding restricts redemption. Expiry is 1–168 hours, default 24. The command does not change the registration mode or create an account. Set `AUTH_REGISTRATION_MODE=invite` and restart to redeem. Switch back to `closed` afterward if desired. Newly created accounts still receive separate workspaces; invitations do not grant access to an existing workspace.

## Login budgets and recovery

Both normalized account email and ASGI client IP have persistent budgets. Unknown emails use the same budget and dummy password-hash path as known accounts. Before hashing, a short transaction reserves an attempt against both budgets. This prevents a concurrent burst from racing the counters. Requests denied during cooldown perform no PBKDF2 and receive HTTP 429 with `Retry-After`; they do not extend the cooldown. The application returns immediately rather than occupying worker threads with sleeps.

After attempts five through nine, cooldowns are 1, 2, 4, 8, and 16 seconds. The tenth allowed attempt sets a 900-second lockout. Budgets expire after 15 minutes without an admitted attempt. To accumulate ten failures, attempts must respect the earlier cooldowns. Ten immediate requests instead yield five password checks and five cooldown responses. The eleventh admitted-sequence request is denied before hashing, even if its password is correct. This policy was explicitly selected by the user.

A successful login clears that account's reservation and a same-account IP budget when no newer attempt has arrived. It cannot erase concurrent newer attempts or an IP budget containing attempts against other accounts. Resets are ordered by attempt admission: a later successful login clears older reservations even if an older hash finishes afterward, while a success admitted earlier cannot erase later reservations. This boundary is regression-tested. In-flight attempts count conservatively until success, so bursty legitimate logins may also receive cooldown responses. Expired budget rows are cleaned in bounded batches. Budget subjects are stored as hashes, not plaintext email/IP; hashes are pseudonymous, not anonymous.

A locked legitimate user can wait for `Retry-After` to expire, or ask the machine/database operator to run:

```bash
.venv/bin/python -m scripts.auth_admin unlock --email owner@example.com --ip 127.0.0.1
```

Use the actual ASGI peer address. Either argument can be used alone, but another blocking budget may remain. This command only clears budgets; it does not change passwords, grant sessions, or disable authentication. There is no public unlock endpoint. Operator database access is required.

The CLI loads `.env` without overriding existing environment variables and uses `DATABASE_URL`, or `DATA_DIR/workflows.db` (default `.data/workflows.db`). For an explicit database:

```bash
.venv/bin/python -m scripts.auth_admin --database-url sqlite:////absolute/path/workflows.db unlock --email owner@example.com --ip 127.0.0.1
```

### Client IP boundary

The application uses `request.client.host`; it never trusts raw `X-Forwarded-For` or `X-Real-IP`. A deployment may configure its ASGI server to accept client addresses only from explicitly trusted proxies that overwrite incoming forwarding headers. Never use an unrestricted proxy allowlist.

The bundled frontend proxy remains loopback-only and does not forward caller-supplied IP headers. Consequently its users share a loopback IP budget; per-account budgets still work. This is conservative for the current single-user local deployment. Public multi-user deployment needs a trusted reverse-proxy configuration that establishes the real client address. Application budgets mitigate guessing/hash work; they do not replace ingress connection/request limits for distributed denial-of-service protection. Existing authenticated sessions are not revoked by a login lockout.

## Dependencies and Chroma retirement

The live audit found four distinct Chroma advisories and a pip advisory (duplicate advisory aliases may appear in the JSON). [GHSA-f4j7-r4q5-qw2c](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c) records no patched version. Following the authorized fallback, Chroma was removed from installation requirements, adapter code, backend capabilities and indexing settings, and uninstalled from this virtual environment. No advisory exclusions were added.

Existing original documents and metadata are not deleted or silently migrated. Existing Chroma search/build requests fail with an instruction to rebuild with FAISS. Open the KB configuration, select FAISS and its `flat` index method, and rebuild; original uploads remain available. Authorized index cleanup after rebuild/deletion removes the retired per-segment directory directly, without importing Chroma, and preserves other index directories. No existing user KB was rebuilt during these tests.

pip is upgraded to 26.2.1. The patched frontend pins are `@cloudflare/vite-plugin` 1.56.0, `wrangler` 4.135.0, and required peer `@cloudflare/workers-types` 5.20260919.1. Plain `npm audit fix` could not update the old exact pins; compatible explicit upgrades resolved the dependency tree without force/legacy-peer-deps.

CI upgrades pip, installs the application and pip-audit 2.10.1, then runs `python -m pip_audit`. Frontend CI runs `npm audit --audit-level=high`, including build dependencies. Audits run on push/PR/manual dispatch and weekly Monday at 09:23 UTC. Registry failures also fail CI; findings are not suppressed. A hosted CI execution has not yet been verified.

## Verification

[Saved audit summary](security-audit-2026-09-19.json): pip-audit reports zero vulnerable packages among 129 installed packages; npm audit reports zero advisories at all severities. `pip check` reports no broken requirements. Audits describe known advisories at the time of execution, not a guarantee of vulnerability absence.

Frontend: 24 tests, typecheck, lint and production build pass. Backend: **288 tests pass**, with four upstream deprecation warnings. The isolated knowledge-service smoke passes ingestion, four search modes, real Retrieve → Agent → Response execution, rebuild/source retention, removal/revocation, and internal service authentication. `git diff --check` passes.

Authentication concurrency and recovery were exercised against real SQLite storage, including concurrent first registration, single-token redemption, cross-IP account lockout, per-IP spraying, restart persistence, and before/after-success attempt ordering. PostgreSQL-specific row/advisory locking is implemented but has **not** been live-tested in this environment. No hosted CI run, production deployment, commit or push was performed. The existing running local stack was not restarted; restart it to load these runtime changes.

The real service smoke requires installed Ollama `embeddinggemma:latest` and `llama3.1:latest` (optional `SMOKE_OLLAMA_MODEL` override). Its obsolete prose-only demo fixture could not satisfy the existing grounded contract; it now uses the same real llama3.1 model as the generation suite. The initial failure and corrected rerun are recorded rather than attributed to dependency incompatibility.

```bash
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python -m scripts.check_knowledge_services
.venv/bin/python -m pip_audit
cd frontend
npm audit --audit-level=high
npm test
npm run typecheck
npm run lint
npm run build
```
