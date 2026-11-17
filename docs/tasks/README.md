# Handoff cards

One card for each work item of `docs/gap-audit.md` and each defect of `docs/defects.md`, in the
order to do them. Each card has: goal, spec link, files to touch, tests to write first, commands
to verify, invariants, done-when, and a prompt to paste into a new coding session.

How to use a card: start a new session, paste the card's prompt, and let the session read the card
and its spec. One card for each session. A card marked "alone" is never combined with another.

Order rules:

- The two Critical/High defect fixes come first, because the README's first promise depends on
  DEF-01.
- The load baseline (W05) comes before every performance change, so that each one has a "before".
- W24 (write-intent table) is alone, by rule, and is gated by S03, S04, S16, S24 and the
  PostgreSQL SIGKILL checks.
- W23 follows W12: `self_approval = true` is temporary.

| # | Card | Title | Phase | Closes | Depends on | Effort |
|---|---|---|---|---|---|---|
| 1 | [DEF-01-02](01-DEF-01-02-agent-write-resume.md) | Agent write resume | before Phase 0: defect fix, Critical | DEF-01, DEF-02 | — | M (2–3 days) |
| 2 | [DEF-03](02-DEF-03-validation-exponential-time.md) | Validation exponential time | before Phase 0: defect fix, High | DEF-03 | — | S |
| 3 | [W03](03-W03-truth-pass-and-scope-freeze.md) | Truth pass and scope freeze | Phase 0 | §2.2 claims, Q4, X4, X5, E6 | — | S |
| 4 | [W04](04-W04-ci-completeness.md) | CI completeness | Phase 0 | X3 | — | S |
| 5 | [W01](05-W01-python-tooling-baseline.md) | Python tooling baseline | Phase 0 | Q1, Q5 | — | M |
| 6 | [W05](06-W05-load-baseline-harness.md) | Load baseline harness | Phase 0 | P1 | — | M |
| 7 | [W06](07-W06-database-clock-leases-and-heartbeat.md) | Database-clock leases and heartbeat | Phase 1 | C1, C2, part of C4, DEF-09 | W05 | S–M |
| 8 | [W07](08-W07-claim-rules-poison-runs-graceful-stop.md) | Claim rules, poison runs, graceful stop | Phase 1 | C3, C5, C6, DEF-04, DEF-05, DEF-07 | W05, W06 | M |
| 9 | [DEF-06](09-DEF-06-fenced-memory-write.md) | Fenced memory write | Phase 1: defect fix, Medium | DEF-06 | — | S–M |
| 10 | [DEF-08](10-DEF-08-workflow-save-validation.md) | Workflow save validation | Phase 1: defect fix, Low | DEF-08 | — | S |
| 11 | [W20a](11-W20a-event-stream-and-event-sequence.md) | Event stream and event sequence | Phase 1 | P2, part of P3 | W05 | S–M |
| 12 | [W08](12-W08-data-integrity.md) | Data integrity | Phase 2 | D1, D2, D5 | W07 | M |
| 13 | [W24](13-W24-write-intent-table.md) | Write-intent table | Phase 2 (alone, by rule) | D3 (markers) | W08 | M |
| 14 | [W09](14-W09-organizations-identity-and-tokens.md) | Organizations, identity and tokens | Phase 2 | S1 (part), S5, S7 | W08 | L |
| 15 | [W02](15-W02-split-the-two-large-functions.md) | Split the two large functions | Phase 2 (with W10) | Q2, Q3 | W01, DEF-01-02 (they touch the same lines) | M |
| 16 | [W10](16-W10-permissions-on-every-route.md) | Permissions on every route | Phase 2 | S1, S3 (application level) | W09 (PR 1), W02 | M–L |
| 17 | [W12](17-W12-actors-and-separation-of-duties.md) | Actors and separation of duties | Phase 2 | S2 | W10 | M |
| 18 | [W23](18-W23-scenarios-without-self-approval.md) | Scenarios without self-approval | Phase 2 | S2 (in the deployed gate) | W12 | S–M |
| 19 | [W13](19-W13-audit-log.md) | Audit log | Phase 2 | S4 | W10, W12 | M |
| 20 | [W11](20-W11-row-level-security.md) | Row-level security | Phase 2 | S3 | W08, W10 | M–L |
| 21 | [W14](21-W14-signed-retrieval-resolution-and-grants.md) | Signed retrieval resolution and grants | Phase 2 | S8, part of S6 | Stage A: nothing. Stage B: W10 | M |
| 22 | [W15](22-W15-per-organization-fair-scheduling.md) | Per-organization fair scheduling | Phase 2 | C7, S10 | W07, W09 | M |
| 23 | [W16](23-W16-secrets-rotation-and-credential-lifecycle.md) | Secrets rotation and credential lifecycle | Phase 2 | S6 | W10, W13 | M |
| 24 | [W17](24-W17-reconcile-path-and-idempotency-key.md) | Reconcile path and idempotency key | Phase 3 | O1 | W24 (release 2), W10, W13 | M |
| 25 | [W18](25-W18-retention-and-deletion.md) | Retention and deletion | Phase 3 | O2 | W08, W13 | M |
| 26 | [W19](26-W19-observability.md) | Observability | Phase 3 | O3, O4, part of P5 | W05 | M |
| 27 | [W20b](27-W20b-checkpoint-table-and-event-paging.md) | Checkpoint table and event paging | Phase 3 | rest of P3, D3 (checkpoints) | W24 (release 3), W05 | M–L |
| 28 | [W21](28-W21-held-out-evaluation-and-one-hosted-model.md) | Held-out evaluation and one hosted model | Phase 4 | E1, E2, E4 | nothing (labelling time) | M + labelling |
| 29 | [W22](29-W22-deploy-and-demo.md) | Deploy and demo | Phase 5 | X1, X2 | W09–W13, W19 | M |

Status: update `docs/gap-audit.md` §11 when a card is done.
