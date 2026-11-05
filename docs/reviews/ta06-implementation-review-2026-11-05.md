# TA06 implementation review — 2026-11-05

**Commit:** `0b7e8c7e09fd660a2d8cad106a48e2e2097910ee`.
**Design:** `docs/superpowers/specs/2026-11-05-unreported-usage-design.md`.
**Evidence:** `evals/results/ta06-implementation-review-0b7e8c7/`.
**Verdict:** two small P2 corrections remain before TA06 closes. The accounting and
attribution changes work in the checked response, retry, cancellation and resume paths.

## TA06-I01 — P2: the journal silently discards the new status

At `backend/app/registry.py:189`, `account_usage` supplies `usage_status` to
`journal`. However, `backend/app/observability.py:18–25` filters every field through
`FIELDS`, which does not contain `usage_status`. The emitted JSON never includes it.

A real Worker run returning an answer with no counts correctly saves one unreported
call in its event and SQL metrics. Its actual `model.usage` journal line contains neither
token counts nor a status. Operators cannot distinguish unreported from unanswered
through the promised status field. This contradicts design §5.2 and the implementation
README's journal claim.

**Reproduction:** `test_real_journal_retains_managed_status[current]` captures the actual
filtered JSON logger output and fails. The companion `allow-status-control` passes when
only the journal allowlist is extended in memory. No application file is edited.

**Smallest correction:** permit `usage_status` through the content-free journal filter.
Add an assertion to an existing TA06 case that inspects captured JSON log output; replacing
`journal` with a test spy would miss this filter. Keep the status restricted to the four
internally generated values. No raw request, answer or exception message is needed.

## TA06-I02 — P2: a locally rejected URL creates a phantom unanswered call

`backend/app/providers.py:131–143` excludes connection failures from delivered attempts,
but not `httpx.UnsupportedProtocol`. With `OLLAMA_BASE_URL=localhost:11434` (missing
`http://`) or `ftp://127.0.0.1:9`, request construction succeeds, `_send` increments
`dispatched`, and the real HTTPX transport then rejects the protocol before connecting.
Relay records `unanswered_calls: 1`, despite no network connection or provider request.

Both reproductions use the real HTTP client, with the network backend instrumented to
fail if any connection is attempted. No connection is attempted. The workflow fails as
expected, but its usage outcome and SQL metric incorrectly contain an unanswered call.
This is a counting error, not a token-charge increase or a successful business action.

The [HTTPX exception documentation](https://www.python-httpx.org/exceptions/)
identifies `UnsupportedProtocol` separately, including unsupported `ftp://` requests.
Inspection of the installed HTTPX 0.28.1 / httpcore 1.0.9 confirms the scheme check happens
before connection-pool work. The source and behavioral probe agree.

**Reproduction:** the two
`test_unsupported_protocol_cannot_reach_a_provider[current-…]` rows fail. Both
`exclude-unsupported-control` rows pass when only `UnsupportedProtocol` is added to the
known-undelivered classification in memory.

**Smallest correction:** classify `UnsupportedProtocol` as known not delivered, or
validate the supported URL scheme before dispatch. Add the missing-scheme case to the
existing local-URL check. Retain the distinction from read/write timeouts: those remain
unknown and must still count as unanswered. Do not exclude all HTTP errors.

## Verified behavior and accepted deviations

- The original missing-count scenario counts three responses as three unreported calls.
- Partial counts preserve known tokens; boolean counts do not count as integers, while
  a reported zero remains valid. Independently checked across all three providers.
- Mixed retry sequences produce one logical status. In particular, read timeout followed
  by connection failure remains unanswered; an HTTP error response followed by connection
  failure is unreported; exhausted pool timeouts count nothing.
- A parent, child and grandchild with unreported, partial and unanswered calls attribute
  each status exactly once to its own invocation during cancellation.
- Unknown usage with zero known tokens still engages TA02's pending-save barrier before
  a structured repair. No second provider request is sent during the accounting outage.
- Cancellation during acknowledgement of a committed LLM checkpoint, then resume, reuses
  that checkpoint without repeating the request or recounting its unreported call.
- The frontend wording distinguishes unreported and unanswered usage from reported zero.
- Omitting zero status counters from metrics API rows is consistent with design §4.3;
  it preserves the old response shape for complete usage. No existing test was modified.
- Extra persistence through `record_spend` follows the approved durability rule. An
  answer-returning stub with no count is correctly treated as unreported.
- The builder's V07 fixture correction and isolation-copy procedure do not weaken the
  behavioral oracle. The recorded isolation hashes and five-seed results were inspected.
- The journal deviation is only partly implemented: omitting absent token fields works;
  adding the status does not, as TA06-I01 shows.

## Verification boundary

The independent edge probe contains 18 collected rows: 12 behavioral controls pass on the
unchanged application, three defect reproductions fail, and the three narrowly patched
controls pass. The patches use pytest monkeypatch and leave application source unchanged.

Focused backend tests and the frontend unit suite were rerun independently. Hosted CI,
exact counts, original-file preservation and the builder-evidence checks are recorded in
the evidence README and JSON files. Five local suite seeds, the eight builder mutants and
the deployed campaign are inspected builder evidence, rather than repeated audit runs.

Independent regression checks: **210 backend tests and 115 frontend tests passed**.
[Hosted run 42110749689](https://github.com/SoumithReddy6/Enterprise-No-Code-Agentic-AI-Workflow-Platform/actions/runs/42110749689)
passed all five jobs on the exact reviewed commit. Backend ran twice, each with 2,000
passed and two platform skips; browser-layout passed 49 tests; PostgreSQL conformance
included migration round trips/model parity at `0006_usage_status`.

The token ceiling remains a soft known-token limit, and provider retries remain one
logical call, as approved. No historical cleanup or broader accounting design is required
for these two corrections.
