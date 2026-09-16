# Phase two: local models and durable workspaces

User authorized Ollama support and moving to the next phase. This implements the previously proposed durable execution and tenant authentication phase while preserving the local launch flow.

## Ollama
Use a backend-owned OLLAMA_BASE_URL (default http://127.0.0.1:11434), native /api/chat with stream=false and /api/tags discovery. Model choice is stored in node configuration, never arbitrary service URLs. The existing LLM node gains an Ollama provider with no credentials. Show connection/model discovery errors. Verify a real call using an already installed small model; do not download models automatically.

## Durable execution
A relational job queue separates the API from a worker process. Atomic compare-and-swap claims and expiring leases prevent ordinary double claims. Workers heartbeat, observe durable cancellation requests and persist completed-node outputs before proceeding. Recovery traverses the same deterministic LangGraph but reuses successful node outputs from the saved execution snapshot, including condition decisions. An in-flight external call may be retried after a crash; this is at-least-once execution, not exactly-once side effects. Persist job ownership with fencing; stale workers cannot write run state or node results. Failed/cancelled runs can be explicitly resumed, preserving successful node results. Local development supports SQLite and PostgreSQL.

## Authentication and tenancy
Cookie-based signed-in sessions gate the editor/API. First-account setup claims preexisting local records in one transaction; subsequent accounts get isolated workspaces. Passwords are hashed, sessions expire and logout revokes them. Enforce tenant scope on workflows, credentials, runs, event streams, cancellation and resume; workers resolve only the run tenant's credentials. Same-origin cookie forwarding works in development and built frontend. Local bootstrap remains browser-driven: the assistant does not choose the user's password.

## Verification
Existing foundation tests plus Ollama protocol/error tests, real local Ollama smoke, tenant isolation, invalid sessions, bootstrap migration, persistence, exclusive queue claims, expired lease recovery, completed-node reuse, cancellation and resume. Frontend tests/typecheck/lint/build; PostgreSQL smoke for queue and tenancy. Keep public hosting, external identity providers, invitations, RBAC administration, Redis queue, native LangGraph checkpoint serialization, loops and parallel execution outside this milestone.
