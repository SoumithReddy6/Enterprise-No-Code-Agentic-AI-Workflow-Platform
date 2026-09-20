# Partial answers and scalable run history

**Goal:** Implement the user's items 1–4 in order; defer tenant queue fairness.
**Architecture:** Keep existing node output and run-detail shapes. Put truncation metadata in the existing grounding JSON and warning events. Store ordered events separately from run state with lease-fenced transactions. Expose indexed keyset pagination through an additive endpoint while preserving the existing list endpoint. Bound the citation registry and preserve cited/in-use evidence with monotonic labels.
**Spec:** User's September 19 request, items 1–4. Existing grounding, tenant isolation, cancellation, write reconciliation, and checkpoint validation must remain enforced. No model/threshold changes or remote publishing.

## Task 1 — Budget exhaustion
- [x] Add failing tool-loop tests for a capped grounded answer, empty-evidence abstention, nested empty-evidence failure observed by parent, and visible nested truncation.
- [x] In `agent_runtime.py`, check exhausted budget before another tool action; issue one final-only model call using accumulated canonical evidence, or abstain without evidence. Nested empty-evidence exhaustion raises a specific error that the parent records. Keep bounded JSON repair.
- [x] Emit a transient warning with `truncated: true` and persist truncation reason in grounding metadata. Preserve the existing four string outputs.
- [x] Run agent/compiler tests and review the independent change before storage work.

## Task 2 — Event rows and migration
- [x] Add `RunEventRecord` with `(run_id, seq)` primary key and tenant/timestamp/type/payload fields. Migrate legacy arrays atomically, preserving exact sequence and timestamps; strip arrays from run JSON only after inserts succeed. Use a migration marker so startup does not deserialize all history repeatedly.
- [x] Append under the existing job lock/fence using indexed maximum sequence allocation; no event-only update of run JSON. Run-detail reads reassemble ordered events; SSE queries only events after its cursor.
- [x] Test SQL write shape, concurrent appends, cross-tenant reads, stale-owner rejection, idempotent legacy migration, cancellation and resume.

## Task 3 — Indexed history
- [x] Add indexed run created_at/status columns and backfill with event migration. Keep status synchronized with lifecycle transitions.
- [x] Select summary fields only, order by `(created_at DESC, id DESC)`, limit in SQL. Add an opaque validated keyset cursor and an additive page endpoint, retaining `/runs` as a list for compatibility.
- [x] Add a history Load more control. Test 3,000 seeded rows, tied timestamps, no duplicate/missing pages, tenant boundaries, bounded fetched rows, query plan and elapsed time (generous non-flaky ceiling plus exact SQL assertions).

## Task 4 — Bounded evidence
- [x] Move registry policy to a focused module. Limit 300 passages, evict oldest uncited/unpinned entries, never reuse labels. If all slots are protected, reject new admissions visibly rather than dropping cited evidence.
- [x] Pin evidence while an agent uses it; promote actual citations permanently. Retrieval returns only admitted passages. Restore labels and cited state from checkpoints before allocating fresh labels. Emit eviction/capacity notices through the normal event path.
- [x] Test overflow, cited/in-use retention, all-protected capacity, Response citation resolution and checkpoint resume without label reuse.

## Review focus and completion
- [x] Migration interruption must roll back without losing an event; duplicate migration must not duplicate events.
- [x] Fenced stale workers must not insert events or change indexed status.
- [x] Nested exhaustion must not be hidden as an ordinary successful complete result.
- [x] Pagination must not deserialize workflow/checkpoint payloads for thousands of rows.
- [x] Evidence caps must never cause a new source to inherit an evicted citation label.
- [x] Run complete backend/frontend suites, typecheck, lint/build as appropriate; inspect reviewer findings; document measured results and limits. No queue fairness changes.

## Verification result

304 backend tests passed (4 existing dependency deprecation warnings). Frontend: 24 tests passed; typecheck, lint and production build passed. Review found a failed-attempt citation-label reuse issue; fixed with a persisted high-water column and resume regression test. Queue fairness deferred. The registry is bounded to 300; checkpoint node-output copies remain intact for replay and are not a 300-entry global snapshot. See `docs/run-history-upgrade.md` for migration and retention limits.
