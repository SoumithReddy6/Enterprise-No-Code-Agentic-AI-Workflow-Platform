# TA01 fix: independent recheck

Reviewed commit: `51a3e944e80071d8a1e147cb78dd5d2c3ba84ae4`.

## Result

TA01 is closed for the reviewed scope. No new in-scope defect was found.
Provider-reported usage is captured before content validation, then accumulated
once on the shared caller's success or error path. The original provider error
still propagates; cancellation remains cancellation and does not await a spend
write inside the helper.

## Independently verified

- 178 targeted tests passed across providers, token budgets, Request reader,
  Request router, OpenAI, Claude, observability and S07 boundary modules. One
  upstream Starlette/AnyIO deprecation warning was emitted.
- Nine independent checks passed, using real adapters, compiler, Worker and
  temporary SQLite storage with synthetic HTTP responses:
  - Each provider (Ollama, Claude and OpenAI), both success and rejection: 150
    tokens in persisted accounting and SQL usage rows, exactly one measured call.
  - OpenAI 429 followed by a rejected 200 response: two HTTP attempts, one counted
    response, 150 tokens. A configured node retry did not retry the content error.
  - Cancellation during actual client cleanup after reported usage was parsed:
    150 tokens retained in memory, one counted call, zero persistence callbacks
    inside the helper, and CancelledError propagated.
  - Rejection without usage: no invented tokens and no persistence callback,
    preserving the explicitly deferred TA06 policy.
- The published production report has 24 passed rows, no source changes during
  its run, and every recorded source hash matches the current application.
- Its SHA256 matches the evidence README:
  `b690d5b67a528b3bfe910aa43a44d92361783dd0d97b0e0075fa8b46d3c6dc34`.
- The historical auditor probe/results and the implementer's revised result and
  production report remained unchanged during the independent probes.
- `git diff --check` passes.

The revised implementer probe checks a persistence callback rather than a SQL
transaction. The separate worker regression test and this independent recheck
establish the actual SQL durability claim.

## Scope and remaining work

This does not close TA02 accounting-outage/resume, TA03 nested cancellation
attribution, TA04 incomplete-run reporting, the soft-limit/unknown-usage policies,
Claude cache normalization, or the parallel accounting ordering risk. The
implementation correctly leaves them separate.

The full backend suite, five seeds, isolation experiment, mutation campaign,
deployed process campaign and hosted CI were not independently repeated here.
Their broader results remain the implementer's evidence. This recheck reran the
178-test focused suite and nine independent cases above.

Application source files were unchanged by the audit and matched the reviewed
commit. During the recheck, an unrelated edit appeared in backend/requirements.txt
(langgraph-sdk 0.4.4 to 0.4.6). It was left untouched, no dependencies were
installed by this audit, and that dependency change is outside this assessment.

Evidence: `evals/results/ta01-fix-review-51a3e94/`.
