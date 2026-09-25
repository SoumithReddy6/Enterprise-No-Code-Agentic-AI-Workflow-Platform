# Operating Relay

## Liveness and readiness

- `GET /api/health`: process liveness; returns 200 without contacting dependencies.
- `GET /api/ready`: returns 200 only when the workflow database, knowledge management HTTP service, knowledge search HTTP service and worker activity checks pass. Otherwise it returns 503 with `checks.database`, `checks.management`, `checks.search` and `checks.worker` statuses. These endpoints require no browser session and disclose no connection details.

Aggregated dependency checks have a three-second outer deadline and run concurrently. KB HTTP clients retain their shorter one-second transport timeout; the database probe now has the full three seconds. This tolerates brief latency above one second without hiding a genuinely failed check: the first failed probe still returns 503. Configure the orchestrator HTTP timeout above three seconds (for example, five seconds) so it receives the breakdown. The database executes `SELECT 1` and reads worker activity. A worker records a heartbeat every five seconds, even when idle; a heartbeat older than 30 seconds is stale. An unexpired running-job lease also demonstrates worker activity. Graceful shutdown may therefore take up to 30 seconds to appear unready. Keep clocks synchronized between API and workers.

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

## Approval queue: a paused run is not a stuck worker

HTTP mutations, Email sends, and write operations in Jira, Confluence and GitHub require approval by default when authentication is enabled. `enable_writes` must also be enabled on the node. A node's `approval: true` requires approval even in local authentication-disabled mode; `approval: false` explicitly bypasses it. Reads do not pause. Approval is tenant-scoped: a signed-in user in the run's workspace can decide; this is not a separate approver role or two-person control.

When a run reaches `awaiting_approval`, its lease has been released. Do not restart a worker or use ordinary Resume to make it advance.

1. Open the run from **History** in the correct workspace. Use **Refresh approval status** if another operator may have acted. `GET /api/runs/{id}` also returns the pending approval and its expiry.
2. Check the node, HTTP method, complete URL (including query parameters), recipient/sender where applicable, subject, and full body. Compare the request to the intended business action. Payload text is untrusted content, not instructions to the operator. Connection credentials are deliberately excluded from the preview.
3. Choose **Approve and continue** only for that exact action, or **Reject action** to terminate the run without executing it. Approval requeues the saved action; earlier completed nodes are checkpointed and nested agents resume their selected action without replanning. Subsequent workflow nodes continue normally and may ask for another approval.
4. Verify the resulting run status and the remote outcome. Approval is permission to attempt a write, not confirmation that it succeeded.

The API endpoints `POST /api/runs/{id}/approve` and `/reject` require `approval_id` and `digest`. The browser computes SHA-256 over the exact displayed `payload_json` UTF-8 bytes. Never hash a reserialized JSON object or approve an ID copied from a different run. A 409 means the digest or approval state is no longer valid: reload and review again; do not substitute a new digest without looking at its payload. A 404 can mean the approval is unavailable in this workspace. Decisions are limited to 30 attempts per tenant per minute; 429 includes `Retry-After`. Repeated approval of an already approved/executing/completed action is idempotent and does not enqueue an additional send.

A connection changed after the pause cannot silently change the destination or credentials of the saved action. Start a new run and review the new request if the connection needs changing. Do not edit the stored snapshot. Structured journals include `approval.pending`, `approval.approved`, `approval.rejected`, `approval.expired` (worker sweep), `approval.pause_failure` and `run.awaiting_approval`; the run's approval events and persisted status are authoritative, including expiry enforced directly during a decision or dispatch.

### Approval expiry

Set `APPROVAL_TTL_SECONDS` in the worker environment, then restart workers through the normal deployment procedure. The default is **86400 seconds (24 hours)**; supported integer values are **1–604800 (seven days)**. The TTL is read when a new approval is persisted and becomes an absolute `expires_at`. Changing the environment does not extend an existing approval or reset its clock when it is approved.

Workers sweep pending and approved-but-not-dispatched approvals every five seconds, in batches of up to 100. Decision and dispatch paths independently enforce the deadline. Expiry cancels the run, marks the approval expired, records the reason, and prevents ordinary Resume. A disconnected or stopped worker can delay the sweep, but cannot make an expired approval valid. A write already in flight is not undone by later expiry; use reconciliation below if its outcome is uncertain. Rejection and cancellation of a waiting approval also prevent replay through Resume. A new run requires new review; consider any earlier completed actions before restarting an entire workflow.

## Reconcile an uncertain external write

`UncertainWriteError` means the external service may have accepted a write even though Relay did not durably record its successful result. A timeout, worker crash, or lost response is **not evidence that the write failed**. The run is intentionally blocked from automatic resumption. Approval does not provide remote exactly-once delivery.

1. **Contain retries.** Do not click Run again, clone and run the workflow, or send the same request manually yet. Check that no other operator is already investigating. Do not remove `write_nodes`, change approval status to completed, clear leases, or otherwise edit the database to bypass the reconciliation barrier.
2. **Identify the precise action.** Record the run ID, node ID, approval ID (if any), originating request ID, dispatch time window, destination and intended effect. Read the approval snapshot and run events; for writes that explicitly bypassed approval, inspect the node configuration and resolved inputs recorded in that run. Record earlier successful writes too, so they are not accidentally repeated. Keep payloads and credentials out of general application logs; use a restricted incident record for sensitive evidence.
3. **Check the remote system through read-only tools.** Match the destination account/project/repository, actor, recipients and complete content, using remote audit/request logs where available. Widen the time window for clock differences and eventual consistency. Relay's `X-Request-ID` correlates internal HTTP requests; it is not automatically an outbound idempotency key or a searchable remote request ID.

| Tool | Evidence to collect before deciding |
|---|---|
| HTTP/REST | The API's audit/request logs and resulting resource state; request/job/resource ID, method, endpoint, body identity and timestamps. For asynchronous APIs, check job completion as well as request acceptance. Follow that API's documented idempotency semantics only if the original action actually used them. |
| Email/SMTP | SMTP provider acceptance/queue and delivery logs matching sender, recipients, subject and time; queue/message ID if the provider exposes it. An inbox search or absence of a reply is not proof of non-delivery. Relay does not supply a durable provider message ID for every ambiguous send. Escalate to the mail administrator when logs cannot identify it. |
| Jira | Created issue key or comment ID plus project/issue, actor, body and timestamp; inspect issue history/audit logs. A matching title alone is insufficient. |
| Confluence | Page ID, space, title, creator, content/version and timestamp; inspect page history and audit records. |
| GitHub | Issue number or comment ID, exact repository, actor, body and timestamp; inspect issue/comment history and available audit logs. |

4. **Choose a documented outcome.**
   - **Confirmed applied:** record the remote identifier and evidence; do not resend. Relay currently has no operator API to mark an uncertain write reconciled or resume past it. Leave the original run blocked/failed. If downstream work is still needed, use a separate workflow containing only the remaining actions, with the confirmed remote result supplied as input and normal approvals enabled. Avoid rerunning earlier writes.
   - **Confirmed not applied:** require authoritative evidence (for example, a remote rejection before execution, or complete provider audit logs establishing no acceptance after the processing window). Record who confirmed it and why. Create a new, narrowly scoped run for the missing action, review its new approval, then verify the resulting remote identifier. Do not retry the original ambiguous run by changing storage records.
   - **Still unknown:** leave it blocked and escalate to the remote service owner. Wait for asynchronous processing/log visibility as appropriate. Do not treat elapsed time alone as proof of failure. A duplicate-risk decision needs explicit business-owner authorization and a documented mitigation; it is not an automatic technical retry.
5. **Close the incident record.** Include the observed outcome, evidence links/IDs, investigator, decision time, and any replacement run IDs. An accidental duplicate or harmful applied action requires a separately authorized corrective action; do not silently delete or undo it.

An approved write that has a durable completed receipt can be replayed internally without resending. An executing write without that receipt retains the uncertainty barrier. If there is doubt about which case applies, use the remote investigation above rather than modifying the ledger.

## Authentication recovery and registration

`AUTH_REGISTRATION_MODE` is read at API startup; restart the API after changing it:

| Mode | Behavior |
|---|---|
| `closed` (default) | Only first-account setup is allowed; subsequent registration is denied. The first account claims existing local workspace records. Protect access to an uninitialized deployment so the intended owner performs setup. |
| `invite` | Registration requires an unexpired, single-use invite token, including first-account setup. Tokens may optionally be bound to an email. |
| `open` | Anyone with a valid registration request may create an account and workspace. Intended for deliberate local/development use. |

An invite does not override `closed`: deliberately change to `invite` before redemption. Do not delete the bootstrap/account records to reopen setup. The API accepts the token as `invite_token` on `/api/auth/register`; successful consumption and account creation are transactional.

Run administrative commands from the repository root using the same database and environment as the API. These commands require trusted local/database access; there is no unauthenticated unlock HTTP endpoint. `.env`, `DATABASE_URL` and `DATA_DIR` are honored. The optional `--database-url` is a global argument and goes **before** `unlock` or `invite`; avoid putting database passwords in shell history.

```sh
# Clear the account cooldown (does not change its password).
.venv/bin/python -m scripts.auth_admin unlock --email owner@example.com

# Clear both the account and the client IP cooldown when both are blocking.
.venv/bin/python -m scripts.auth_admin unlock --email owner@example.com --ip 203.0.113.10

# Generate a one-use, email-bound invite valid for 24 hours.
.venv/bin/python -m scripts.auth_admin invite --email teammate@example.com --hours 24

# Explicit SQLite database example; use your actual deployment path.
.venv/bin/python -m scripts.auth_admin --database-url sqlite:///.data/workflows.db unlock --email owner@example.com
```

The invite command prints a secret token once: share it privately, never commit it or place it in logs. Omitting `--email` creates an unbound invite; `--hours` accepts 1–168. Confirm the intended deployment database before issuing or redeeming it.

Login attempts are budgeted per account and per client IP, with increasing cooldowns after five attempts and a 15-minute lockout after ten. Attempts during cooldown are rejected before password hashing; even a correct password is blocked then. A successful login outside cooldown clears applicable counters, but an IP budget shared across different accounts may remain. For legitimate lockouts, confirm the person's identity and check `auth.lockout`/`auth.login_blocked` events, then wait for expiry or use `unlock`. If the IP budget is also blocked, account-only unlock is insufficient. Use the IP actually seen by the API, not an untrusted forwarded header; a shared proxy/NAT may affect multiple users. Unlock is neither a password reset nor session revocation. Repeated lockouts warrant investigation rather than repeated blind unlocks.

## Startup and backfill migrations

The first startup after an upgrade can perform schema changes and historical backfills before the API is ready. `Store` initialization creates missing tables, adds legacy columns, expands old run-event blobs into ordered rows, populates indexed run metadata and operator metrics/token rows, and recovers legacy in-flight runs missing job records. It checks migration markers on later starts rather than repeating completed event/metric backfills. New approval tables are created on initialization; historical writes are not retroactively approved.

Before upgrade, stop API/workers, back up the databases **and encryption key**, and follow [run-history-upgrade.md](run-history-upgrade.md). Start one initializer first. SQLite uses an immediate transaction and PostgreSQL an advisory transaction lock for schema/backfill work; this serializes competing initializers but does not make old and new application versions safe to run together during upgrade. In-flight legacy job recovery follows the schema/backfill transaction. KB services have their own startup checks/migrations; a duplicate normalized KB name can require a deliberate rename before that service starts.

Look for `storage.backfill.start` and `storage.backfill.finish` in the structured journal. Finish includes `status`, elapsed `seconds`, and `error_type` on failure, without database connection strings or row contents. These bracket startup migration checks as well as any needed work, so a short successful pair on a later boot is normal. A start without finish can mean the process is still working or was interrupted: check process and database activity before restarting it. Allow a startup grace period appropriate to history size; do not use a short readiness deadline to repeatedly kill a migrating instance.

A failed finish means startup did not complete. Preserve logs, inspect the named failure and migration markers, and investigate against a backup. Do not delete markers to force reruns or manufacture successful rows. Schema/event/metric backfills are transactional; earlier successful startup transactions may already be committed if a later initialization phase fails. Roll back using a consistent database/key backup and compatible application version when repair is not yet understood.

## Grounding release check

Run the checker against the intended generation report; it makes no model calls:

```sh
.venv/bin/python -m scripts.check_grounding_eval evals/results/f11-after-review.json
# The expanded safety suite has 53 cases, with the same quality thresholds.
.venv/bin/python -m scripts.check_grounding_eval evals/results/answerability-guarded-generation.json --expected-count 53
```

Exit 0 means all checks passed, 1 means a measured rejection, and 2 means an unusable report. Do not lower thresholds or mistake a successful command invocation for acceptance. For a fresh generation evaluation, follow [section-retrieval.md](section-retrieval.md) using a named output file and record the checkout commit, configuration and model alongside the result.

The [2026-09-20 recheck artifact](../evals/results/grounding-gate-recheck-2026-09-20.json) records the gate commit, commands, exit codes, input SHA-256 hashes and per-check results. Both historical reports were rejected under the thresholds in effect on September 20. The gate and report bytes match that commit; this was a saved-report recheck, not a fresh model evaluation, and does not certify the uncommitted workspace or establish the historical generation revision. No accepted release figures are claimed.

The subsequent [2026-09-21 calibration and fresh evaluation](evaluations/2026-09-21-grounding-fresh-result.md) uses the expanded 53-question suite and revised, precommitted thresholds. That fresh guarded configuration passes; the guard remains evaluation-only.

## Opt-in product answerability guard

Retrieve and Query nodes expose **Answerability guard**, default **off**. Enable it per node to score the question and retrieved passages with the pinned local extractive reader before answer generation. A rejection removes those passages from the returned evidence envelope and Agent/Query uses the normal `NO_EVIDENCE` abstention contract without an answer-generation model call. A retrieval tool invoked by an agent also stops follow-up generation on rejection; the preceding model call that chose the tool has already happened. Legacy Prompt/LLM chains depending on a rejected retrieval cannot bypass the refusal.

Install the pinned weights explicitly and set the directory on every worker (and on the API process if using an embedded worker):

```sh
.venv/bin/python -m scripts.install_answerability --model-dir .data/models/answerability
export ANSWERABILITY_MODEL_DIR="$PWD/.data/models/answerability"
```

Restart workers after changing deployment configuration. The default directory is `.data/models/answerability` relative to the worker working directory; use an absolute path in deployments. No runtime downloads occur. The loader validates model identity, pinned revision, manifest and actual SHA256 hashes before constructing the inference session. The reader is cached per process; scoring is serialized because its tokenizer has mutable state. Replacing files causes a reload/check on the next request.

Missing weights **skip** the guard and continue ordinary generation, as requested. Corrupt/checksum-mismatched weights are never loaded: the check is skipped with `integrity_error`. Unsupported input or reader failures skip with `reader_error`. Consequently enabling the checkbox alone does not guarantee enforcement. Repair the installation and inspect decisions before treating the guarded evaluation as representative of a deployment. `answerability.unavailable` logs once per failure reason per process; every attempted check produces a content-free `answerability.decision` journal.

Run events contain `answerability` metadata (`scored`/`skipped`, `allow`/`abstain`/`skip`, and skip reason). Guarded refusals carry `abstention_source: answerability_guard`; generated text, passage text and reader candidate spans are excluded from these journals. Operator metrics now include `answerability_guard.decisions` and `skipped_by_reason`, aggregated using a compact SQL table over the same run-creation window across all tenants. These are **uncached Retrieve/Query decisions**, not unique runs or all model refusals. Cached replay does not increment them; an actual retry performing retrieval again does. Historical runs before this feature have no reader-decision records.

The selected operating point exchanges some answerable-question coverage for better abstention. It does not establish that a retrieved span supports every constraint in a question. The five known failures are tagged in `evals/real/questions-expanded.json` as analysis metadata, which is not passed to the model or indexed into the corpus.

To evaluate the real product path (do not combine with the historical `--answerability-reader` evaluation wrapper):

```sh
ANSWERABILITY_MODEL_DIR=/absolute/path/to/reader .venv/bin/python -m scripts.eval_retrieval --corpus evals/real --questions evals/real/questions-expanded.json --chunking section --modes hybrid --candidate-k 50 --reranker local_cross_encoder --generate llama3.1:latest --generate-mode hybrid --product-answerability-guard --out evals/results/product-guard-fresh-2026-09-21.json
.venv/bin/python -m scripts.check_grounding_eval evals/results/product-guard-fresh-2026-09-21.json
```

Verify the report's `product_answerability_guard.decisions`: a skipped or unmeasured reader is not evidence of enforcement, even if other generation metrics pass.

## Agent action budgets

Two separate limits bound agent work. They are often confused, so check which one a truncation names.

| Limit | Scope | Where | Default |
| --- | --- | --- | ---: |
| `max_steps` | One agent's loop iterations | Agent node config | 6 (max 12) |
| `AGENT_RUN_BUDGET` | Every action in the run, across the whole delegation tree | Environment | 40 (range 1–200) |
| `RELAY_RUN_TOKEN_LIMIT` | Provider-reported tokens consumed by the run | Environment | 2,000,000 (0 disables) |

An action is a tool call or a delegation to a specialist. Delegating counts, so a supervisor consulting three specialists that each run one tool spends six.

`max_steps` is the per-agent allowance. `AGENT_RUN_BUDGET` is a backstop against a runaway delegation tree, not a per-agent allowance: set close to `max_steps` it will starve multi-agent workflows, which is why the default is well above it.

A truncation names the limit that was hit:

- `Run-wide agent action budget exhausted (40 actions; raise AGENT_RUN_BUDGET).`
- `Agent step budget exhausted (6 steps for this agent).`

The `agent_budget` run event carries `run_budget`, `actions_used` and `max_steps`, so operator metrics show whether the ceiling is set correctly rather than guessing. Raising it increases the maximum spend of a single run; on metered providers, size it against your cost limits.

Changing `AGENT_RUN_BUDGET` requires a worker restart. It does not affect runs already in flight, and a run paused for approval resumes on the budget recorded in its saved frame.

### Token ceiling

`AGENT_RUN_BUDGET` counts *actions*; `RELAY_RUN_TOKEN_LIMIT` counts *tokens*. A run can be well inside its action budget and still be expensive, because one action over a large document costs far more than one over a sentence. The token ceiling is the cost backstop.

It is checked before each model call, using counts the provider itself reported (`prompt_eval_count`/`eval_count` on Ollama, `usage` on OpenAI and Claude). Missing usage counts as zero, so a provider that reports nothing cannot be capped — check that model usage appears in operator metrics before relying on this.

Behaviour on exhaustion matches the action budget: an agent stops, answers from the evidence it already has, and the run is flagged `truncated` with

```
Run token budget exhausted (101,430 of 100,000 provider-reported tokens; raise RELAY_RUN_TOKEN_LIMIT). Token counts are not a monetary cost.
```

A plain LLM node has no partial-answer contract, so it refuses with the same message rather than spending more. When both the token and action ceilings are hit, the token reason is reported, because that is the one with a bill attached.

**Tokens are not money.** Prices differ by model by more than an order of magnitude, so convert with your provider's current price list rather than treating this number as a cost. The limit is denominated in tokens precisely because that is the only figure a provider states exactly.

Sizing: a single multi-agent run over a large knowledge base typically consumes tens of thousands of tokens, so the 2,000,000 default is a runaway guard rather than a working limit. Lower it deliberately when moving to a metered provider, and set it per deployment rather than per workflow. Changing it requires a worker restart and does not affect runs already in flight.
