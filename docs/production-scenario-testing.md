# Production workflow scenario gate

This campaign is a reusable release check across phases. It deploys an authenticated FastAPI process and a separate worker process against an isolated temporary SQLite database. Jira and delivery services are local HTTP fixtures, reached through the actual ToolService HTTP client. Nothing is run against the signed-in workspace or external write accounts.

## Run it

```bash
# Deterministic deployed API/worker scenarios; no Ollama, Docker, or API credits needed.
.venv/bin/python -u -m scripts.check_production_scenarios

# Add the entire backend test suite and frontend tests/typecheck/lint/build.
.venv/bin/python -u -m scripts.check_production_scenarios --all

# Also index the real local evaluation corpus, run Ollama and Docker workflows,
# and run the existing read-only public HTTP scenario.
.venv/bin/python -u -m scripts.check_production_scenarios --all --live-models \
  --out evals/results/production-release
```

Choose a new output directory for each retained release record. The runner preserves any pre-existing workflows-latest.json when running the live campaign. It never stashes, resets, or switches Git worktrees. Live-model requirements are the same as scripts/check_workflows.py; an unavailable dependency fails that stage rather than silently skipping it.

Exit zero means every requested scenario/stage passed. Failures, missing results, and source changes during execution fail the gate. Known defects remain red: no xfail, no --force, no threshold adjustment. A manual GitHub Actions workflow (`Production workflow scenarios`) runs the deterministic deployment plus repository checks and uploads evidence even on failure. It is still manual. The A3/A4 defects it was waiting on closed on 2026-09-29, so it can now be promoted to a required pull-request check.

## Coverage catalogue

| ID | Production use case | Guarantee tested |
| --- | --- | --- |
| S01 | Jira ticket batch sends reviewed replies | Approval before each send, lease released, digest mismatch refused, duplicate approval idempotent |
| S02 | Operator denies a customer reply batch | No delivery; terminal rejection prevents resume |
| S03 | Delivery succeeds remotely but response is lost | continue cannot conceal uncertainty or run downstream |
| S04 | Same incident with a fallback branch | route cannot conceal uncertainty |
| S05 | Worker restarted between approvals | Completed item not repeated; next approval still works |
| S15 | Worker exits immediately after item SQL commit | Real lease expiry recovers remaining items without repeating completed write |
| S16 | Worker killed after remote receipt, before response | Genuinely uncertain write never automatically replays |
| S06 | Vendor read returns a transient outage | Bounded retries and attempt events |
| S07 | Same transient outage inside a batch | A3 retry policy composes with A4 |
| S08 | Error branch reads successful output | Invalid recovery binding rejected before execution |
| S09 | Batch exceeds max_items | Incompleteness propagated to run result |
| S10 | Connection targets local infrastructure | SSRF denied outside the explicit test fixture |
| S11 | Anonymous access and unwanted registration | Authentication and default closed registration enforced |
| S12 | Another signed-in tenant requests a run | Run and connection isolation |
| S13 | Two browser tabs edit one workflow | Stale update rejected |
| S14 | Connection destination changes after review | Reviewed write does not go to changed endpoint |
| S17 | Purchases above $1,000 go to the approval desk | A typed `gt` condition on a JSON field decides whether the external write is sent: 1200 and 1000.01 are sent, 80 and 1000.00 are not, and the receiver sees exactly those two |
| S18 | A threshold is typed as words | Invalid comparison is rejected at validation with its reason; no run is created, nothing is sent |
| S19 | The amount arrives as text that is not a number | The run fails naming the value; neither branch runs, nothing is sent |
| L01 | Two tickets processed by real Ollama agents (optional) | Model usage survives into durable run events |
| M01 | One agent performs a write, then asks approval for another | Approved continuation completes without repeating the first write; deterministic Worker/Store probe |

The optional existing live campaign adds grounded retrieval, query generation, attached tools, specialist agents, condition branches, typed routing on an extraction agent's JSON (7b/7c), memory, extraction, chained agents, triggers, invalid-KB rejection, and consent enforcement. Model-quality assertions remain that campaign's own assertions; functional success is not a substitute for the 53-question grounding quality gate.

## Evidence and realism boundaries

- Real processes, sockets, authentication, leases, SQL persistence, approval endpoints, actual tool transport, and SIGKILL/abrupt process exit.
- S01–S19 and L01 use no API/worker function stubs. M01 is separately labelled: it exercises real Worker/Store logic with scripted model and delivery fixtures, not deployed HTTP. One test-only failpoint exits after the real item SQL commit to deterministically hit the settled-item crash boundary.
- DNS substitution permits only scenario-provider.invalid at the fixture's allocated port. The helper is never imported by product code. Other connection targets use ordinary SSRF checks.
- The receiver records requests before dropping the response to simulate an ambiguous external outcome.
- A second synthetic tenant is seeded as setup; its access tests use real login and HTTP authorization. This is not an invitation/registration conformance test.
- SQLite is exercised here. PostgreSQL row locking and multiple concurrently claiming workers still need a dedicated real-Postgres profile; this suite does not certify them.
- No real Jira, SMTP, or other external-account writes. Real provider sandbox conformance should be a separately configured opt-in profile with disposable accounts.
- This campaign checks frontend unit tests/build, not browser layout. The separate
  [Chromium layout gate](browser-layout-testing.md) checks incomplete-run scrolling
  at desktop and tablet widths; it does not certify general visual usability.
- Security assertions are bounded regression checks, not a penetration test or claim that all vulnerabilities are absent.

Artifacts include report.json with HEAD, dirty-tree status, source hashes, timing, stage results and strict pass/fail; runs.json with persisted histories; receiver request records; and separate process/build logs. Inputs are synthetic, but approval records include their exact outgoing payload. Do not reuse real credentials or sensitive documents in these fixtures.

## Policy for each new phase

Add at least one business workflow and its negative/recovery counterpart to this catalogue before calling the phase complete. Check what the receiver/database actually observed, not only run.status. Include cross-phase scenarios: retries + iteration, approvals + iteration, budgets + recovery, retrieval + generation, and later tenant permissions + nested execution. Retain failing artifacts and link them to defect IDs.

Upcoming profiles: durable action/token budgets across resume; complete typed results; stopping after persisted item errors; concurrent approvals and two-worker fencing; PostgreSQL conformance; knowledge-service loss and ingestion cleanup; approval expiry; restore/upgrade migration checks. These are planned coverage, not passing scenarios.

## Latest audit

The September 26 campaign was intentionally red: 13 of 18 scenarios passed; S07, S08, S09, L01 and M01 failed. See [Batch 1 audit](reviews/batch1-production-audit-2026-09-26.md) for reproductions, two hash-order test flakes, and coverage limits.

As of 2026-09-29 (commit `4b1cb94`) all 18 scenarios pass, with the backend suite, frontend checks and live-model campaign: 24 of 24 stages, recorded in `evals/results/loop-storage-production/report.json`. The fixes and their independent audits are in [reviews](reviews/): S07 ([audit](reviews/s07-audit-2026-09-27.md), [completion](reviews/s07-complete-audit-2026-09-27.md)), [telemetry abort](reviews/telemetry-audit-2026-09-28.md), [S08](reviews/s08-audit-2026-09-28.md), [S09](reviews/s09-audit-2026-09-29.md) and [loop storage](reviews/loop-storage-audit-2026-09-29.md). One backend test, `test_hung_probes_have_deadlines_and_no_unbounded_db_tasks`, was timing-sensitive and failed twice under machine load; `d2816f7` replaced it with a scheduler-independent version.

On 2026-10-06, A5.1 (typed conditions) was run on `64e03f7` plus its working-tree changes, with `--all --live-models`: **27 of 27 stages** passed, including S17–S19 and all 17 live workflows, with no source change during execution. In live 7b/7c, `llama3.1` extracted `{"amount": 1350.0}` and `{"amount": 24.0}` from free-text orders, and the typed condition sent them to review and auto-approval respectively. Evidence: `evals/results/a5.1-production-2026-10-06/report.json`.
