# Operational observability implementation plan

**Goal:** Implement the user's four September 20 requirements: dependency-aware readiness, HTTP/run correlation, safe structured journals, and operator SQL metrics.
**Architecture:** Shared journal/context middleware; durable worker heartbeats; timeout-bounded aggregate readiness; SQL aggregates over run columns and event usage. Preserve liveness, existing API shapes, and prior uncommitted work.
**Execution:** Inline implementation, no commits or deployment. User supplied the detailed specification and requested implementation.

- [x] Readiness tests first: healthy, DB unavailable, each KB unavailable, stale/idle worker, short timeout. Add readiness module and worker heartbeat table; monitor running and idle workers. Limit outstanding synchronous probes to prevent timeout thread accumulation.
- [x] Correlation tests first: inbound/generated IDs, error responses, concurrent isolation, request/run journal matching. Add ASGI middleware, contextvars, header validation, safe route-template request logs, persist submission ID, reset worker context after each run, forward context through internal RPC.
- [x] Structured log tests first: authentication outcomes, lease expiry/acquisition, KB failures, retry events; no passwords/tokens/prompts/passages/emails. Shared journal accepts only allowlisted metadata, no exception strings or arbitrary inputs. Log security outcomes after durable changes.
- [x] Metrics tests first: operator-only including auth-disabled mode, known status/percentile/token/abstention/truncation fixtures, time boundaries, resumed/cache events, missing usage. Indexed SQL selection and aggregation; duration is creation-to-finish including queue/retry time; rates among successful runs with recorded grounding (abstention) and successful runs (truncation). No monetary-cost claim.
- [x] Full backend suite, service checks where available, independent review, documentation including metric denominators, timeout/worker thresholds and operator configuration.

Review focus: dead probes must not exhaust threads; idle workers must remain ready; context must not leak between concurrent jobs; no user-controlled error strings in journals; cached outputs must not count token usage twice; unauthenticated local mode must not expose operator metrics.


## Verification results

- Full backend suite: **325 passed**, four dependency deprecation warnings.
- Real isolated `scripts.check_knowledge_services` smoke: passed with local Ollama, including Retrieve → Agent → Response, rebuild/activation, cleanup provenance, deletion and internal authentication.
- Review findings fixed: cancelled-node token omissions, health-request correlation, agent/delegated token attribution, and cancellation during a threaded event commit. Regression tests preserve the checkpoint and count usage once for both explicit cancellation and timeout.
- Metrics migration/restart and SQL-only aggregation are covered; seeded known durations yield exact p50=10 and p95=19. Operator override and auth-disabled denial are covered.
- No live database migration, process restart, commit or push performed. Previous uncommitted run-scale changes were preserved.
- Limitations: KB health probes establish HTTP reachability; reported token counts are not dollar costs and can omit provider/crash-unreported usage; rates and time-window cohort definitions are documented in `docs/operations.md`.
