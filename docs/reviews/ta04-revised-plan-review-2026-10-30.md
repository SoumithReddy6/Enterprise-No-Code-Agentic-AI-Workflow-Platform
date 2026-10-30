# TA04 revised-plan audit — 2026-10-30

Baseline: `51ba164`. Reviewed the revised plan with SHA256
`ce5b1e39ca227727daf6d62a7ebae814a292acbe8cd373e14d2a131ea6c36824`.
This is a plan audit. No application files, original plan, prior report or prior evidence were changed.

## Recommendation

Keep typed token refusal and D1=B, but settle the three findings below before implementation.
The first two are material fallback-boundary omissions. The third is a smaller inconsistency
in the explicitly planned compaction case. The earlier findings are addressed in the main
paths; this audit does not reopen the need for durable loop halt or Agent repair fallback.

The central rule should be: **a budget stop changes the outcome, but does not bypass the
normal source-validity and output-contract checks.** The halt decision and its cause must
also survive replay as data, independently of shortened display text.

## Production guidance checked

These are official public contracts, not claims about private company implementations.

| Source | What it establishes | Comparison with this plan |
|---|---|---|
| [AWS Step Functions error handling](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html) | Named errors allow specific retry/catch policies; fallback error output carries the cause. | Typed `TokenBudgetExhausted`, a non-retry policy and an explicit incomplete cause are appropriate. A generic `ValueError` cannot express this distinction reliably. |
| [OpenAI Agents SDK Python](https://openai.github.io/openai-agents-python/running_agents/#errors-and-recovery) and [JS](https://openai.github.io/openai-agents-js/guides/running-agents/#errors-and-recovery) | Controlled limit/error fallbacks are supported. The structured invalid-output fallback is validated; JS explicitly requires returned fallback output to match the current agent's output type. | B is a legitimate policy, but a prose fallback cannot silently become a successful strict JSON business result. Neither reference requires a further paid call to synthesize a fallback. |
| [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers#durability-modes) | Replay re-executes work after a checkpoint; synchronous durability persists checkpoints before continuing. | Relay owns its own persistence, so the loop's stop decision must be committed with progress before replay can safely skip/restart work. Merely keeping a Python local variable is insufficient. |
| [Azure API Management token limits](https://learn.microsoft.com/en-us/azure/api-management/llm-token-limit-policy#considerations-for-token-counts-and-estimation) | Response-based enforcement can overshoot, including with concurrent requests, and blocks subsequent requests once exhaustion is observed. | The revised TA05 caveat is accurate. B stops additional calls after known exhaustion; TA04 is not strict admission or a billing ledger. |

## R01 — P1: budget fallback must retain the existing source-validity gate

Plan lines 159–179 add two early returns of collected sources: known exhaustion at a step,
and a caught refusal during finalization. The normal grounded Agent return goes through
`agent_runtime.py:423`, which calls `verify_vector_sources` after generation. The new early
returns are not specified to go through that gate.

This gate is substantive: `KnowledgeServices.verify` checks source contents and then
authoritatively checks document availability after index IO. A document can be removed
while a model call or another awaited operation is running. Marking a source uncited does
not make its contents safe to return without that check.

I made a disposable git-archive export and implemented only the plan's Agent early-return
paths, typed entry refusal and repair re-raises. Synthetic HTTP reports 100 tokens. A fake
platform starts with a valid source, then removes it at a controlled boundary:

| Case | Result with the partial-plan candidate |
|---|---|
| Valid final answer; source removed during call | Fails at the existing source check |
| Repair refused by tokens; source removed during call | Succeeds and returns the removed source |
| Known token exhaustion at step; source removed before fallback | Succeeds and returns the removed source |

**Required plan change:** funnel budget outputs through the same source verification as
normal grounded outputs, or perform that exact verification before returning the helper's
sources. Do not catch its permission/deletion failure as token exhaustion. Preserve
cancellation and lease loss as control flow.

**Tests:** removal at both return sites, plus an unchanged-source control; assert that no
removed source is published in a success output. Include one nested-specialist case. No
additional provider call should be needed for any of these checks.

In the disposable candidate, adding verification before both B returns makes both removal
cases fail, keeps the valid-source B case successful, and still makes exactly one provider
call in each of the six Agent cases.

## R02 — P2: structured fallback needs a defined contract, and T9 misses its handler

The helper at plan lines 171–177 returns prose. The finalization catch at lines 166–169
also covers Agents configured with `output_schema`, extraction roles and classification
roles. For example, a node promised to return `{"amount": <number>}` can instead succeed
with “The run token budget ran out…”. Run-level truncation is useful, but it does not make
that text match the node's business schema.

The partial-plan candidate reproduces this with a strict amount schema and no evidence:
one paid invalid response, refusal of repair, then `status=success` with schema-invalid text.
This contradicts the fallback-validation pattern documented by the OpenAI Agents SDK.

There is also a precise test gap. T9 says it covers the structured repair wrapper but
collects evidence. In current code, evidence selects `if grounded` at line 421, so it never
enters `elif structured` at line 424. Both T9 and T9b exercise grounded finalization. M4 can
therefore survive unless another test independently reaches the structured-only wrapper.

I ran both shapes through real `llm_node` and synthetic HTTP:

| Agent input | Grounded-finalizer calls | Wrapper reached |
|---|---:|---|
| Structured config with evidence | 1 | Grounded wrapper |
| Structured config without evidence | 0 | Structured-only wrapper |

**Required plan change:** state the structured-output policy explicitly. Recommended
minimal rule: retain B for free-text Agents; for strict structured output, use a validated
schema-compatible abstention only if the declared contract supports one. Otherwise retain
the typed token failure. Never fabricate required business fields or silently weaken the
schema. If B must apply to every arbitrary schema, a separate typed non-business outcome
is needed; a prose string in the business output is not that outcome.

This is a narrowing of B for strict structured Agents and should be recorded explicitly,
not introduced unnoticed during implementation. Evidence already collected should remain
available in authorized run/source data; it must not be fabricated into a business result.

**Tests:** add the no-evidence strict-schema path; retain a grounded structured case; cover
extraction/classification's JSON-object requirement. Assert either a schema-valid fallback
or the typed failure, zero extra provider calls, and no ordinary business output on failure.
M4 must fail because the structured wrapper itself lost the type.

The guarded disposable candidate demonstrates the minimal typed-failure option. It refuses
the incompatible fallback without making another provider call; it is not a final product
decision or a complete TA04 implementation.

## R03 — P3: compacted halt reason is different fresh versus resumed

Plan lines 201–204 restore the halt reason from `entry.error`, falling back to a fixed
message after compaction. Fresh iteration still uses the separate unshortened `exhausted`
string returned by `_run_item`. Consequently the explicitly planned compacted-entry path
does not restore the same summary/reason:

- fresh: `Run token budget exhausted (100 of 100 ... RELAY_RUN_TOKEN_LIMIT) ...`;
- replay: `A run budget was exhausted. ...`.

The stop flag survives, but its cause does not match. T7b does not compact an entry; T7c
checks only that replay uses the fixed reason, so neither compares both paths together.

**Required plan change:** derive fresh and restored halt state through one interpretation
of the admitted/persisted record. Prefer bounded typed halt metadata (kind plus a bounded
reason/code), retained independently of lossy display error text. Alternatively, use the
same deliberate fixed message in both paths. Update the compact-size reserve accordingly.

**Test:** force a marked entry through compaction, interrupt after its actual progress
commit, resume and compare the complete summary/reason as well as calls/actions. Test both
item-error policies. This is a conditional plan-level counterexample, not evidence that a
normal 100-token refusal currently reaches compaction at production size limits.

## What checked out

- The original two review findings are now represented in the plan, including compaction
  retention, durable worker replay and final-answer repair refusal.
- Current SQLite storage preserves an added `budget_halt` entry key after a database reopen.
- A stale worker cannot save the entry. An injected pre-commit failure leaves neither the
  item record nor its loop event, supporting the plan's same-transaction choice.
- A valid final answer that consumes the last tokens still succeeds as complete in the
  disposable candidate. Budget exhaustion must not discard an already-valid paid answer.
- B with valid sources can succeed without any further model call.
- The focused existing suite passes: **83 tests** (token budgets, recovered budgets, run
  causes, Agent grounding and result contracts). This is baseline coverage, not verification
  of a completed TA04 fix.

## Finite closure checklist

Use the plan's tests plus these combinations; avoid relying on seed count as a substitute:

1. **One typed terminal cause:** fail/continue/route, ordinary provider failures remain
   ordinary, no retry on run-budget refusal, and control-flow exceptions still escape.
2. **All model-call sites:** plain LLM, Query generation and repair, Request reader/router
   repair, free-text Agent, grounded repair, structured-only repair and specialist. Include
   a valid final answer exactly at the limit and the disabled-limit control.
3. **B preserves guards/contracts:** source validity at both fallback sites; explicit strict
   schema policy; no fabricated business fields; no extra provider call; deliberate memory
   behavior for a synthetic fallback.
4. **Durable replay equivalence:** interruption after each relevant commit, including a
   compacted halt and a successful B checkpoint. Compare complete outputs, reasons, action
   spend, retrievals and provider calls. A failed persistence/fence operation must not be
   mistaken for committed progress.
5. **Fenced atomic storage:** row/event/halt commit or roll back together. Test the durable
   worker path. Existing marker storage is suitable; no new schema is needed for this key.
6. **Regression and release evidence:** planned mutants, focused tests, full backend seeds,
   isolated commit tree, deployed scenarios and hosted CI on the final commit.

Closing this checklist closes **TA04**. It does not close the distinct TA02 durability
failure during accounting outages, TA03 cancellation metric attribution, TA05 strict
admission policy, TA06 unknown/missing usage, TA07 conditional cache normalization or the
future parallel snapshot issue. Those need their own explicit acceptance criteria; this
plan should not claim that all token accounting is finished.

## Evidence and limits

New artifacts: `evals/results/ta04-revised-plan-review-2026-10-30/`.
The candidate is a disposable, partial implementation of the plan's Agent paths, not code
proposed for merging. Provider and resource operations are synthetic. The storage probe
uses a temporary SQLite database. No real user run, knowledge base or external write was
created. PostgreSQL, full backend, frontend, production campaign and hosted CI were not run
for this plan review. Original input hashes are in `preserved-inputs.json`.
