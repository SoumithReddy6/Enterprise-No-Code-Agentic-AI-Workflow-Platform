# TA07 implementation review — 2026-11-05

**Commit:** `c3ee703be9c933442504580d4316ee0ecaaddf17`.
**Design:** `docs/superpowers/specs/2026-11-05-cache-input-tokens-design.md`.
**Evidence:** `evals/results/ta07-implementation-review-c3ee703/`.
**Verdict:** implementation approved; no blocking findings. All five hosted CI jobs passed
on this exact commit. TA07 is closed within its approved scope.

## Contract and implementation

- Claude sums base input, cache reads and cache creation once. `_claude_input` separates
  the known subtotal from completeness, and `CallUsage.complete` prevents a partial
  subtotal from appearing reported. Empty receipts remain unreported; absent/null cache
  defaults do not manufacture a known zero. This implements TA07-D01.
- OpenAI and Ollama retain their authoritative prompt totals. Cache details are accepted
  only when valid and within that total. Missing/null optional details do not discard the
  other valid detail. Malformed annotations leave the total and status intact. This
  implements TA07-D02.
- The C01 test exercises the actual ceiling: a valid response consumes 1,015 tokens and
  the next model node is refused under a 1,000-token ceiling before a second HTTP request.
  Breakdown values are not charged twice.
- The breakdown reaches owner totals, invocation totals, per-model totals, durable
  snapshots, usage-bearing events and the `model.usage` journal. SQL continues reading its
  fixed fields. No migration or change to token-budget arithmetic is needed.
- The journal accepts only valid integer cache counts and the four generated status
  strings. Null/list/dict statuses are safely removed, closing the TA06 hardening note.
- No existing test changed. The ten collected cases and nine mutants match the approved
  design. The two recorded deviations explain a stronger empty-receipt oracle and the
  optional helper arguments; neither weakens a requirement.

## Independent checks

Fresh focused suite: **111 passed**, covering the new cache tests, TA06 status tests,
provider compatibility, token budget, TA03 attribution, TA02 durability and observability.

Additional edge probes: **9 passed**. They cover 24 literal parser/detail boundary rows,
plus seven real Worker/Store executions:

- valid zero, missing input/output, malformed strings/booleans/containers and partial
  cache contributions;
- a TTL breakdown counted only through its aggregate field;
- annotations equal to or greater than the inclusive total;
- rejected answers retaining complete or partial cache usage;
- malformed OpenAI detail containers and null optional details;
- nested cancellation with the parent's cache read 10, child's read 20/write 3, and
  grandchild unanswered: SQL reports 37 known tokens, with each invocation's annotation
  attributed once;
- cancellation while a committed checkpoint is acknowledged, then resume: two requests
  total, 1,018 known tokens, and no replayed charge or repeated cache annotation.

These checks use synthetic HTTP and temporary databases. No live model request or
application-source edit was made.

## Evidence boundary and closing gates

The builder's recorded baseline failures, nine mutant kills, five full 2,012-test seed
runs, six-file isolation proof and 24-row deployed report were inspected. The source
hashes are compared against the reviewed tree in `checks.json`. They are distinguished
from the fresh independent tests above; the full local campaign was not repeated.

The original design, builder evidence and application files are preserved. Exact-SHA
hosted status is recorded in `hosted-ci.json`, separate from local results. Cached tokens
remain token quantities rather than prices. Strict reservation, streaming, cache-ratio
metrics and historical backfill remain outside this approved scope.

[Hosted CI run 42124859362](https://github.com/SoumithReddy6/Enterprise-No-Code-Agentic-AI-Workflow-Platform/actions/runs/42124859362)
passed backend, frontend, browser-layout, native-macos and postgres-conformance. Both
hosted backend suite runs report 2,010 passed and two platform skips (2,012 collected).
The schema check passed 25 tests; frontend passed 115 tests, browser-layout 49 tests, and
native-macos 16 tests. Typecheck, lint, build and the fault-profile check also passed.
