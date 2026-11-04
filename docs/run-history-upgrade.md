# Run history and bounded agent execution

## Upgrade procedure

Stop the API and every workflow worker before upgrading. Back up the workflow database (for SQLite, use its backup command after stopping writers; for PostgreSQL, use your normal database backup). Start one new API/worker process to complete the startup migration, then start the remaining processes. Do not run old and new binaries against the migrated database together.

Startup transactionally expands legacy `runs.data.events` into `run_events`, preserving event sequence, timestamps and payloads. It backfills indexed run creation time/status/name and the citation high-water mark. The migration marker prevents repeat history scans. Invalid ordering aborts the transaction with the affected run ID; repair the backed-up source data deliberately before retrying. Rollback to the old binary requires restoring the database backup because the old binary cannot read event rows.

## Behavior

- A tool/step budget limit triggers a final grounded answer from accumulated evidence, with `truncated` and a reason on the run. Empty evidence produces abstention; empty-evidence nested exhaustion is a failed specialist observation and marks the parent incomplete. Existing one-repair contract validation still applies and can fail invalid model output.
- Event appends insert one payload row under the job lease lock. Successful node checkpoints still update run state. Detail responses preserve the existing ordered `events` array; SSE reads incremental batches of 200.
- `GET /api/runs/page?limit=100` returns `items` and `next_cursor`; pass that cursor to retrieve older runs. The legacy `/api/runs` list still returns the newest 100. The editor exposes **Load older runs**. Listing uses an indexed creation-time/ID seek and SQL limit; its cost is bounded by a page plus index lookup, not literally constant with database size.
- The live evidence registry holds at most 300 passages. Oldest uncited passages are evicted first; cited and actively used agent passages are protected. If all slots are protected, new passages are excluded from retrieval output. Warning events report eviction/rejection counts. Citation numbers are never reused after a published retrieval, including across failed-attempt resume.
- Checkpointed node outputs are retained for exact replay. The 300-passage limit bounds the live registry, **not total checkpoint JSON bytes**. Evicting checkpoint contents would change downstream input bindings. Legacy checkpoints with more than 300 cited passages fail explicitly rather than losing citation support.
- Queue scheduling remains unchanged; per-tenant fairness is deferred.

## Verification

Regression coverage includes capped partial answers and abstention, visible nested exhaustion, event-only SQL writes, 60 concurrent ordered appends, stale-worker/tenant isolation, transactional migration rollback and retry, checkpoint resume, incremental SSE, and 3,000-run cursor pagination with indexed SQL and no ORM run-payload loading. Evidence tests cover oldest-first eviction, cited/pinned retention, full protected capacity, warning events and label restoration across retries.
