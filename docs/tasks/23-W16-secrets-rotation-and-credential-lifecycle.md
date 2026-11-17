# W16 — Secrets rotation and credential lifecycle

| | |
|---|---|
| Order | 23 of 29 (Phase 2) |
| Closes | S6 |
| Depends on | W10, W13 |
| Effort | M |
| Spec | `docs/gap-audit.md` W16; gap S6. |

## Goal

Encryption keys have ids and can be rotated; credentials and connections can be rotated and deleted, with a reference check.

## Files to touch

- `backend/app/storage.py:130-138,250,378-381,455-470` (`MultiFernet` from `CREDENTIAL_ENCRYPTION_KEYS`, a comma list, newest first; the single-key setting stays valid)
- `scripts/rotate_encryption_key.py` (new): re-encrypt `credentials.encrypted`, `tool_connections.encrypted`, `run_approvals.execution`, in batches, resumable
- `backend/app/main.py`, `tool_api.py`: `PUT /api/credentials/{id}` (rotate secret), `DELETE /api/credentials/{id}`, `DELETE /api/connections/{id}`
- `backend/app/storage.py`: a reference check over saved workflows and allowed models

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_old_ciphertext_reads_with_two_keys_configured` | |
| `test_rotation_reencrypts_every_row_and_the_old_key_alone_fails` | After the script, a store with only the new key reads all; one with only the old key reads none |
| `test_rotation_is_resumable` | Stop after the first batch; run again; all rows done once |
| `test_a_pending_approval_survives_rotation` | Its plan decrypts and its digest is unchanged (I6) |
| `test_a_credential_in_use_cannot_be_deleted` | 409 with the list of workflows and models that name it |
| `test_delete_and_rotate_need_credentials_write_and_are_audited` | |
| `test_a_run_in_flight_fails_closed_when_its_credential_is_deleted` | The existing "Credential not found" message (`storage.py:468`) |

## Commands to verify

```bash
# 1. New tests fail on the parent commit (show the output), then pass.
# 2. Full backend suite under five hash seeds:
for s in 0 1 2 3 4; do PYTHONHASHSEED=$s .venv/bin/python -m pytest backend/tests -q || break; done
# 3. Deployed scenario gate (about 5 minutes, local only):
.venv/bin/python -u -m scripts.check_production_scenarios --all --out <scratch>
# 4. Frontend, when a frontend or API contract file changed:
(cd frontend && npm test && npm run typecheck && npm run lint && npm run build)
```
```bash
# PostgreSQL checks. Use a throwaway container, never the user's database. Ask first: more than 5 minutes.
.venv/bin/python -u -m scripts.check_milestone_storage --out <scratch> --checks \
  postgres-migrations postgres-four-workers postgres-stale-lease postgres-sigkill-loop \
  postgres-accounting-before-commit postgres-accounting-after-commit postgres-parallel-sigkill \
  postgres-repeat-sigkill populated-downgrade
```

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

I6: rotation changes the ciphertext of `run_approvals.execution`, never `payload` or `digest`.

## Done when

- The rotation procedure is in `docs/operations.md` with the measured time for 10,000 rows.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W16 (secrets rotation and credential lifecycle).
Read docs/tasks/23-W16-secrets-rotation-and-credential-lifecycle.md first, then every file it links.
The signed knowledge RPC part of the audit item is card W14; do not repeat it here.

Rules for this session:
- Read the card, the linked spec and the cited code first. Do not make design decisions that the spec already made. If the spec and the code disagree, stop and ask.
- Write the tests first. Show that each fails on the parent commit for the reason the card states.
- Never weaken an existing invariant (I1-I8 in docs/gap-audit.md section 6) or an existing test to make a new test pass. If they conflict, stop and ask.
- Run mutants only in scratch copies, with an M00 no-change control and a guard that fails when no test ran.
- Ask before any run longer than 5 minutes, any network access, or any API key use. Use a throwaway PostgreSQL container, never the user's database or .data directory. Never use git stash.
- Give exact git add and git commit commands; no Co-Authored-By line; the user commits.
- After the gate passes: update the status table in docs/gap-audit.md section 11, add only verified numbers to docs/resume-evidence.md, and remove the xfail marker of a defect test only in the commit that fixes it.
- Write replies in about 80% Simplified Technical English.
```
