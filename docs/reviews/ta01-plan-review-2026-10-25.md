# TA01 implementation-plan review

Reviewed commit: `21d6036`.
Plan: `docs/superpowers/plans/2026-10-25-token-usage-on-rejected-responses.md`.

## Assessment

The core implementation approach is appropriate for TA01: extract usage before
content validation in all three adapters, and account once on both successful
and exceptional returns through one shared caller. Keep the remaining token
findings separate. Correct the following test and documentation details before
implementation; no broader runtime redesign is required for this step.

## R01 [P2] — The Request reader test assumes a repair path that does not exist

Plan line 135 expects provider rejection followed by a valid repair. The first
llm_node call in request_reader.read_request is outside its JSON-validation try
block. ProviderResponseError escapes; only returned text that fails
validate_reading triggers repair. request_router has the same distinction.

A synthetic HTTP response with empty Ollama text followed by a queued valid
reading produced one provider request, ProviderResponseError, and no repair.
Changing only the TA01 usage boundaries cannot make the proposed two-call test
pass. Adding such repair behavior would be an unrelated policy change.

Replace it with two controls:

1. Provider-level rejection records its reported usage once, then escapes without
   an automatic reader repair.
2. Nonempty but invalid JSON text triggers the existing one repair, with both
   responses counted exactly once. This control should already work on baseline.

## R02 [P2] — Do not describe a timeout as an unbilled rejected request

Plan lines 46–48 group HTTP errors with timeouts and say the provider does not
bill a rejected request. A timeout gives no such evidence. The provider may
continue processing after the client stops waiting.

OpenAI's official spending-controller example explicitly treats interruption as
potentially charged and keeps an unresolved reservation:
https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api

Use: "No reported usage is added in this step when no usage was returned. A
timeout's consumption remains unknown; handling it is TA06." This is a wording
correction, not a request to implement TA06 or strict reservations here.

## R03 [P2] — The historical auditor probe intentionally asserts the broken result

Plan lines 141–143 reference rejected_paid_response in the historical probe.
That function asserts tokens_spent == 0 at line 102 and returns a hard-coded
accounted_tokens: 0 at line 103. Running it unchanged after a correct fix will
raise AssertionError, not report a passing 150-token result.

Use a separate revised copy in a new evidence directory, or a new permanent
regression test. Assert the measured count is 150 and emit that measured value.
Preserve the original evidence without running writers against it.

## R04 [P3] — Make test/mutation acceptance internally consistent

- Lines 124 and 163 conflict: a control explicitly expected to pass on baseline
  cannot also be required to fail there. Require defect reproductions to fail
  before the fix and pass afterward; unchanged-behavior controls pass both.
- M4 says to count in both the exception and success paths. The exception path
  re-raises, so the success path is unreachable for that call. Instead inject a
  second accounting call inside the reachable error handler and assert both
  150 tokens and calls == 1. Give the successful path the same exact-once control.
- Add the equivalent Claude adapter-order mutant, since all three independently
  written adapters need their own boundary covered.

## Optional simplification

Catch asyncio.CancelledError explicitly rather than calling every BaseException
"cancelled". Keep cancellation non-retryable and promptly propagated; this need
not expand TA01 into the deferred cancellation-attribution or durability fixes.

## Verification and limits

The plan, provider code, registry usage accounting, reader/router repair paths,
event usage attribution and historical probe were read directly. The reader
boundary was exercised with the real adapter and reader, using synthetic HTTP:

```sh
.venv/bin/python evals/results/ta01-plan-review-2026-10-25/probe_reader_boundary.py
```

No application code or plan was edited. The proposed helper does not exist yet;
this review makes no implementation-passes claim. Full suites and hosted CI
were not run for this plan-only review.
