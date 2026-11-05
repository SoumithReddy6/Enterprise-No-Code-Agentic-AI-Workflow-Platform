# TA03 implementation review

Date: 2026-11-03, America/New_York.
Reviewed commit: `dfbadf92374acd6387edde814f3f232db2216584`.
Design: `docs/superpowers/specs/2026-11-02-token-outcome-attribution-design.md`.
Evidence: `evals/results/ta03-review-dfbadf9/`.

## Verdict

Approved under the P2 tier. No blocking implementation finding. TA03 can close on this
commit. One documentation count correction is listed below.

## Verified implementation

- The three compiler emitters that used inclusive owner totals now obtain usage from
  the current invocation. Success, ordinary failure and approval-pause paths already
  obtained invocation usage. Per-model rows sum the resulting disjoint inputs.
- Agent-attached targets and loop bodies report their own known usage before cancellation
  unwinds. Invocations that have no usage inherit none. Typed unsafe/accounting refusals
  retain their stop semantics while reporting paid work.
- The Worker issues a receipt only after a confirmed outcome commit. The receipt names
  the invocation, including transient children and paused outcomes. Each catcher matches
  that identity before suppressing an additional cancellation report. A saved child's
  receipt preserves the parent's separate usage report.
- False fence acknowledgments retain the existing cancellation path. The fence rejects
  attempted unwind writes before changing usage rows or accounting. U06 verifies both.
- The compiler resets per-invocation usage each worker attempt. Resumed cached work adds
  no usage, while a loop item whose outcome committed before its missing progress is
  executed again and contributes new real usage. U07 verifies that distinction.
- The changed cancellation-test selector retains the original Agent token assertion and
  adds the assertion that the cancelled tool has no usage. The narrower selection is
  necessary because the tool now has its own cancellation outcome.
- The authoritative TA02 snapshots, atomic save boundary, pending-save gate and monotonic
  guard continue to operate through the existing persistence paths.

## Independent verification

| Check | Result |
|---|---|
| Focused tests across attribution, TA02, operations, policy, metrics, token recovery, iteration and loop checkpoints | 179 passed, exit 0 |
| Five original diagnostic scenarios, using current code without the diagnostic counterfactual | Correct totals for cancellation after a child, cancellation inside a child, cancellation during a child save, unsafe finalization and uncertain finalization |
| Ordinary nested success | 45 known tokens and 45 metric tokens |
| Specialist cancelled inside a loop body, two models | 30 tokens, two measured calls, 15 attributed to each model, unfinished item has no saved progress |
| Cancellation during helper and root pause-event acknowledgment | Both report 30 tokens exactly once, with no sent request |
| Real run deadline armed at helper and root pause-event commit barriers | Both preserve 30 tokens and the named timeout failure |
| Hosted CI run `41771309836` | Five jobs passed on the reviewed SHA |
| Hosted backend logs | Both suite runs report 1,985 passed and two skipped, totaling 1,987 collected cases |
| Deployed report | 24 rows pass, zero source changes during the run, all 69 recorded source hashes match the current files |
| Protected review inputs | All 80 original source, design, test and evidence hashes match their pre-review values |
| `git diff --check` | Clean |

The independent probes use synthetic provider responses, the real Worker/Store and
disposable SQLite databases. The two deadline probes reschedule the real timeout only
after the event commit barrier is reached. They use no short elapsed-time requirement.

The implementation's five-seed, isolation and eight-mutant records were inspected. This
review independently reran the focused tests and eleven scenarios. It did not repeat the
entire five-seed or deployed campaign. Hosted CI provides the full suite, browser,
frontend, native-macOS and PostgreSQL results on the reviewed commit.

CI: https://github.com/SoumithReddy6/Enterprise-No-Code-Agentic-AI-Workflow-Platform/actions/runs/41771309836

## Deviations 1–5

1. Accepted. The baseline fixes correctly distinguish LangGraph's node cancellation
   wrapper, the pending-save reservation callback and the absence of loop progress.
   The final baseline records seven behavioral failures and six controls.
2. Accepted. Selecting the Agent's cancellation event preserves the assertion being
   tested as additional nested outcomes become visible.
3. Accepted. Publishing newly added failure outcomes only when own usage exists satisfies
   the attribution rule and preserves the tested claim-time refusal behavior.
4. Accepted with the count correction below. Both U03 parameters use the shared SQL row
   derivation function at compile level. The remaining eleven cases use Worker/Store.
5. Accepted. A budget-refused `not_run` item has no usage. Its possible additional
   cancelled status cannot duplicate consumption.

## Nonblocking documentation correction

**P3.** `evals/results/ta03-fix-2026-11-03/README.md:84` says "The other 12 cases use the
real Worker and Store." U03 has two parameters within thirteen collected cases, so that
number should be **11**. This changes the description of the evidence, with no effect on
the test results or implementation approval. The original evidence remains intact.

## Documented production pattern

Genkit attributes response usage to individual model operations, and LangSmith presents
parent aggregates alongside child usage. The implementation applies that distinction
through Relay's invocation counters, so the SQL sum receives disjoint contributions.
This is an appropriate adaptation for the approved scope.
[Genkit conventions](https://genkit.dev/docs/observability/otel-genai-semantic-conventions/),
[LangSmith cost tracking](https://docs.langchain.com/langsmith/cost-tracking).

The design's limits remain applicable when a response has no usage, a process is killed
before publication, a commit is unconfirmed, or a lease is lost. The review covers the
selected new-run attribution behavior and the preserved control-flow guarantees.
