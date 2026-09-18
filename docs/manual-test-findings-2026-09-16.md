# Manual test findings — Employee Handbook workflow

Date: 2026-09-16

Workflow: Chat Input → Retrieve → Agent → Response

Knowledge base: all documents in `evals/corpus`

Status: open findings from manual testing

Automated follow-up: [Test01 retrieval and grounded-generation report](test01-evaluation-report-2026-09-17.md)

## Test configuration and limits

The tester supplied the generated answers but not the run IDs, model configuration, retrieval options, or saved run events. Each issue below is reproducible from the recorded question and corpus evidence, but the relevant run ID should be added when the case is rerun. No finding below assumes that retrieval failed merely because generation failed: several answers quote the correct passage and then reason incorrectly.

Priority meanings:

- **High**: can produce a materially wrong or fabricated policy answer while appearing grounded.
- **Medium**: creates ambiguous resources, incorrect abstention, or misleading citation behavior.
- **Low**: presentation or canonical-format defect with limited effect on the core answer.

## Summary

| ID | Priority | Finding | Layer | Status |
| --- | --- | --- | --- | --- |
| MT-001 | Medium | Duplicate normalized knowledge-base names are accepted | Management/API | Open |
| MT-002 | Medium | Existing citations are provenance-checked but need not support the associated claim | Grounding/runtime | Open |
| MT-003 | Low | Combined citation syntax such as `[S1, S2]` is not canonicalized | Grounding/runtime | Open |
| MT-004 | Medium | The Agent abstains even when the retrieved passage answers “front tyres” via “steer axles” | Generation/evaluation | Open |
| MT-005 | High | Missing facts trigger speculative or fabricated answers instead of strict abstention | Generation/grounding | Open |
| MT-006 | High | The Agent does not conclude that 45 days is later than a 30-day deadline | Generation/evaluation | Open |
| MT-007 | High | A 6% customer-facing outage is classified as SEV2 despite the explicit >5% SEV1 rule | Generation/evaluation | Open |

## MT-001 — Duplicate normalized knowledge-base names

**Observed behavior:** A second knowledge base named `first` was created while another knowledge base with that name already existed.

**Expected behavior:** Knowledge-base names are unique inside a workspace after trimming whitespace and applying case-insensitive normalization. `first`, `First`, ` first`, `first `, and `FIRST` should conflict.

**Impact:** The Knowledge selector and Retrieve-node configuration become ambiguous even though internal IDs remain distinct.

**Required fix:**

1. Store a normalized name key owned by the workspace/tenant.
2. Enforce uniqueness in the database, rather than only performing a pre-insert query.
3. Apply the same rule to creation, rename, and legacy import.
4. Return a clear conflict response and preserve the user-entered form values.

**Regression tests:** Create/rename conflicts, whitespace and case variants, different tenants using the same name, and two concurrent creation requests where exactly one succeeds.

## MT-002 — Citation provenance does not prove support

**Question:** “What is the API rate limit per customer account?”

**Observed behavior:** The core answer correctly stated `600 requests per minute` and cited `[S1]`, but it also narrated that `[S2]`, `[S3]`, and `[S4]` did not answer the question. Existing labels are accepted even when the passages are irrelevant to the factual claim.

**Expected behavior:** The concise answer is: “Each customer account is limited to 600 API requests per minute [S1].” Only passages that support a claim should be presented as its citations.

**Corpus evidence:** `evals/corpus/api-integration-guide.md` states that each customer account is limited to 600 requests per minute.

**Impact:** A response can look thoroughly sourced while its citations merely exist in the run and do not support the associated claims.

**Required fix:** Add a claim-to-evidence verification stage that distinguishes valid provenance from actual support. Initially report this as a quality signal; promote it to a blocking rule only after calibration against human-reviewed cases.

**Regression tests:** A supported claim, an irrelevant existing citation, a partially supported compound claim, a contradicted quantity, and an uncited factual claim.

## MT-003 — Combined citation syntax is not canonicalized

**Question:** “Which tool is used to undo a bad production deployment?”

**Observed behavior:** The answer correctly named Rewind but used `[S1, S2]`. The current citation convention and parser recognize individual labels such as `[S1], [S2]`.

**Expected behavior:** The runtime accepts one canonical representation, normalizes harmless variants, and renders citations consistently.

**Impact:** Supporting sources may not be marked as cited even though the user can see citation-like text.

**Required fix:** Parse and normalize a bounded grammar for comma-separated labels, reject malformed or unknown labels, and render the canonical format.

**Regression tests:** `[S1]`, `[S1], [S2]`, `[S1, S2]`, duplicates, invalid labels, mixed valid/invalid labels, and labels embedded in unrelated brackets.

## MT-004 — “Front tyres” is not mapped to the steer-axle rule

**Question:** “When are the tyres on the front axle of a truck considered worn out?”

**Observed behavior:** The answer said the evidence was insufficient even though it repeated the relevant distinction between steer axles and drive/trailer axles.

**Expected behavior:** Front/steer-axle tyres are replaced when tread depth falls below 4 mm.

**Corpus evidence:** `evals/corpus/fleet-maintenance.md` states that tyres are replaced below 4 mm on steer axles and below 3 mm on drive and trailer axles.

**Classification:** Generation/reasoning failure, not a retrieval miss, because the answer demonstrates that the relevant passage was available.

**Regression test:** Add the exact question and variants using “front,” “steering,” and “steer” to the grounded-generation evaluation. Require the 4 mm answer and a supporting citation.

## MT-005 — Missing facts cause speculation or hallucination

**Questions tested:** Parental leave, chief executive, employee shipment discount, payroll bank, and customer-meeting dress code.

**Observed behavior:**

- Parental leave was speculated from vacation and sick-leave policies.
- The COO was discussed as a possible high-ranking substitute for an unknown chief executive.
- The payroll response claimed “The bank processes company payroll [S1]” even though no bank was identified.
- Customer-care staffing hours were included in the dress-code response.
- Only the employee-discount answer stopped cleanly after declaring insufficient evidence.

**Expected behavior:** State that the requested fact is not present, then stop. Do not infer an answer from adjacent policies, cite irrelevant passages, or turn a generic relationship into a named fact.

**Impact:** High. The response can fabricate organizational, financial, or employment-policy information while appearing evidence-based.

**Required fix:**

1. Use a grounded-answer contract with explicit `answer`, `citations`, `abstain`, and `reason` fields internally.
2. Require every material factual claim to have supporting evidence.
3. Reject or repair answers that contain factual claims while `abstain` is true.
4. Run calibrated claim-evidence evaluation on unanswerable questions.

**Regression tests:** All five questions above, plus unrelated retrieved passages and prompts that explicitly request guessing.

## MT-006 — Deadline comparison is not applied

**Question:** “I submitted an expense after 45 days. Does the policy say it is on time?”

**Observed behavior:** The answer repeated the 30-day deadline and director-approval rule, then said the evidence was insufficient to determine whether 45 days was on time.

**Expected behavior:** No. Forty-five days exceeds the 30-day deadline, so the report is late. Written director approval may allow processing but does not make it on time.

**Classification:** Generation/reasoning failure with sufficient evidence.

**Regression tests:** Values below, equal to, and above the deadline; verify that exception/approval rules do not change the definition of “on time.”

## MT-007 — Explicit severity threshold is overridden

**Question:** “A customer-facing outage affects 6% of customers. What severity is it, and when must the postmortem be published?”

**Observed behavior:** The Agent classified it as SEV2 and gave a five-business-day postmortem deadline.

**Expected behavior:** SEV1 because 6% is greater than the explicit 5% threshold. The postmortem is due within five business days of resolution.

**Corpus evidence:** `evals/corpus/incident-severity.md` defines SEV1 as a customer-facing outage affecting more than 5% of active shipments; every SEV1 and SEV2 requires a postmortem within five business days.

**Impact:** High. A plausible citation accompanies a materially incorrect operational classification.

**Regression tests:** 4.9%, 5%, 5.1%, and 6%; separately verify the strict “more than” boundary and the postmortem rule.

## Improvement plan

### Phase 1 — Deterministic integrity rules

1. Enforce tenant-scoped normalized KB-name uniqueness at the database boundary.
2. Canonicalize citation syntax and expose malformed citation errors.
3. Add all manual findings to automated regression data before changing prompts or models.

These fixes are deterministic and should behave identically across Ollama, OpenAI, and Claude.

### Phase 2 — Grounded-answer contract

Change grounded generation to produce a validated internal structure containing the answer, cited labels, an abstention flag, and an abstention reason. Render normal prose only after validation. Enforce these invariants:

- cited labels exist in the authorized run evidence;
- non-abstaining factual answers contain citations;
- abstaining answers do not add unsupported factual speculation;
- unsupported or contradicted claims are surfaced as a failed quality check.

### Phase 3 — Claim-to-evidence verification

Use the existing optional local claim evaluator on a human-reviewed calibration set. Measure false acceptance and false rejection before allowing it to block live responses. Once calibrated, use it for a single bounded repair attempt; if support still fails, return a safe abstention with the failed claims visible in the execution log.

### Phase 4 — Reasoning regression suite

Add deterministic evaluation cases for thresholds, comparisons, negation, exceptions, and terminology mappings. The initial suite should include front/steer axle, 45 versus 30 days, and 6% versus a >5% threshold. Run the suite for each permitted production model and publish model-specific pass rates rather than assuming all enabled models behave equally.

### Phase 5 — Operator visibility

In the run view, distinguish:

- retrieved passages;
- passages cited by the answer;
- citations verified as supporting the associated claim;
- unsupported, contradicted, or uncited claims;
- clean abstentions.

This makes grounding failures inspectable instead of hiding them behind valid-looking source labels.

## Acceptance target

The first reference workflow is ready for broader production testing when:

1. All seven regression groups pass.
2. Duplicate KB creation is safe under concurrency.
3. Every material factual answer has at least one supporting citation.
4. Every unanswerable test returns a clean abstention without speculation.
5. Boundary and comparison tests pass for the selected model.
6. Model-specific evaluation results and known limitations are visible before a model is enabled for users.
