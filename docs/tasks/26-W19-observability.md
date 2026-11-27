# W19 — Observability

| | |
|---|---|
| Order | 26 of 29 (Phase 3) |
| Closes | O3, O4, part of P5 |
| Depends on | W05 |
| Effort | M |
| Spec | `docs/gap-audit.md` W19. |

## Goal

OpenTelemetry traces for each run and a Prometheus metrics endpoint; a configurable run timeout and a per-node timeout.

## Files to touch

- `backend/app/observability.py` (tracer and meter setup; no content in spans: the journal allowlist rule)
- `backend/app/worker.py:19,61-66,237-241` (spans; `RELAY_RUN_TIMEOUT_SECONDS`, 10–3600, default 120)
- `backend/app/storage.py` (spans around claim and commits; counters for fence refusals and lease expiries)
- `backend/app/main.py` (`GET /metrics`, loopback or token protected)
- `backend/app/models.py:17-28` (`Node.timeout_seconds: int | None`, 1–600)
- `docs/grafana-dashboard.json`

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_one_run_makes_the_expected_span_tree` | In-memory exporter: run → claim → node × n → model call / tool call → commit |
| `test_spans_and_metrics_hold_no_content` | No attribute value contains the test's message, prompt or tool text |
| `test_lease_expiry_and_fence_refusal_counters_move` | Force each; scrape; the counter is up by one |
| `test_queue_depth_and_oldest_age_by_organization` | |
| `test_run_timeout_setting_bounds_and_effect` | |
| `test_node_timeout_fails_the_node_and_leaves_a_write_marker_in_place` | A timeout during a send is an uncertain write, as today |
| `test_metrics_match_sql_counts_under_the_load_smoke_cell` | The harness reads the same numbers from `/metrics` and from SQL |

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

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

A node timeout during a send must leave the marker (I2, I7).

## Done when

- Throughput with tracing on and off is measured with the load harness.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W19 (observability).
Read docs/tasks/26-W19-observability.md first, then every file it links.
No vendor SDK. No prompt, input or output text in any span or metric label.

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
