# R01 Response boundary, revision 2 — re-review

Reviewed plan commit: `e50fa65`. Application baseline: `812fefd` (unchanged).
Review date: 2026-10-31. No application, submitted plan or earlier evidence was edited.

**Recommendation: retain D1=A, D2 and D3, but close the remaining publication gap below
before implementation.** The three earlier corrections are represented properly. One
combination of two existing scenarios is still unsafe: loop reuse passes, then the source
is removed before a fresh Response. There is also one incorrect control-test expectation.

## R2-01 — P1: reused-loop dependencies still never reach the publication check

Plan locations: section 5b, lines 127–130; section 5d, lines 152–154; U3/U5/U9,
lines 186–197. The goal at lines 10–18 applies the publication rule to reused loop work.

D2 correctly checks saved item dependencies at resume and loop reuse. But section 5b
still checks only the source list returned by `ground_answer`. A restored loop has
`results`, `failed` and `summary`, with no `sources` output. Consequently
`agent_runtime.evidence_from_outputs` cannot restore that loop's evidence into the shared
registry. The final Response's list is empty even while it publishes text derived from
the saved knowledge result. A valid empty presentation list does not prove that the answer
has no knowledge dependencies.

I applied the proposed changes in a disposable archive export, then exercised this flow
with real KB ingestion/removal, source verification, Worker and SQLite:

```text
Input → Jira list → For each (Query body) → Prompt formats each.results → Response
```

1. Query retrieves a real document and answers `Bluebird is the project codename. [S1]`.
2. Crash immediately after the loop checkpoint commits, before Prompt/Response.
3. Resume while the document still exists. All four recorded resume/reuse verifier calls
   succeed. The cached loop is reused without repeating the model call.
4. Let the downstream Prompt finish and commit its success event.
5. Remove the document through the real management service, before Response runs. A second,
   unrelated document keeps the KB searchable.
6. Response sees an empty registry, calls no verifier and successfully publishes the old
   answer text. It changes `[S1]` to `[unsupported reference]`; the protected fact remains.

Observed on the revision-2 prototype:

```text
run status: success
Response checkpoint saved: yes
output contains Bluebird: yes
Response sources: []
verification calls after document removal: 0
provider calls across both attempts: 1
```

This is the same removal-before-Response behavior U3 protects for an Agent/Query, applied
after U5's loop reuse. It is not the accepted interval *after the Response's own source
check*: that dependency is never checked by the Response at all. The document is removed
after both loop reuse and downstream Prompt checkpoint commits. No scheduling race, sleep,
malformed database record or new model behavior is needed to reproduce it.

**Required bounded change:** make the Response-time authority check cover saved knowledge
dependencies behind reused loop text as well as its displayed source list. Carry those
dependencies into the runtime publication check, or have that check consult the existing
records for reused items. D2 already chooses conservative coverage, so checking all reused
item dependencies again at each Response is consistent with that choice. Keep canonical
batching and fail-closed handling. No citation parsing, extra model or new schema is needed.

This does not require restoring citation labels to fix the security gap: authorization
dependencies and displayed citations may be handled separately.

**Acceptance:** add the exact sequence above; require a named fatal refusal, no new Response
checkpoint and empty run output, without repeating the Query/model call. Also cover reuse
from partial progress. A mutant that checks reused dependencies only at preflight/reuse,
and omits them at Response publication, must fail this test. Keep an unchanged-document
control. These cases stay within R01's existing publication invariant.

## Test note — U9's exact replay equivalence is not a baseline control

U9 is labelled C (passes on `812fefd`) while requiring identical output and sources after a
loop-checkpoint resume. I ran an unchanged-document fresh/resume pair both on the prototype
and on a separate, unmodified archive export. Both trees show:

| Case | Status | Model calls total | Response sources | Citation in output |
|---|---|---|---|---|
| Uninterrupted loop | success | 1 | 1 | `[S1]` |
| Resume from loop checkpoint | success | 1 | 0 | `[unsupported reference]` |

Thus the exact output/source equality assertion fails on the baseline and the proposed
revision. It is the same evidence-restoration fact behind R2-01, not another accounting
defect. Completion with unchanged authority and no repeated calls is a valid control.
Either explicitly add accurate evidence/label restoration and classify that behavior as a
defect test, or narrow U9's C assertion to supported completion/call behavior and record the
pre-existing citation difference. Do not weaken the Response-time dependency check.

## Corrections that checked out

- D1=A and D3's `UnsafeExecutionError` fit the existing `CONTROL_FLOW` handling. On the
  prototype, source removal before a fresh Response causes the named refusal under each
  of `fail`, `continue` and `route`; no output or new Response checkpoint is published.
- D2 refuses a document removed before resume both after a full loop checkpoint and after
  item 0's progress commit. The error names `each:0`; no model call is repeated.
- A saved Response with a subsequently removed document is refused on restore.
- Nonempty sources without a verifier refuse; the malformed cached `"{}"` container is
  rejected. These are direct prototype checks, not a claim that every U12 shape was run.
- Retaining conflicting same-id records, explicit container validation and acknowledging
  the unbounded check/commit interval are appropriate plan corrections.

## Published guidance

[Saltzer and Schroeder's complete-mediation principle](https://web.mit.edu/Saltzer/www/publications/protection/Basic.html)
applies authority checks to recovery and warns against relying on remembered checks after
authority changes. Applied here, a successful reuse check cannot substitute for the planned
publication check when the document changes before that later use.

[CWE-367](https://cwe.mitre.org/data/definitions/367.html) explains why checks and use can
observe different states. The plan may document the final check/commit race without solving
atomic revocation in R01. That limit does not justify omitting an entire dependency from the
Response check. These sources support the invariant; they do not certify this implementation.

## Evidence and limits

Artifacts: `evals/results/r01-plan-r2-review-e50fa65/`.

- Nine real-Worker prototype scenarios: six expected refusals, two unchanged-source success
  controls, and the remaining publication bypass. Two direct contract probes.
- Two unchanged-source Worker controls on an unmodified baseline export, confirming U9's
  classification issue predates the proposed change.
- `probe-results.json` records the verification/removal timeline; `baseline-controls.json`
  records the baseline pair. Exit zero means the probe reproduced its stated expectations,
  including the unsafe success. It does **not** mean R01 is fixed.
- Synthetic provider/Jira responses; no live model, real user database or outbound write.
- Candidate changes exist only in a temporary export. The full backend suite, production
  campaign and hosted CI were not rerun for this plan-only audit.
- TA04 remains closed. TA02/03/05–07, retention policy and terminal-Response route validation
  remain separate.
