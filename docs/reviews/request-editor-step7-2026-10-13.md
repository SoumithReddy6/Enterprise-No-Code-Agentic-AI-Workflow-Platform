# Step 7 — workflow setup and typed-review screens

Implemented locally on base `6cb595e68bf453ee4c33071eecdd1d090900a618`.
All five hosted jobs for that Step 6 base passed in run `38288903621` before work
started. Step 7 remains uncommitted for manual user integration; its hosted CI
and the Step 8 live-model acceptance measurement remain pending.

## Delivered

- New contract-2 Q&A starter with exact prepared replies, model request routing,
  an Agent and terminating reply branches. Q&A accepts broad questions and makes
  no business-verification claim about narrative text.
- New-workflow setup for purpose, accepted requests, examples, exclusions and
  clarification rules; business templates also expose schema, required fields,
  field rules and cross-checks. Both model nodes select an enabled model on new
  creation; imports preserve their settings.
- Purchase, support/ticket, refund and yes/no drafts using generic nodes. They
  configure no external writes. Membership examples use explicit read-only Jira
  nodes and documented test-only response fixtures, never embedded fake authority.
- Prepared-reply versus model-routing explanations, checkpoint replay, missing
  fields, actual check outcomes, original/checked values and recorded human choices.
  Router continue is explicitly not verification or business approval.
- Distinct interpretation accept/reject, explicit true/false decision selection,
  and outgoing-write approval. Rejection is not false. The digest hashes the exact
  displayed backend `payload_json`, preserving Unicode and exact numeric text.
- Expiry while open, disabled expired/malformed/unknown reviews, and digest refusal
  feedback beside the review. All panels share the response scroll region.

## Independent review and fixes

One fresh-context, read-only reviewer ran 13 routing units and 21 backend example/
parity tests. It found two Important issues, no Critical or Minor issues:

1. A delayed confirmation success/error could reclaim a different opened run and
   leak the first run's error into it. Two browser reproductions failed before the
   fix. Run-view generation checks now guard hashing, response updates and recovery.
2. A reused JSON editor could retain the prior router's raw draft while a new
   workflow supplied different settings. Its browser reproduction failed first.
   Draft formatting now belongs to the exact value it represents, so changed
   document/undo/reload values cannot display stale text.

All three browser reproductions pass. No second review was dispatched. An additional
author check found shared nested template defaults; a strengthened independence
test failed before builders cloned each draft's schema/rules/policy. JSON settings also reject overflowing JavaScript numbers instead of silently serializing them as null; raw drafts remain saveable.

## Evidence

| Check | Result |
|---|---|
| New backend examples, worker execution and fixture parity | 21 pass |
| Full backend, hash seeds 0/1/2 | 1,606 pass + 1 optional Redis integration skip each; exit 0 |
| Final clean Git export plus exact implementation overlay | Backend 1,606 pass + the same skip; frontend units/typecheck/lint/build exit 0 |
| Frontend unit tests | 93 pass |
| Full browser suite | 31 pass, including review races, JSON drafts, expiry, exact digests and layout |
| Deliberately broken implementations | 12/12 caught; browser failures require actual assertion failures, not server startup errors |
| Existing separately deployed API/worker scenarios | 20/20, no backend/scenario source changes during the run |
| Dependency gate | Pass with accepted residual: 6 high findings on reviewed braces chain, expiry 2026-12-04; not a clean audit |
| `git diff --check` | Clean |

The skipped case is `test_real_redis_expiry_poison_and_tenant_policy_namespaces`:
`RELAY_TEST_REDIS_URL` was not set locally. Its CI profile ran successfully on the
Step 6 base; no Redis implementation/dependency changed here. This is reported as
a skip rather than included in the local pass count.

Evidence: `evals/results/request-editor-step7-2026-10-13/`. Final implementation/
example hashes match the clean export; compiler imports were confirmed from it.
Only documentation/checklists were completed afterward. Mutation/browser testing
used throwaway exports and an isolated frontend on port 3317, not real run history.
The reviewer did source-level lifecycle reproductions; the parent performed actual
browser gesture reproductions, RED→GREEN, and inspected the layout screenshot.

## Decisions and remaining limits

- Q&A needs admission, not a fictitious business verifier. Business drafts expose
  checks and require actual source setup; cost: conservative clarification/review.
- Yes/no cannot correlate eligibility with a record using boolean membership alone;
  the generic draft requires a person. A suitable domain checker is separate work.
- Missing connections/failed reads stop; empty or mismatched records reach review.
  Illustrative project names/pointers must be adapted to the user's real records.
- Existing workflows are not converted. Older examples remain available.
- Reviewer declined live semantic accuracy (Step 8), final release/hosted CI
  (pending integration), real deployment Jira mapping (documented configuration),
  forged backend objects (server integrity is authoritative), and browser gestures
  (covered by the parent's reproductions). None is silently presented as certified.

These drafts/screens are functional enforcement examples, not a release claim that
the LLM understands every workflow. Measure the fixed and held-out suite in Step 8
after the exact Step 7 commit passes all five hosted jobs.
