# W14 — Signed retrieval resolution and grants

| | |
|---|---|
| Order | 21 of 29 (Phase 2) |
| Closes | S8, part of S6 |
| Depends on | Stage A: nothing. Stage B: W10 |
| Effort | M |
| Spec | `docs/specs/signed-retrieval-resolution.md`. |

## Goal

The search service runs a search only with a resolution signed by the management service and bound to the exact request; knowledge bases can be restricted by grant; a run retrieves as its initiator.

## Files to touch

Spec §5: Stage A commits 1–7 (`backend/app/kb/resolution.py` new, `management.py`, `search.py`, `rpc.py`, `kb_gateway.py`, `scripts/kb_resolution_keys.py`); Stage B commits 8–13 (`management.py` access state, `worker.py:213-217`, `platform_validation.py:29-34`, routes, S35).

## Tests to write first

Spec §6.1 (R01–R18), §6.2 (P01–P09). Mutants M01–M09.

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
.venv/bin/python -m scripts.check_knowledge_services
```

## Invariants

The pre-filter rule: forbidden chunks never enter ranking or BM25 statistics (`kb/search.py:229,244-249`). The publication rule: a source must be valid when the answer is published (`README.md:348`). I1–I8 are not touched.

## Done when

- Spec §9. Stage A can be done before W09; Stage B needs the Principal.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W14 (signed retrieval resolution and grants).
Read docs/tasks/21-W14-signed-retrieval-resolution-and-grants.md first, then every file it links.
Do Stage A in one session and Stage B in another. In Stage A the principal claim is always tenant:<tenant>.

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
