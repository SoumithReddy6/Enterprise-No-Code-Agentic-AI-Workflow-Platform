# Spec: signed retrieval resolution between the management and search services (audit W14, W16; gap S6, S8)

Status: ready to implement. Written 2026-11-16 against source `24e659c`.
Stage A (signature and binding) needs no other work item. Stage B (grants for each principal)
needs the `Principal` of `docs/specs/multi-tenancy-core.md`.

## 1. Current behavior

| Step | Code | Behavior |
|---|---|---|
| Retrieve | `backend/app/kb_gateway.py:33-43` | The gateway calls management `resolve` (tenant, `kb_id`), embeds the query, calls search `search` with `kb_id, version, segment_id, document_ids, query, query_vector, options`, then `verify` |
| Resolve | `backend/app/kb/management.py:356-365` | Returns the active version, its segment, and the live documents. The only input about the caller is the tenant string |
| Search authority | `backend/app/kb/search.py:223-229` | The permitted documents are the `document_ids` list **that the caller sends**. The segment must belong to the tenant **that the caller sends** (`search.py:98-104`) |
| RPC authentication | `backend/app/kb/rpc.py:22-32,78,86-87` | One shared symmetric key for both services, in a file next to the data. The tenant is a field of the request body |
| After the search | `kb_gateway.py:44-54` | `verify` on search (canonical text) and on management (documents still live) |
| Worker path | `backend/app/worker.py:213` | The worker calls the same `retrieve` with the run's tenant (`storage.py:447-450`) |
| Preview | `kb_gateway.py:145-155`; `search.py:307-317` | Same pattern for `preview` |

Consequences: any process that holds the shared key can search any tenant's segment with any
document list. The search service cannot tell an authorized resolution from an invented one.
There is no principal, so a permission below the tenant cannot be enforced (gap S8).

## 2. Target design

### 2.1 Roles of the three parties

- **Management service**: the only authority on "which principal may read which documents of
  which knowledge base". It **signs** each resolution with an Ed25519 private key.
- **Search service**: holds only public keys. It runs a search or a preview **only** when the
  request carries a valid resolution token that matches the request exactly.
- **Gateway** (API process and worker): carries the token from management to search. It cannot
  make one. The shared service key (`rpc.py`) stays as transport authentication; it no longer
  grants any read by itself.

Ed25519 comes from `cryptography` (already in `backend/requirements.txt`). No new dependency.

### 2.2 What is signed

Claims, serialized as canonical JSON: `json.dumps(claims, sort_keys=True, separators=(',', ':'), ensure_ascii=True)`.

| Claim | Type | Value |
|---|---|---|
| `v` | int | `1` |
| `kid` | str | id of the signing key (16 hex characters: the first 8 bytes of SHA-256 of the raw public key) |
| `iss` | str | `"kb-management"` |
| `aud` | str | `"kb-search"` |
| `purpose` | str | `"search"` or `"preview"` |
| `tenant` | str | organization id |
| `principal` | str | `"<type>:<id>"`, type one of `user`, `service`, `tenant` (Stage A always uses `tenant:<tenant>`) |
| `kb_id`, `segment_id` | str | from the resolution |
| `version` | int | from the resolution |
| `docs` | str | SHA-256 hex of `"\n".join(sorted(document_ids))` (the empty list hashes the empty string) |
| `req` | str | SHA-256 hex of the request binding (see 2.3) |
| `iat`, `exp` | int | seconds on the management host's clock; `exp = iat + 60` |
| `jti` | str | 32 hex characters, random. For logs and audit only; it is not stored |

Token: `base64url(claims_json) + "." + base64url(signature)`, no padding. The signature is over the
exact `claims_json` bytes. The search service verifies the signature over the received bytes
**before** it parses them, then parses, then checks every claim. It never re-serializes.

### 2.3 Request binding (replay protection)

The token is valid for **one request content**, not only for one scope.

- `purpose = "search"`: `req = sha256(canonical({"query": query, "options": options}))` where
  `options` is the validated `RetrievalOptions` dump that the gateway sends (`kb_gateway.py:36`).
  The query vector is not in the binding (it is derived from the query; the search service
  cannot check that derivation, and a wrong vector reads only permitted documents).
- `purpose = "preview"`: `req = sha256(canonical({"document_id": document_id, "limit": limit}))`.

So a captured token can be used again only for the same query, the same options, the same
documents, for at most 60 seconds, and it returns what its first use returned. It cannot be
turned into another read.

No `jti` store. Reason: the search is a read; an exact replay inside 60 s gives no data that the
first call did not give, and a store would make the search service stateful and add a failure
mode. Revocation inside the 60 s is covered by 2.6.

### 2.4 Checks in the search service

New module `backend/app/kb/resolution.py` with `sign(private_key, claims)` and
`verify(token, public_keys, now)`. `Search._search` and `Search._preview` call
`self._authorize(tenant, p, purpose)` as their **first** statement. It raises
`PermissionError('Resolution refused: <reason code>')` (the RPC layer maps it to HTTP 403 with the
reason code only). Order of checks and reason codes:

| # | Check | Reason code |
|---|---|---|
| 1 | `p['resolution']` is a str of at most 4,096 characters with exactly one `.` | `missing` |
| 2 | Both parts are valid base64url | `malformed` |
| 3 | The claims parse as a JSON object with exactly the 14 keys of 2.2 and the right types | `malformed` |
| 4 | `v == 1`, `iss == "kb-management"`, `aud == "kb-search"` | `wrong_audience` |
| 5 | `kid` is in the configured public keys | `unknown_key` |
| 6 | Ed25519 signature valid for the received claims bytes | `bad_signature` |
| 7 | `iat - 5 <= now <= exp` and `exp - iat == 60` | `expired` |
| 8 | `purpose` equals the action | `wrong_purpose` |
| 9 | `tenant` equals the RPC tenant | `wrong_tenant` |
| 10 | `kb_id`, `segment_id`, `version` equal the request | `wrong_scope` |
| 11 | `docs` equals the hash of the request's `document_ids` | `wrong_documents` |
| 12 | `req` equals the hash computed from the request (2.3) | `wrong_request` |

(The signature check is number 6 in the table for reading order; in code do 1, 2, 5, 6 on the raw
bytes, then 3, 4, 7–12.) After `_authorize`, the existing checks stay: `_segment(…)` with the
tenant (`search.py:227`), the document pre-filter (`search.py:229`), the second `_segment` check
after ranking (`search.py:278-279`).

The pre-filter stays a **pre-filter**: the SQL conditions at `search.py:229` are applied before
ranking, and the BM25 statistics (`search.py:244-249`) are computed over the permitted chunks
only. This is an invariant of this spec (test P05).

### 2.5 Keys, storage and rotation

| Item | Management service | Search service |
|---|---|---|
| Production | `KB_RESOLUTION_PRIVATE_KEY`: base64 of the 32-byte Ed25519 seed | `KB_RESOLUTION_PUBLIC_KEYS`: JSON object `{"<kid>": "<base64 raw public key>", …}` |
| Development (no setting) | Creates `DATA_DIR/kb-resolution.key` (mode 0600), the pattern of `rpc.py:26-32` | Reads `DATA_DIR/kb-resolution.pub.json`, which management writes beside the private key |
| What it must never have | — | The private key. A test starts the search app with only public keys in its environment |

Rotation, with no downtime:

1. Make a new key pair. Add the new public key to `KB_RESOLUTION_PUBLIC_KEYS` (two entries).
   Restart search.
2. Set `KB_RESOLUTION_PRIVATE_KEY` to the new key. Restart management. New tokens carry the new
   `kid`.
3. Wait at least 2 minutes (twice the token lifetime). Remove the old public key. Restart search.

Emergency revocation of a key: remove its public key from search and restart; every token signed
with it fails with `unknown_key` at once.

A command `python -m scripts.kb_resolution_keys new` prints a new seed, its `kid` and the public
key entry. It writes nothing.

### 2.6 Revocation inside the token lifetime

The gateway's final management `verify` call (`kb_gateway.py:53`) becomes principal-aware in
Stage B: it sends the principal, and management checks the grant again and the documents again.
So a grant removed after the token was signed makes the retrieval fail at `verify`, before any
passage reaches a model or a response. The run-end check of sources (`README.md:348`) uses the
same call.

### 2.7 Grants (Stage B)

Grants live in the knowledge base's state document, where all its other state is
(`kb/management.py:202`): `state['access'] = {'restricted': bool, 'grants': [{'principal': 'user:<id>' | 'service:<id>' | 'role:<role>', 'permission': 'search' | 'manage'}]}`.
Default for existing and new bases: `{'restricted': False, 'grants': []}` (every member of the
organization may search).

`resolve` and `verify` take `p['principal'] = {'key': 'user:<id>', 'roles': ['builder']}`:

- `restricted == False`: permitted.
- `restricted == True`: permitted when a grant with permission `search` or `manage` matches the
  principal key or one of its roles; the roles `owner` and `admin` are always permitted.
- Not permitted: raise `KeyError('Knowledge base not found')` — the same answer as for a base
  that does not exist (`management.py:175`), so its existence is not disclosed.

New management actions `grant` and `revoke` (payload: `kb_id, principal, permission`), and
`set_restricted` (`kb_id, restricted`). Gateway routes: `PUT /api/knowledge-bases/{kb_id}/access`
with permission `kb:write`.

Document-level grants are **not** in this spec. The token already carries a document list, so a
later change can narrow the list for each principal with no change to the search service.

### 2.8 Rollout setting

`KB_RESOLUTION_ENFORCE` on the search service: `off` (ignore the token), `log` (check; on failure
journal `kb.search.resolution_refused` with the reason code and continue), `on` (refuse). Default
`on` after commit 6 below. Any other value: startup error.

## 3. Rejected alternatives

| Alternative | Why not |
|---|---|
| HMAC with the shared service key | The gateway holds that key, so it could mint a token. The point is that only management can |
| The search service reads the management database | Breaks the rule "services never access another service's tables" (`rpc.py:1`) and couples deploys |
| The search service calls management for each search | One more RPC on the hot path; management takes a global lock on every call (`management.py:149-151`) |
| JWT with a library | The token has one issuer, one audience, one algorithm. A fixed format with one algorithm removes the algorithm-confusion class of bugs. No new dependency |
| A `jti` store for one-time use | See 2.3 |
| A longer token lifetime with caching in the gateway | Makes revocation slower; the resolve call is cheap next to the embedding call |
| Post-filtering results after ranking | Leaks through scores and counts, and can return fewer than `top_k` |

## 4. Invariants that must not break

- I1–I8 of `docs/gap-audit.md` §6 are not touched (no core storage change).
- Evidence rule: a source must be valid when the answer is published (`README.md:348`). The
  `verify` calls stay, and in Stage B they also check the grant.
- Pre-filter rule (2.4): forbidden chunks never enter ranking or statistics.
- Fail closed: any token problem refuses the request; no fallback to "tenant only".
- The token and the private key never appear in a journal line (`observability.py:18` allowlist
  has no such field; add the reason code under the existing `reason_code` field only).

## 5. Change list, in commit order

Stage A:

| # | Commit | Files |
|---|---|---|
| 1 | Tests first (red): R01–R18 | `backend/tests/test_kb_resolution.py` (new) |
| 2 | `resolution.py`: `sign`, `verify`, key loading, `kid` | `backend/app/kb/resolution.py` (new) |
| 3 | Management signs: `resolve` takes `purpose` and `binding` (`query`, `options` or `document_id`, `limit`) and returns `resolution` | `backend/app/kb/management.py`, `management_app.py` |
| 4 | Gateway sends the binding to `resolve` and the token to `search` and `preview` | `backend/app/kb_gateway.py:33-43,145-155` |
| 5 | Search checks in mode `log` (default `log` in this commit) | `backend/app/kb/search.py`, `search_app.py`, `rpc.py` (map `PermissionError` to 403) |
| 6 | Default `on`; key command; docs | `search_app.py`, `scripts/kb_resolution_keys.py`, `docs/operations.md`, `backend/app/kb/contracts.md` |
| 7 | Mutants and evidence | `evals/results/w14-resolution-<date>/` |

Stage B (after the `Principal` exists):

| # | Commit | Files |
|---|---|---|
| 8 | Tests first (red): P01–P09 | `backend/tests/test_kb_grants.py` (new) |
| 9 | `access` in the state, with a default for existing rows on read; `grant`, `revoke`, `set_restricted` | `management.py` |
| 10 | `resolve` and `verify` take the principal; the token's `principal` claim carries it | `management.py`, `kb_gateway.py` |
| 11 | The worker passes the run's initiator as the principal | `worker.py:213-217`, `kb_gateway.py` |
| 12 | Submission and save refuse a workflow that names a base the principal cannot search | `platform_validation.py:29-34` (it already calls `resolve`; pass the principal) |
| 13 | Routes for access; scenario S35 | `kb_gateway.py`, `scripts/scenarios/` |

## 6. Acceptance tests

### 6.1 `backend/tests/test_kb_resolution.py` (Stage A)

Build real `Management` and `Search` objects on SQLite, as `backend/tests/test_kb_services_integration.py` does.

| Id | Test name | Asserts |
|---|---|---|
| R01 | `test_a_search_with_a_valid_resolution_returns_results` | Control |
| R02 | `test_a_search_without_a_resolution_is_refused` | 403, reason `missing` |
| R03 | `test_a_changed_claim_byte_fails_the_signature` | Flip one byte of the claims part: `bad_signature` |
| R04 | `test_a_token_signed_by_another_key_is_refused` | `unknown_key` when its `kid` is not configured; `bad_signature` when the `kid` is reused |
| R05 | `test_an_expired_token_is_refused` | Clock at `exp + 1`: `expired` |
| R06 | `test_a_token_from_the_future_is_refused` | Clock at `iat - 6`: `expired` |
| R07 | `test_a_token_with_a_longer_lifetime_is_refused` | `exp - iat == 61`: `expired` |
| R08 | `test_a_search_token_cannot_authorize_a_preview` | `wrong_purpose` (and the reverse) |
| R09 | `test_a_token_for_another_tenant_is_refused` | `wrong_tenant` |
| R10 | `test_a_token_for_another_segment_or_version_is_refused` | `wrong_scope` (three cases: `kb_id`, `segment_id`, `version`) |
| R11 | `test_an_added_document_id_is_refused` | Token for `[d1]`, request `[d1, d2]`: `wrong_documents` |
| R12 | `test_a_removed_document_id_is_refused` | Token for `[d1, d2]`, request `[d1]`: `wrong_documents` (the list must be exact) |
| R13 | `test_a_token_cannot_be_used_for_another_query` | Same scope, other query text: `wrong_request` |
| R14 | `test_a_token_cannot_be_used_with_other_options` | `top_k` changed: `wrong_request` |
| R15 | `test_an_exact_replay_returns_the_same_result_and_nothing_more` | The same request twice: equal results |
| R16 | `test_two_public_keys_are_accepted_during_rotation_and_one_after` | Tokens of key 1 and key 2 pass with both configured; key 1's token gives `unknown_key` after its removal |
| R17 | `test_the_search_app_starts_with_public_keys_only` | `search_app` with `KB_RESOLUTION_PUBLIC_KEYS` set and no private key in the environment or `DATA_DIR` starts and verifies; the module has no code path that loads a private key (assert `resolution.load_private_key` is not imported by `search.py` or `search_app.py`) |
| R18 | `test_enforce_modes` | `off`: a missing token passes; `log`: passes and journals `reason_code='missing'`; `on`: 403; another value: startup error |

### 6.2 `backend/tests/test_kb_grants.py` (Stage B)

| Id | Test name | Asserts |
|---|---|---|
| P01 | `test_an_unrestricted_base_is_searchable_by_every_member` | Control; existing state rows with no `access` key read as unrestricted |
| P02 | `test_a_restricted_base_refuses_a_member_without_a_grant` | `resolve` raises `KeyError`; the gateway returns 404, the same body as for an unknown id |
| P03 | `test_a_user_grant_and_a_role_grant_both_permit` | Two cases |
| P04 | `test_owner_and_admin_are_always_permitted` | |
| P05 | `test_scores_do_not_change_when_a_forbidden_document_is_added` | Search for user A over `[d1]`; add document `d2` that A cannot read and that shares the query terms; A's result ids **and scores** are equal to the first search |
| P06 | `test_a_grant_revoked_after_signing_fails_at_verify` | Sign; revoke; search succeeds at the search service (token still valid); the gateway `retrieve` raises at the management `verify` |
| P07 | `test_a_run_retrieves_with_its_initiators_permissions` | A run started by a user with no grant fails at the Retrieve node with the standard "unavailable" message; the same workflow started by a granted user succeeds |
| P08 | `test_a_workflow_that_names_a_forbidden_base_is_refused_at_submission` | 422 with the existing message of `platform_validation.py:34` |
| P09 | `test_access_changes_need_kb_write` | A Viewer's `PUT …/access` returns 403 |

### 6.3 Existing tests that must pass

`test_kb_search.py`, `test_kb_management.py`, `test_kb_services_integration.py`,
`test_kb_workflow.py`, `test_response_source_boundary.py`, `test_runtime_answerability.py`,
`scripts/check_knowledge_services.py`, `scripts/check_kb_postgres.py`. Their helpers that call
`Search.call('search', …)` directly must obtain a token from the test's `Management` object;
change the helpers, not the assertions.

### 6.4 Mutants (M00 control first)

| Id | Mutant | Killed by |
|---|---|---|
| M01 | Skip the signature check | R03, R04 |
| M02 | Skip the expiry check | R05 |
| M03 | Compare `docs` as a subset | R12 |
| M04 | Skip `req` | R13, R14 |
| M05 | Skip `purpose` | R08 |
| M06 | Skip `tenant` | R09 |
| M07 | Parse the claims, re-serialize, then verify | A test that sends claims with a different key order and a valid signature over those bytes must **pass**, and one with re-ordered bytes and the old signature must fail (`test_verification_uses_the_received_bytes`) |
| M08 | Apply the document filter after ranking | P05 |
| M09 | `restricted` ignored in `verify` | P06 |

## 7. Failure-mode analysis

| Failure | Result |
|---|---|
| Clock skew between management and search hosts over 5 s behind or 60 s ahead | Tokens fail with `expired`. Retrieval stops (fail closed). The journal shows the reason code. Fix the clock; do not widen the window |
| Search restarted with no public key configured | Production: startup error. Development: reads the file |
| Management restarted with a new key before search knows it | `unknown_key` until step 1 of the rotation is done. This is why the rotation order is fixed |
| The private key leaks | Rotate (2.5) and remove the old public key at once. Tokens are for one tenant and one request; an attacker with the key and the shared RPC key can read any segment until the removal |
| The gateway is compromised | It can read what management will sign for the principals it can name. In Stage B the principal comes from the session or the run row, so this equals "the API process is compromised" |
| A token is logged by mistake | It expires in 60 s and binds one request. Still: the journal allowlist test (`test_observability.py`) must show no `resolution` field |
| Management is down | `resolve` fails; retrieval fails with the existing "service unavailable" error (`rpc.py:61`) |

## 8. Rollback

- `KB_RESOLUTION_ENFORCE=log` or `off` on the search service restores the old behavior with no
  deploy. The gateway and management can keep sending tokens.
- Stage B: the `access` key is optional in the state; old code ignores it. To roll back the code,
  first set every base to unrestricted (a one-line management script), because old code would
  serve a restricted base to every member.

## 9. Done when

- R01–R18 and P01–P09 pass; mutants M01–M09 are killed and M00 passes.
- `grep -n "document_ids" backend/app/kb/search.py` shows no path that reaches the pre-filter
  before `_authorize`.
- Scenario S35 passes in the deployed gate.
- `docs/gap-audit.md` §11 marks W14 done (and the knowledge-RPC part of W16).
