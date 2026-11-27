# W05 — Load baseline harness

| | |
|---|---|
| Order | 6 of 29 (Phase 0) |
| Closes | P1 |
| Depends on | nothing |
| Effort | M |
| Spec | `docs/specs/load-baseline-harness.md`. |

## Goal

A deployed-process load harness and the first throughput, latency and write-rate numbers, on unchanged source.

## Files to touch

- `scripts/load/` (new: `fixtures.py`, `workflows.py`, `stack.py`, `measure.py`, `run.py`, `report.py`)
- `backend/tests/test_load_harness.py`
- `evals/results/load-baseline-<date>/`
- No file under `backend/app` in phase A

## Tests to write first

H01–H08 of the spec, §6.

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
.venv/bin/python -m scripts.load.run --db sqlite,postgres --workers 1,4,16 --workflow w3,w20,loop50 --latency-ms 0,200 --repeats 3 --out evals/results/load-baseline-$(date +%F)
```
The full matrix takes more than 20 minutes: ask first.

## Invariants

No source change. The harness must refuse the user's data directory and database (spec §4).

## Done when

- Spec §10.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W05 (load baseline harness).
Read docs/tasks/06-W05-load-baseline-harness.md first, then every file it links.
A first version of the harness and a baseline may already exist under scripts/load and evals/results/load-baseline-*; read them first and extend, do not start again.

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
