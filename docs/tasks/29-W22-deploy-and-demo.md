# W22 — Deploy and demo

| | |
|---|---|
| Order | 29 of 29 (Phase 5) |
| Closes | X1, X2 |
| Depends on | W09–W13, W19 |
| Effort | M |
| Spec | `docs/gap-audit.md` W22. |

## Goal

Container images, a compose stack that passes the deployed gate, one small cloud instance with fixture receivers, a 3-minute video and a public design document.

## Files to touch

- `Dockerfile` (Python services, multi-stage), `frontend/Dockerfile`
- `compose.yaml` (PostgreSQL with the three roles, API, 2 workers, knowledge services, fixture receiver, local OIDC issuer, Prometheus, Grafana)
- `.github/workflows/ci.yml` (build images)
- `docs/design.md`, `README.md` (link and screenshots)

## Tests to write first

| Check | Asserts |
|---|---|
| `docker compose up --wait` on a clean machine | `/api/ready` returns `ready` |
| `scripts.check_production_scenarios --against <compose url>` (new flag) | The gate passes against the stack |
| Image scan | No high or critical finding without a recorded exception |
| Public instance | Uses fixture receivers only; each organization has a token limit; registration is by invite; the first account is created before the address is public (`docs/defects.md`, design hazards) |

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

Cloud steps need network access and an account: ask first.

## Invariants

The public instance must never hold a real write credential.

## Done when

- A link, a video and the design document are in the README; cold-start time and image sizes are recorded.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W22 (deploy and demo).
Read docs/tasks/29-W22-deploy-and-demo.md first, then every file it links.
Do the images and the compose stack first. Stop before any cloud step and ask.

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
