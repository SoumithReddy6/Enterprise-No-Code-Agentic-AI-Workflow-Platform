# Durable approvals for consequential tools

Implement the user's exact-payload approval specification. Existing side effects already use positional registry arguments; make write capability explicit without treating read operations as mutations.

- [x] Separate pure request preparation from delivery for HTTP, Email, Jira, Confluence and GitHub. Snapshot method, complete URL, query/body or SMTP sender/recipient/subject/body and connection reference; keep secrets encrypted, outside the public approval payload. Revalidate connection identity before sending.
- [x] Add tenant-scoped run_approvals with immutable payload SHA-256, bounded expiry, lifecycle state and durable result; job-lock transactions serialize approval, rejection, expiry, claim and dispatch. Default approval for authenticated runs, explicit per-node override. No network before approval; no duplicate sends after decision replay. In-flight uncertainty retains reconciliation protection.
- [x] Persist suspended agent frames (including nested delegates, tool action, transcript, evidence and budget), so approval resumes the selected call without new model planning. Ordinary graph checkpoints are replayed without re-execution.
- [x] Add authenticated tenant-scoped approve/reject routes with persisted rate limiting and exact approval ID/digest. Poll expiry from workers and enforce expiry on decisions. Rejection/expiry cancel cleanly and cannot be bypassed through ordinary resume.
- [x] Add pending-approval payload review, digest-bound controls, node approval settings, pending status and refreshed history in the editor.
- [x] Tests: no network at pause; released lease; exact prepared request; replay/idempotence/concurrent decisions; rejected/expired/digest/foreign tenant; agent and nested resume; changed connections; uncertain dispatch; frontend review rendering. Full suites and independent review before completion.

No real emails or external mutations will be sent during implementation. Test transports only. Remote exactly-once delivery cannot be guaranteed after a crash; ambiguous writes are never retried automatically.

## Verification

- Full backend suite: 361 passed (four dependency deprecation warnings).
- Approval-specific suite: 21 passed, including concurrent decisions, all five adapters, repeated specialist delegation, post-claim expiry, tenant isolation and rate limiting.
- Frontend: 26 tests passed; typecheck, lint and production build passed.
- Independent review found three issues (hidden HTTP query parameters, reusable specialist frames, post-claim expiry); all fixed and regression-tested. Follow-up review found no remaining high/medium blockers in reviewed paths.
- Delivery verification uses mock transports only. Running local services were not restarted as part of this change. PostgreSQL concurrency was not exercised against a live PostgreSQL instance; transactional concurrency tests used SQLite.
- Pending approval views have an explicit Refresh approval status control; worker-side expiry and decision-time expiry checks remain authoritative.
