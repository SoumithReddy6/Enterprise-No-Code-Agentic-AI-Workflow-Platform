# R01 Response boundary, revision 3 — re-review

Plan commit: `9801c46`. Application baseline remains `812fefd`; intervening dependency
upgrade `7d910de` changes no application Python files. Review date: 2026-10-31.

**Decision: ready for implementation. No additional blocking finding in the reviewed
paths.** Keep D1=A, D2 and D3. The revised publication check closes R2-01 in the disposable
prototype, and U9 now describes the actual baseline behavior. This approves the plan,
not a completed product fix: R01 remains open until implementation and the closing gates.

## Why the revised design fits

Section 5f carries the already-durable item dependency lists into the current attempt and
records owners at both reuse points. Section 5b checks their canonical union with displayed
sources immediately before Response returns. That closes the specific case in which a
source is valid at loop reuse, then disappears before publication while the presentation
source list is empty. The supplementary dependencies are not displayed, so fixing authority
coverage does not require reconstructing citation labels.

This fits the existing worker/lease/dependency design: reuse the authoritative verifier and
saved records, with no new model, schema or persistent store. The typed fatal refusal also
keeps the safety decision out of retry and recovery under all three `on_error` settings.

[Saltzer and Schroeder's complete-mediation and fail-safe-default principles](https://web.mit.edu/Saltzer/www/publications/protection/Basic.html)
support checking current authority at publication as well as recovery. They do not prescribe
Relay's exact `reused_owners` implementation. That implementation choice is supported here
by the existing data paths and the experiments below.

The residual interval after a source's final check and before checkpoint commit remains
documented, consistently with [CWE-367](https://cwe.mitre.org/data/definitions/367.html).
This plan does not provide atomic document revocation and publication; no such claim is
needed to close the identified missing-check paths.

## Prototype verification

I exported `9801c46` into a disposable directory and applied the planned behavior there.
The real checkout's application, submitted plan and previous evidence were unchanged.
The probe uses real temporary KB ingestion/removal, management/search/index verification,
Worker and SQLite; only the model response and Jira HTTP transport are substituted.

Eleven Worker cases and two direct contract cases completed with their expected results:

| Case | Observed |
|---|---|
| Removed source before a fresh Response, `fail` / `continue` / `route` | Three named fatal refusals; empty output; no Response checkpoint |
| Removed source before restoring a saved Response | Restore refused; no output republished |
| Removed source before loop-checkpoint resume | Refused naming `each:0`; one provider call total |
| Removed source before partial-progress resume | Refused naming `each:0`; one provider call total |
| Uninterrupted valid loop | Success, one displayed source, one provider call |
| Valid loop-checkpoint resume | Success, no repeated provider call; Response verifies the hidden saved dependency |
| Full loop restored, Prompt commits, document removed, then Response | Named refusal, empty output, no Response checkpoint; one provider call total |
| Item 0 restored from progress, item 1 runs fresh, Prompt commits, item 0's document removed | Named refusal, empty output, no Response checkpoint; two provider calls total |
| Same mixed reused/fresh case with no removal | Success; Response verifies both documents; two provider calls total |
| Nonempty evidence without a verifier; malformed cached `"{}"` | Both refused (direct contract probes) |

The mixed case uses separate documents: item 0 is about the Bluebird codename; item 1 is
about unrelated handbook material. The Response's displayed source is item 1's, while
item 0's authority comes through the saved dependencies. This prevents fresh evidence from
accidentally covering the old document and hiding a missing partial-progress marker.

The existing citation difference remains visible in the unchanged controls. The reused
item's `[S1]` is unsupported in the displayed output, but its saved source is verified.
That is consistent with the revised U9 and the explicitly separate citation work.

## New mutants

| Mutant | Full-checkpoint sequence | Partial-progress sequence |
|---|---|---|
| N14: check reused dependencies only at resume, omit them at Response | Caught: unsafe success | Caught: unsafe success |
| N15: record owners only from restored loop checkpoints | Control still refuses correctly | Caught: unsafe success |

Each caught run fails the assertion because the forged implementation publishes
successfully, not because of an import/setup error. The N15 full-checkpoint control passes,
which isolates the missing partial-progress behavior. The first local mutant harness run
resolved the virtualenv Python symlink to the base interpreter and failed to import
`httpx`; the harness rejected that as invalid evidence. Preserving `sys.executable` fixed
the setup, and all four final checks above ran to their intended outcomes.

## Implementation/test clarification

U23 says item 1 runs fresh but inherits U22's "same assertions," which includes one provider
call total. For a Query body with two nonempty results, the correct assertion is **two
provider calls total**, one per item, with item 0 never repeated. Make the two source
documents distinct, as above. U24 should assert that the Response checks item 0's document
specifically, rather than merely asserting that some verifier call happened. This is a test
specification clarification, not a reason for another design revision.

Keep the planned baseline defect tests, remaining mutants and full closing checklist.
The prototype probe is not a substitute for implementing U1–U24 and running the regression,
isolation, scenario and hosted-CI gates on the actual implementation commit.

## Artifacts and scope

`evals/results/r01-plan-r3-review-9801c46/` contains the prototype builder, probe, results,
two-mutant checker, final mutant outcomes and preservation hashes. Logs stay local.

No application implementation or full-suite/production/hosted-CI run was performed for
this plan-only re-review. No real user data, live model or external write was used.
TA04 remains closed; accounting, retention, citation restoration and terminal-route
validation remain separate. The submitted revision 3 is already committed as `9801c46`.
