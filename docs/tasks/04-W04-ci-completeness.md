# W04 — CI completeness

| | |
|---|---|
| Order | 4 of 29 (Phase 0) |
| Closes | X3 |
| Depends on | nothing |
| Effort | S |
| Spec | `docs/gap-audit.md` §2.1 and W04. |

## Goal

CI runs the deployed scenario gate on each push and the Repeat crash checks; the A6.1 Part 3 review is on record.

## Files to touch

- `.github/workflows/production-scenarios.yml:2-3` (add `push` and `pull_request`)
- `.github/workflows/ci.yml:126-130` (add `postgres-repeat-sigkill`)
- `docs/reviews/a61-part3-review-<date>.md` (record the review that was done)

## Tests to write first

No unit test. Proof by a scratch branch:

1. A mutant commit that skips `mark_write` (`worker.py:203`) makes the scenario workflow fail at S16.
2. A mutant that ignores saved Repeat rows makes `postgres-repeat-sigkill` fail.

Record both runs in the pull request text. Do not merge the mutant commits.

## Commands to verify

Push the branch and read the six job results with `gh run list` (network: ask first).

## Invariants

None touched.

## Done when

- Six jobs pass on the branch; the two mutant runs failed at the named rows; CI wall time for each job is recorded in `docs/status-report.md`.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W04 (CI completeness).
Read docs/tasks/04-W04-ci-completeness.md first, then every file it links.
Change only workflow files and docs.

Rules for this session:
- Read the card, the linked spec and the cited code first. Do not make design decisions that the spec already made. If the spec and the code disagree, stop and ask.
- Write the tests first. Show that each fails on the parent commit for the reason the card states.
- Never weaken an existing invariant (I1-I8 in docs/gap-audit.md section 6) or an existing test to make a new test pass. If they conflict, stop and ask.
- Run mutants only in scratch copies, with an M00 no-change control and a guard that fails when no test ran.
- Ask before any run longer than 5 minutes, any network access, or any API key use. Use a throwaway PostgreSQL container, never the user's database or .data directory. Never use git stash.
- Give exact git add and git commit commands; no Co-Authored-By line; the user commits.
- After the gate passes: update the status table in docs/gap-audit.md section 11, add only verified numbers to docs/resume-evidence.md, and remove the xfail marker of a defect test only in the commit that fixes it.
- Write replies in about 80% Simplified Technical English.
```
