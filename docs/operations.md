# Operating Relay

## Liveness and readiness

- `GET /api/health`: process liveness; returns 200 without contacting dependencies.
- `GET /api/ready`: returns 200 only when the workflow database, knowledge management HTTP service, knowledge search HTTP service and worker activity checks pass. Otherwise it returns 503 with `checks.database`, `checks.management`, `checks.search` and `checks.worker` statuses. These endpoints require no browser session and disclose no connection details.

Aggregated dependency checks have a three-second outer deadline and run concurrently. KB HTTP clients retain their shorter one-second transport timeout; the database probe now has the full three seconds. This tolerates brief latency above one second without hiding a genuinely failed check: the first failed probe still returns 503. Configure the orchestrator HTTP timeout above three seconds (for example, five seconds) so it receives the breakdown. The database executes `SELECT 1` and reads worker activity. A worker records a heartbeat every five seconds, even when idle; a heartbeat older than 30 seconds is stale. An unexpired running-job lease also demonstrates worker activity. Graceful shutdown may therefore take up to 30 seconds to appear unready. Keep clocks synchronized between API and workers.

`checks.python_sandbox` reports whether Python tools can run: the Docker CLI is on PATH, the daemon responds, `python:3.12-alpine` is present locally (execution uses `--pull=never`), and a minimal container actually runs under the same command, image, security flags and limits as the Python executor - Docker can pass the first three and still refuse that container. Its `reason` is one of `cli_missing`, `daemon_unavailable`, `image_missing`, `container_failed` or `probe_timeout`, and `required` mirrors `PYTHON_SANDBOX_REQUIRED`. When required, an unavailable sandbox returns 503; by default it does not, so knowledge and other workflows stay routable. Either way, a workflow containing a Python tool is refused at validation, submission, resume and execution while the sandbox is unavailable, with the reason and the fix.

Readiness and validation share one probe and cache its result for five seconds, so polling does not start a container per request and a recovered daemon is noticed within that window. The worker always probes afresh immediately before executing a Python workflow, so a cached success never vouches for a sandbox that has since stopped; the probe takes about 0.2 seconds. A hung `docker` command is stopped, and a smoke container it may have started is removed.

KB health checks establish HTTP reachability, not successful database/vector-index operations inside each service. They are not a substitute for an ingestion/retrieval smoke test. Readiness does not check Ollama or external provider availability.

Only one database probe may remain outstanding per API process. Timeout does not kill a database driver call; subsequent probes share the outstanding work rather than accumulating threads. Configure driver/network timeouts for deployment as well. Worker polling and lease renewal use background threads. Other existing runtime/database paths are not all asynchronous; use separate workers (`RELAY_EMBEDDED_WORKER=false`) for production process isolation.

Example:

```sh
curl -i http://127.0.0.1:8000/api/health
curl -i http://127.0.0.1:8000/api/ready
```

Use liveness for process restarts and readiness for traffic routing. Expect readiness to fail until KB services and a worker are running.

## Prepared replies and the optional Redis cache

The `greeting_cache` entry node handles only messages explicitly configured in its
`entries` list. Whole-message lookup uses Unicode NFC, case folding and collapsed
whitespace. Punctuation stays significant: configure `hello!` separately from `hello`.
`Hello, buy 3 laptops` continues with the original trigger input, unchanged. No preset
includes yes/no or approval commands. The node and admission chain require contract 2;
the router and verification handlers are separate implementation steps, so the editor's
new-workflow starter has not changed yet.

Redis is optional. With `COMMON_REPLY_REDIS_URL` unset, prepared replies work locally
without a model. To enable the disposable local service:

```sh
docker compose --profile common-replies up -d common-replies
# Set this in the API and worker environments, then restart those processes:
COMMON_REPLY_REDIS_URL=redis://127.0.0.1:56379/0
```

The service is bound to loopback, capped at 64 MB, and does not persist cache data.
The client is pinned to redis-py 8.1.0; the server to Redis 8.6.7-alpine3.23 and its
image digest. The client was audited before installation; the server's official
[8.6.7 security release](https://github.com/redis/redis/releases/tag/8.6.7) was reviewed.
This release review is not an OS-level image vulnerability scan. Remote services should
use authenticated `rediss://` URLs and network access controls. URL query overrides are
refused so they cannot weaken timeouts or enable retries. URLs never enter workflow
documents, journals, or readiness responses.

The workflow configuration is authoritative. Cache entries contain only versioned
prepared replies; tenant, workflow content, reply-set content and whole-message digests
scope the keys. Each value is at most 4,096 bytes and expires after 3,600 seconds.
GET/fill together have a 200 ms deadline with no client retry. Oversized configured
replies stay exact locally. Missing, expired, malformed or altered entries are replaced
with the current configured reply; timeouts/outages use it locally. Bounded reads also
prevent a poisoned large cache value from being downloaded in full.

`checks.common_reply_cache` reports `ok`, `unavailable`, or `disabled`, always with
`required: false`. Its failure never makes an otherwise healthy API return 503. API
and standalone worker shutdown close their owned async connections. The content-free
`common_reply.lookup` journal line uses `reason_code` values `hit`, `filled`,
`local_fallback`, or `not_common`; the node's checkpointed `receipt` records that
outcome and the content digests. Checkpoint replay reuses the saved result rather than
looking up Redis again. Prepared reply text is not eligible extraction evidence.

Redis holds no request-router decisions, approvals, leases, action results or business
permission. Those remain in the workflow database. CI tests use a separate Redis
service and unique disposable namespaces via `RELAY_TEST_REDIS_URL`.

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
| `human_reviews.by_kind` | Durable approval counts by `write`, `verification` or `decision` and current status; completed typed reviews are manual confirmations, not automatic model checks |
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

### Verification and decision reviews (contract 2)

Approvals expose `kind`. For `verification`, inspect the exact original request,
extracted interpretation and field-check report in `payload_json`. Accept only that
interpretation or reject the run. For `decision`, inspect its original request,
resolved inputs and decision policy, then explicitly select the true or false route.
These approvals grant no additional fact provenance; model guesses remain labelled
as guesses. The editor's full typed-review controls are Step 7; Step 6 provides the API.

Use the existing approve/reject endpoints with the displayed approval ID and SHA-256
digest. Decision approval also requires `"choice": true` or `"choice": false`, a JSON
boolean. Missing choice returns 409; string/number choices return 422. Verification
and write approvals must omit choice. Duplicate matching confirmation is idempotent;
trying to change an already confirmed decision is refused. A rejection is terminal.

Both typed kinds checkpoint the earlier work, release the lease, and requeue after
confirmation. Their TTL is the same `APPROVAL_TTL_SECONDS`. Expired confirmations cannot
authorize a later restored decision or write, even when the earlier review completed.
Input, interpretation, policy, run or tenant mismatch requires a new run and review.
Never edit a checkpoint or approval row to force continuation. A pre-execution safety
refusal sends nothing and needs no remote reconciliation; an uncertain external write
still follows the reconciliation procedure below.

Migration `0004_approval_kind` backfills earlier approvals as `write`, adds the allowed
kind constraint and widens invocation IDs. Normal startup upgrades through Alembic.
Downgrade refuses databases containing typed reviews or IDs over the former 300-character
limit rather than treating a review as a write or losing its identity. Back up, export
and archive affected records under a reviewed maintenance procedure first. The frozen
baseline and data-backfill authority are unchanged.

Content-free journals include `verification.result`, `review.confirmed` (manual, with
kind), and the existing approval lifecycle. The operator metrics endpoint groups real
approval rows by kind/status, so replay does not count another person or another review.

### Approval expiry settings

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

## Decision provenance (shadow mode)

Every successful node output carries a `_provenance` record. It labels each output, and each field of a structured Agent answer, as `source`, `quoted`, `calculated`, `guessed` or `absent`. Labels are assigned by code: a model's value is `quoted` only if it is written in that Agent's trusted bound input. System prompts and examples are never searched, and another Agent's free text is never trusted. See [the plan](superpowers/plans/2026-10-08-decision-provenance.md) for the rule and its limits.

Current records use version 2 and collision-free JSON Pointer field paths: `/order/amount`
and `/order.amount` identify different values. Records fit within 4,096 serialized bytes;
omitted fields or incomplete traversal remain unknown, never proven absent. Attached
Agents, specialists and loop bodies use the same labelling function as graph nodes.
Loop entries retain their bounded labels on checkpoint and replay.

Quotation evidence is limited to this invocation's resolved, eligible input. Trigger
messages and explicit Jira/Confluence/GitHub record reads can establish independent
data. Arbitrary HTTP/Python results and write responses remain guessed, even if they
echo a request. Prompt literals, model grounding answers and retrieval wrappers
(question, filename, citation and page) cannot establish quotation evidence. Retrieval
uses the passage text only. A model-authored specialist task is also unverified.

On resume, version-1 derived labels are conservatively downgraded before a new decision
consumes them; the old rules could label invented values as trusted. Public values and
completed decision receipts are preserved. A completed decision replays its saved
receipt without another model call. Malformed labels inside cached loop results or
saved item progress are refused, just like malformed top-level metadata.

In this release the labels are **observational**. A Condition still takes its branch exactly as before. Its success event carries `decision: {label, would_review, reason}`, and a decision on a guessed value writes a content-free `decision.would_review` journal entry with `node_id` and `reason_code`. The reason codes are:

- `guessed`: the value could not be found in the trusted input;
- `unlabelled`: the value came from a checkpoint created before this feature;
- `missing_field` or `unrecorded_field`: the field has no label;
- `check_failed`: a Calculate node in check mode found its values disagree, such as a written total that differs from quantity × unit price. Its success event carries `decision: {label, passed, would_review, reason}`.

Use these entries to measure how often enforcement would pause runs before turning it on.

Validation distinguishes decisions that can **only** rest on a guess from calculations
that may use guessed values when present. Constant short circuits do not warn about
unread operands, and nullable calculations do not claim that every run needs review.
Warnings are advisory. The editor's "Trust in the last run" section shows each value's
label and each decision's outcome for the run on screen. A checkpoint with a malformed `_provenance` record is refused on resume; start a new run.

## Agent action budgets

Two separate limits bound agent work. They are often confused, so check which one a truncation names.

| Limit | Scope | Where | Default |
| --- | --- | --- | ---: |
| `max_steps` | One agent's loop iterations | Agent node config | 6 (max 12) |
| `AGENT_RUN_BUDGET` | Every action in the run, across the whole delegation tree | Environment | 40 (range 1–200) |
| `RELAY_RUN_TOKEN_LIMIT` | Provider-reported tokens consumed by the run | Environment | 2,000,000 (0 disables) |

An action is a tool call, knowledge-base retrieval or query, or a delegation to a specialist. Delegating counts, so a supervisor consulting three specialists that each run one tool spends six. Model calls are accounted under the separate token ceiling.

`max_steps` is the per-agent allowance. `AGENT_RUN_BUDGET` is a backstop against a runaway delegation tree, not a per-agent allowance: set close to `max_steps` it will starve multi-agent workflows, which is why the default is well above it.

A truncation names the limit that was hit:

- `Run-wide agent action budget exhausted (40 actions; raise AGENT_RUN_BUDGET).`
- `Agent step budget exhausted (6 steps for this agent).`

The `agent_budget` run event carries `run_budget`, `actions_used` and `max_steps`, so operator metrics show whether the ceiling is set correctly rather than guessing. Raising it increases the maximum spend of a single run; on metered providers, size it against your cost limits.

Changing `AGENT_RUN_BUDGET` requires a worker restart. A paused or interrupted run resumes from its durable accounting against the current configured ceiling. Lowering the ceiling can refuse further actions; previously spent actions remain spent, and saved remaining budget is never replenished. Approval frames are not a second budget authority.

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

## Loop result and event size bounds

A For each loop's `results` output has a stated contract, measured in serialized UTF-8 bytes of the whole list (brackets, separators and entry structure included):

- **Retained values: 1,000,000 bytes.** Item values are kept in order while the list fits; the first value that does not fit, and every later value, is stored as `""` with `value_dropped: true`. Values are never cut part way and always form a prefix.
- **Outcome text: 500 characters per field.** `error` and `truncation_reason` longer than that are shortened and end with `…`.
- **Hard maximum: 2,000,000 bytes for the complete list, metadata included.** Room is always reserved for every remaining accepted item in compact form (at most 226 bytes each); an entry that would take that room is stored compact - `index` and `status` kept, details replaced by a marker and `details_omitted: true`. Unlike values, details are kept whenever room remains.

Any dropped value or omitted detail marks the loop, and therefore the run, incomplete with the count and the reason. Item rows in `run_loop_items` hold the same admitted entries, so resume reaches identical results.

Loop events stored for observability (`loop_node_id` set) are bounded too: large fields become 2,000-character previews, `preview_of` lists at most 20 shortened paths (`preview_omitted` counts the rest), `usage` and `answerability` stay exact at normal size and keep their structure when abnormally large, and a final ceiling of 32,768 bytes is applied to the event exactly as stored, preview metadata included: the largest ordinary fields are replaced first, then `preview_of` is reduced to its count, and `usage` and `answerability` are removed - never retyped - only as a last resort. Identity fields (`node_id`, `status`, `loop_node_id`, `item_index`, `invocation_id` and similar) are never shortened. Approvals, write results, checkpoints and item rows are stored in full elsewhere.

## Database schema

Alembic is the single authority for DDL. `alembic_version` records schema state;
`schema_migrations` records application data transformations. They answer different
questions and are kept separate.

| Database | What startup does |
| --- | --- |
| Empty | Upgrades to head, creating the whole schema |
| Existing, pre-Alembic | Brings it to the baseline shape, verifies it structurally, stamps the baseline, then upgrades |
| At head | Starts normally |
| Behind head | Upgrades, or with `auto_upgrade=False` refuses and names the command |
| Unknown or newer revision | Refuses to start |

A database is never stamped because a table happens to exist. It is compared against the
declared baseline, and any unexpected object or column the adoption step cannot add is an
actionable startup failure naming exactly what differs. A table that is simply absent is
created — that is lossless and is what the pre-Alembic startup always did.

```bash
# Apply pending migrations explicitly.
.venv/bin/python -m alembic upgrade head

# Create a revision after changing a model.
.venv/bin/python -m alembic revision --autogenerate -m "what changed"
```

CI asserts that models and migrations agree: an autogenerate diff against a freshly
migrated database must be empty. That is what catches a table added to the models without
a revision, which happened twice before migrations existed.

**Downgrades are tested, not promised to be lossless.** A revision that adds a table
drops it on the way down, and the data with it. For production, restoring a backup and
redeploying is usually safer than reversing a populated migration.

Adoption compares against a **frozen snapshot of 0001**, not the live models
(`backend/migrations/baseline_schema.py`). That file is generated once and never edited
when a model changes: a model change gets a new revision instead. Comparing against live
metadata would repair a legacy database to a later revision's shape and then stamp it as
0001, so the migration that introduced those objects would collide on the next upgrade.

The comparison covers tables, columns, types, nullability, primary keys, unique
constraints and indexes. Missing non-unique indexes are created during adoption, since
that is additive and lossless. Missing unique constraints are refused: adding one to data
that already violates it is an operator decision, not a startup decision.

No component creates schema. `Store`, `install_auth` and `scripts/auth_admin.py` all
verify the database is at head and refuse otherwise.

**Scope.** This covers the main application database only. The knowledge management and
search services keep separate databases with their own metadata and still use
`create_all`; they need their own migration strategy before production claims.

SQLite is exercised in CI as a development compatibility check. PostgreSQL is the
authoritative target and needs its own profile before production claims.

### What each budget counts

Two budgets govern two kinds of spend, and every paid call belongs to exactly one.

| Budget | Counts | Charged |
| --- | --- | --- |
| `AGENT_RUN_BUDGET` | Tool calls, knowledge-base retrieval and query, specialist delegation | First attempt and every retry. Replaying an attempt already paid for — an approval continuing, a settled write returning its stored result — is not charged again. |
| `RELAY_RUN_TOKEN_LIMIT` | Every model call: a standalone LLM node, an agent's planning step, a structured-output repair, query generation | Provider-reported tokens, recorded as soon as the call returns |

An LLM node is not an action, and neither is an agent in itself: the agent is a
container whose tool calls and delegations are charged individually and whose planning
calls are model spend. Charging model calls as actions too would bill one call twice.

**Durability.** An action is reserved in the database *before* the external call starts,
and if that reservation cannot be written — the lease has been lost, or the database is
unavailable — the call never starts. Tokens cannot be reserved in advance, because the
count is only known once the provider responds, so they are written the moment the call
returns. The remaining gap is the interval between a response arriving and that write; a
worker that dies inside it leaves at most one call's tokens unrecorded. A resumed run
restores both budgets from the database, so it continues spending rather than starting
over.

Provider-level retries inside a single model call are covered by the token ceiling
checked before that call, not re-checked per retry; a call already in flight is allowed
to complete.

## Request router

`request_router` is a contract-2 flow node after the greeting cache. Configure a purpose,
accepted request descriptions, exclusions, clarification rules and examples, and select
an enabled model belonging to the workspace. Demo mode cannot classify requests and
fails with a model-selection instruction. The existing Agent `router` role continues
to mean specialist delegation.

The node sends the original message as separate user data and the saved brief as system
rules. It has no tools, specialist delegation or write capability. `proceed` maps to
`continue`; `clarify` and `out_of_scope` map to `reply`, which must terminate at a Response.
All three public values (`branch`, `reply`, `decision`) remain model-derived guesses.
A proceed verdict is admission to read-only extraction/checking, never approval of a
purchase, refund or external write. Full verification and human review land separately;
the editor's complete admission palette and new-workflow starter are not activated yet.

The initial response and at most one structured repair use the existing provider,
credential authorization, token ceiling and durable usage records. Both calls count
toward tokens, with no separate router action budget. Sampling settings use provider
defaults, so models that reject optional temperature/top-p parameters remain usable. Invalid output after repair,
provider failure, token exhaustion, cancellation and lease loss do not default to proceed.
The maximum legal saved brief is accepted by a private bounded call configuration;
the public plain LLM node's prompt limits are unchanged.

A router checkpoint carries runtime-created `_routing` metadata containing SHA256
digests of the exact input, full router settings and validated decision. Replay checks
those digests and the branch/reply consistency, then uses the saved decision without
another model call. A missing, malformed or mismatched record refuses resume before
downstream execution: start a new run. These digests are audit bindings, not verification
receipts or evidence of semantic correctness. Routing verdicts are never stored in Redis.

`request_router.decision` journals only the node and route code; `request_router.repair`
records only `invalid_output`. Prompts, input text, replies, reasons and credentials are
not added to structured journal lines. Normal run history still contains the configured
workflow and its outputs as it does for other nodes.

**Measured limitation.** The Step 4 exploratory `llama3.1:latest` smoke check matched
4 of 7 expected routes: one complete greeting-prefixed request was sent to clarification,
and two questions produced invalid decisions even after repair. Those invalid outputs
failed without downstream execution. A separate repeat of one question succeeded, so
these observations are not a deterministic accuracy guarantee. Full multi-workflow
calibration is Task 8; deterministic extraction checks and business enforcement are
Tasks 5–6. Do not treat the router alone as a correctness or authorization boundary.

## Deterministic extraction checking (pure layer)

Step 5 provides `verify_extraction` and verification receipt helpers. It does **not**
register a runnable verifier node, approve a write, or enable human review. Step 6 supplies
those execution boundaries. A model/handler must never be allowed to provide its own
`_verification` metadata or construct its own trusted evidence context.

The checker parses exact JSON, rejects duplicate keys, and checks schema, origin and
suitability separately. Missing required facts and malformed values yield `clarify`;
unconfirmed origin, unsuitable values or incomplete checking yield `needs_review`.
Only complete, successful field coverage yields `passed`. Quotation proves that a value
was written, not its business meaning. For example, the word “order” cannot pass a product
catalogue check merely because it appears in “order $450”. Yes/no inferred from prose
remains unverified; structured boolean origin uses the exact JSON Pointer.

`source_field` compares an exact Pointer in structured original input. `enum` checks a
configured domain but also requires origin. `reference_member` requires the exact resolved
reference from an explicit record-read adapter, scoped to the same tenant. Trigger data,
arbitrary HTTP/Python results, model output and request echoes are not independent
references. Runtime must authorize the read and construct this context; origin labels
alone do not furnish reference authority. Unrelated/changed reference data never qualifies.

Calculate checks establish consistency. An equality can establish a calculated target
only when the actual operands already passed their field checks; it cannot repair a known
source mismatch or establish the semantic roles of merely quoted operands.

Each original/value/reference input is capped at 262,144 UTF-8 bytes; traversal is limited
to 32 levels and 4,096 visits. Reports and receipts have at most 64 fields and 8,192
serialized bytes. Omitted/oversized coverage is marked incomplete and cannot automatically
qualify. Receipts bind exact original/value bytes, run, verifier node, policy rules and
reference scope. Successful construction also checks that the fields cover the actual
value. Empty successful reports are invalid. Consumers must pass the expected verifier
node and independently validate real approval records; a supplied review id is not proof
that approval occurred. Checksums are audit bindings, not cryptographic signatures.

Schemas use exact existing number validation. Local references are checked with a bounded,
cycle-aware traversal, including targets beneath arbitrary keys. Remote resolution is
blocked independently at the validator registry. Regex `pattern`/`patternProperties`
checks are unavailable because Python regex execution has no deadline; inline remote
schemas under local definitions and use supported deterministic field rules. This is
reported as unavailable checking, never silently ignored or converted to a pass.
JSON Schema formats remain annotations under the existing validator; they are not a
substitute for business suitability rules. Existing JSON schema numeric-bound precision
limits at API parsing remain as documented in `typed-conditions.md`.

## Request admission observations

Revision `0005_admission_metrics` stores only stage/status/outcome/replay codes in
`run_admission_metrics`, in the same fenced transaction as the event. Operator metrics
return deployment-wide `request_admission.outcomes` and `checkpoint_replays`. Counts
start with this revision; historical events are not backfilled. Cached checkpoint
replay is separate from a Redis prepared-reply hit. Human confirmations remain in
`human_reviews.by_kind` and never turn model-origin fields into source evidence.
No original message, reply, extracted field, credential or Redis URL is returned.

The request measurement uses isolated API/worker processes and synthetic local
reference/receiver systems. Run the frozen live suite with:

```bash
.venv/bin/python -m scripts.eval_request_admission --profile live --out evals/results/request-admission-new-run
.venv/bin/python -m scripts.check_request_admission_eval evals/results/request-admission-new-run/report.json
```

`--profile fault` uses a model HTTP fixture and proves verification boundaries with
three real positive delivery controls. CI runs this profile, which cannot certify
live model accuracy. A live release requires zero unsafe automatic cases, all ten
configured whole-message greetings without model calls, and at least 18/20 expected
valid requests admitted. The suite, companion fact labels, model and policy hashes
are retained; model replies/failed runs are never discarded to make a score pass.
Interpretation or business-decision confirmation must match the exact saved run and
consumed checkpoint. Writes are checked for exact body, case/branch destination and
no duplication. These remain finite-sample tests. Low admission means no production
release recommendation, even if safety tests pass; do not lower the gate.
