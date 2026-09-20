# Operating Relay

## Liveness and readiness

- `GET /api/health`: process liveness; returns 200 without contacting dependencies.
- `GET /api/ready`: returns 200 only when the workflow database, knowledge management HTTP service, knowledge search HTTP service and worker activity checks pass. Otherwise it returns 503 with `checks.database`, `checks.management`, `checks.search` and `checks.worker` statuses. These endpoints require no browser session and disclose no connection details.

Each dependency has a one-second deadline; checks run concurrently. The database executes `SELECT 1` and reads worker activity. A worker records a heartbeat every five seconds, even when idle; a heartbeat older than 30 seconds is stale. An unexpired running-job lease also demonstrates worker activity. Graceful shutdown may therefore take up to 30 seconds to appear unready. Keep clocks synchronized between API and workers.

KB health checks establish HTTP reachability, not successful database/vector-index operations inside each service. They are not a substitute for an ingestion/retrieval smoke test. Readiness does not check Ollama or external provider availability.

Only one database probe may remain outstanding per API process. Timeout does not kill a database driver call; subsequent probes share the outstanding work rather than accumulating threads. Configure driver/network timeouts for deployment as well. Worker polling and lease renewal use background threads. Other existing runtime/database paths are not all asynchronous; use separate workers (`RELAY_EMBEDDED_WORKER=false`) for production process isolation.

Example:

```sh
curl -i http://127.0.0.1:8000/api/health
curl -i http://127.0.0.1:8000/api/ready
```

Use liveness for process restarts and readiness for traffic routing. Expect readiness to fail until KB services and a worker are running.

## Request and run correlation

Every HTTP response includes `X-Request-ID`. Supply a value containing 1–128 ASCII letters, digits, `.`, `_`, `:` or `-` to retain an existing trace; missing or invalid values receive a generated UUID. Treat this value as an untrusted correlation label, never authentication or proof of origin.

The `relay.journal` logger emits compact JSON with UTC timestamp, request ID, run ID and tenant when known. `http.request` records method, route template, status and duration. Query strings and unmatched raw paths are omitted. Raw `uvicorn.access` and HTTPX informational logs are suppressed to avoid duplicate lines containing URLs or query strings. Unhandled errors return a generic 500 with the same request ID.

Run submissions persist their originating ID. Worker lifecycle, node and `model.usage` journals carry it through execution; resumed runs retain that original submission ID. HTTP requests for existing runs also log the run ID. Internal KB RPC and health requests forward the request ID, and newly queued KB builds retain it for ingestion processing. Historical records without an ID cannot acquire one retrospectively.

Journals include metadata only. Passwords, session/invite tokens, credential values, emails, prompts, passages, outputs and exception messages are excluded. Authentication events include `auth.login_success`, `auth.login_failure`, `auth.login_blocked`, `auth.lockout` and `auth.invite_consumed`. Job recovery emits `job.lease_expired` and `job.lease_acquired`; provider retries and KB failures have separate events. Existing run records still contain execution inputs/outputs under their normal authenticated access rules.

```sh
# For a JSON log file collected by your process supervisor:
grep '"event":"auth.lockout"' relay.log
grep '"request_id":"your-request-id"' relay.log
```

## Operator metrics

`GET /api/operator/metrics?hours=168` requires a valid browser session and operator authorization. The first workspace account is the default operator. Set `RELAY_OPERATOR_ACCOUNT_IDS` to comma-separated account IDs to replace that default; IDs are available from `/api/auth/me`. An explicitly empty allowlist denies everyone. Authentication-disabled test/local mode also denies access. The endpoint covers all tenants; it is intentionally not a normal workspace-user route.

`hours` accepts 1–2160 (90 days). The default is seven days. The response contains:

| Field | Meaning |
|---|---|
| `runs_by_status` | Current statuses of runs created in the half-open `[start, end)` window |
| `duration_seconds.p50/p95` | SQL nearest-rank percentiles among finished success/failed/cancelled runs; creation through final completion includes queue time and resumed attempts |
| `tokens` | Reported prompt/completion tokens and measured calls grouped by provider and model, including failed/cancelled nodes when their usage event was durably recorded |
| `abstention_rate` | Fraction of successful grounded runs with at least one grounded node that abstained; not necessarily final-response refusal |
| `truncation_rate` | Fraction of successful runs flagged incomplete because an agent exhausted its budget |
| `denominators` | Successful run counts and successful grounded run counts used by the rates |

Empty denominators/percentile populations return `null`. Cached checkpoint replay contributes no additional tokens. Cancellation waits for an in-flight event transaction and does not record its usage a second time. Calls across resumed attempts remain counted. Provider omissions, crashes before event persistence, and loss of a worker lease can leave token usage incomplete; no estimate is invented. Token counts are not dollar costs. Legacy multi-model agent events lack per-model detail and cannot be perfectly attributed; only their stored attribution is available.

Run creation/status indexes bound window selection; percentile and grouped aggregation work remains proportional to the matching population. SQL reads compact run metric columns and token rows, not workflow JSON, passages or event payloads. Completion updates metric columns atomically; token rows are inserted alongside ordered run events. Startup atomically backfills existing runs/events once using a migration marker. Large histories may extend the first startup.

Before upgrading, follow the database backup and stopped-worker procedure in [run-history-upgrade.md](run-history-upgrade.md). No live database or service restart is performed by editing the application files.
