# TA05 design review — 2026-11-05

**Application baseline:** `38f3509`.
**Draft:** `docs/superpowers/specs/2026-11-05-token-admission-limit-design.md`.
**Verdict:** the admission policy is appropriate for the selected option A. Two P2
lifecycle gaps need to be incorporated before implementation. They require small metadata
changes, not strict reservations or a different enforcement architecture.

## Published-system comparison

| Primary source checked | Fit and boundary |
|---|---|
| [Claude Managed Agents budgets](https://platform.claude.com/docs/en/managed-agents/budgets) | Checks consumed list cost before requests and does not interrupt a request because it crosses the budget. This supports the admission pattern. Its unit is dollars and its session outcome differs from Relay's typed workflow refusal; copy the policy principle, not those API/status details. |
| [Azure APIM token-limit policy](https://learn.microsoft.com/en-us/azure/api-management/llm-token-limit-policy) | With estimation disabled, response usage updates counters and subsequent requests can be refused. It documents overshoot with concurrent requests. This is useful precedent for response-based enforcement, not proof of a strict cap or identical per-run semantics. |
| [Bedrock quota accounting](https://docs.aws.amazon.com/bedrock/latest/userguide/quotas-token-burndown.html) | Reserves input plus output allowance and later settles quota usage. This is an architectural comparison for deferred option C. Its cache rules and output weights are quota-specific, not Relay's token or billing formula. |
| [LiteLLM budgets](https://docs.litellm.ai/docs/proxy/users) | Exceeded-budget errors expose spend and budget. This supports making overshoot visible; it does not establish Relay's one-call bound or transaction semantics. |

Keep the valid final answer complete when no work was refused. Recording the durable
known-token total atomically with terminal state is the correct local adaptation.

## TA05-D01 — P2: finish_run is not the only terminal writer

Rule 4.5 promises a record for every finished success, failed or cancelled run. Section
5.2 places the only hook in `Store.finish_run`. That misses:

- `Store.request_cancel` (`storage.py:753–770`): directly terminates queued and
  awaiting-approval runs.
- `approvals._close` (`approvals.py:104–110`): directly terminates rejected or expired
  approvals/reviews.

For example, models spend 80 + 30 tokens, then the workflow pauses before an approved
write. Rejection cancels the run with durable spend 110, but it never calls finish_run.
A worker that releases paid work back to the queue can likewise be cancelled without
that hook. L06 covers active-worker cancellation, which is a different path.

**Verified:** real Store/approval operations with a durable 110-token fixture show zero
finish_run calls for queued cancellation, paused cancellation, rejection and expiry. The
ordinary worker-finish control reaches it once.

**Required change:** use one shared metadata helper in all three terminal writers,
inside each writer's existing transaction. Read the authoritative accounting column via
the existing accounting accessor, then attach the record with the status change. Worker
finish retains its execution fence; administrative/review paths retain their existing
job-row lock and authorization. Do not call finish_run from inside another writer's
transaction or add a separate commit.

Add `approvals.py` to the change map. Add two focused cases: cancellation of queued paid
work, and rejection of an over-limit paused approval. They cover the two additional
terminal writers. This increases the collected-case cap from seven to **nine**.

## TA05-D02 — P2: an omitted new record does not remove an old one

Run data is merged at finish, and resume preserves fields it does not explicitly reset.
The proposed instruction to add a record only when the helper returns one can retain
stale information:

1. A run fails at 110 with limit 100 and records `over: 10`.
2. The operator raises the limit to 200 and resumes it.
3. It finishes at 120. No new overshoot record is produced, but the old record remains.

The response would say limit 100/spent 110/over 10 even though the draft explicitly says
to use the finish-time limit and omit the record when spend is under it. The same stale
record can survive an unlimited or invalid setting.

**Verified:** seed the prospective prior record through the existing finish updates API,
resume, save durable spend 120, then finish without a new record. The original record
survives under limits `200`, `0` and `lots`. This demonstrates the existing retention
mechanism; it does not claim current application code already generates TA05 metadata.

**Required change:** every terminal metadata application first removes any old
`token_limit`, then attaches a newly computed record only when valid and over limit.
Clear it on resume as well if it is not intended to describe a prior attempt while the
run is active. Use this rule on all writers from D01, including skip paths for invalid
settings or unreadable accounting.

Extend L02 through resume under a raised limit and assert the old record disappears;
no new collected case is needed. Add targeted mutants for the two missed terminal
hooks and stale-record retention. Together with the original six, a cap of **nine
mutants** remains proportionate.

## Wording to tighten

- Replace "an admitted call always completes" with "the admission limit does not abort
  an in-flight call; cancellation, timeouts and provider errors still apply."
- State the one-call overshoot bound for sequential execution, an unchanged limit and
  the known provider-reported subtotal. It does not bound real consumption hidden by
  missing reports or retries. One logical Relay call can include multiple HTTP attempts.
- A lower limit applied at finalization can make past spend exceed it by more than one
  call; the record then reports comparison with current configuration, not a violation
  of an earlier admission decision.
- Scope "never blocks finalization" to metadata validation/configuration errors. Existing
  lease fencing and database failures must retain their normal behavior.

These are clarifications of the chosen policy; they do not request new enforcement.

## Verification and stop rule

Independent baseline checks: **106 passed** across token budget, token recovery,
accounting durability and approvals. Five real storage terminal-path probes and three
resume/retention fixtures all confirm the mechanisms above. No paid model request was
made, and application/design files were left unchanged.

Evidence: `evals/results/ta05-design-review-38f3509/`.

Once D01, D02 and the wording are incorporated, proceed tests-first under P2. No separate
implementation plan or further full design round is required. Keep the existing full
suite, isolation, deployed and hosted gates; keep the fix limited to metadata and naming.
