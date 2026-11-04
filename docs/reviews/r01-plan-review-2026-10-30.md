# R01 Response boundary plan review — 2026-10-30

Reviewed plan commit: `65a7774`. Application baseline remains `812fefd`.
No application, original plan or original evidence was changed by this review.

**Decision D1: choose A, fail closed.** Reusing the authoritative verifier is appropriate.
The plan needs the two fail-closed corrections below and an explicit recovery policy before
implementation. The main security gap is the untested interval before a Response checkpoint
exists; validating saved Response outputs alone does not close the loop restore path.

## Published guidance checked

[Saltzer and Schroeder](https://web.mit.edu/Saltzer/www/publications/protection/Basic.html)
describe complete mediation as covering recovery as well as ordinary operation, and warn
that remembered authorization results need updating when authority changes. This directly
supports checking reused evidence dependencies rather than treating a restored answer as
currently authorized merely because its source list was not restored.

[OWASP LLM08](https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/)
supports permission-aware retrieval and source integrity. It does not establish that checking
only presentation metadata is sufficient for already-cached answer text.

[CWE-367](https://cwe.mitre.org/data/definitions/367.html) supports acknowledging the remaining
check/use race. The loop replay failure below is **not** that residual race: the document
is removed while the worker is stopped, and no source verification occurs on resume.

## R01-P1: restored loop text can be published with an empty source list

Plan section 5c, lines 88–91, checks a saved Response's `sources`. U4 crashes after the
Response checkpoint exists. Neither covers a crash after the loop checkpoint but before
Response execution.

I built a disposable archive export with exactly the proposed three-file behavior: Response
verification, exact-copy deduplication/id-safe batches, and saved Response verification in
`kb_errors`. Then I used a real temporary KB, document ingestion/removal, real Worker and
SQLite. Only the provider and Jira HTTP responses were substituted.

Workflow:

```text
Input → Jira list → For each (Query body) → Prompt formats each.results → Response
```

1. Query retrieves a valid passage and produces `Bluebird is the project codename. [S1]`.
2. The loop checkpoint commits. Stop before Prompt/Response execute.
3. Remove the source document. An unrelated document keeps the KB resolvable/searchable.
4. Resume. The loop restores its answer text, but its output contains no `sources` port.
5. Shared `run.evidence` is empty in the new attempt. Response lists no sources, so its new
   verification helper makes no call. It publishes the removed document's answer text.

Observed on the partial-plan candidate:

```text
status: success
output contains removed answer: true
Response source count: 0
verification calls after resume: 0
provider calls: 1 total, none repeated on resume
```

The saved dependency is still present under `each:0`. Existing
`platform_validation.py:30` skips it because that owner is not a checkpoint key. The added
Response-check branch has no saved Response to inspect. `evidence_from_outputs` does not
reconstruct knowledge sources from loop result strings. Removing an unsupported citation
does not remove the protected answer text.

**Required plan change:** validate the recorded dependencies of reused loop checkpoints
and saved item progress even when no Response checkpoint exists. Use existing dependency
records, not citation parsing or a second model. A conservative policy can verify saved
loop-item dependencies on resume; if choosing a narrower consumed-item policy, state its
mapping explicitly. Checking only a future Response's presentation source list is insufficient.

**Test:** add a real-KB crash immediately after the loop checkpoint, before Response, then
remove the document and resume. Require refusal before the cached answer is newly published,
with no extra model call. Include reuse from partial item progress too.

The existing U4 scenario remains useful: the prototype correctly refuses resume when the
Response checkpoint itself already exists. It simply covers a different commit boundary.

## R02-P2: make missing authority and malformed source containers fail closed

Section 5a, line 73 explicitly skips verification when `ctx.platform is None`. That permits
non-empty sources to be published without any authority check. Current production Workers
provide a resolver, so this is a construction/API-contract gap, not a demonstrated HTTP
bypass. The new helper should not encode an exception to its own security rule.

The restore path also needs explicit container validation before batching. The plan does
not state that requirement. A straightforward implementation accepts a cached string
`sources: "{}"`: JSON decodes to an empty dictionary, iteration produces zero batches, and
restore reports no error or verifier calls. Invalid metadata has become empty evidence.

The disposable candidate confirms both observations:

```text
nonempty sources + no platform verifier → source published
cached sources="{}" → no restore error, no verifier call
```

**Required plan change:** source metadata must be a JSON array of source objects. Reject
missing/malformed containers and entries before empty/batch shortcuts. Only a valid empty
list needs no verifier. Non-empty sources with no configured verifier must fail with a clear
unavailable-verifier error. Keep the explicit `CONTROL_FLOW` re-raise.

**Tests:** non-empty sources without a resolver; empty list without a resolver as a control;
cached `{}`, `null`, a string and non-object entries. Malformed data must never be silently
treated as zero listed sources.

## R03-P2: the proposed recovery behavior does not match the worker

Section 5b, lines 81–84 says `continue`/`route` recover with no outputs. For the valid
`continue` configuration, the prototype does recover the Response node with `{}`, but
`worker.py:204` then unconditionally reads `result['values'][response_id]['text']`.

Observed with removal after an Agent's successful source check:

| Response policy | Run result |
|---|---|
| `fail` | Fails with the planned, specific source-unavailable message |
| `continue` | Response records the specific recovered failure, then Worker raises `KeyError('text')`; run reports `Run failed unexpectedly.` |

Both prevent publication, but the second is not the promised normal recovery path.
Explicit recovery edges are incompatible with the current terminal-Response rule.
The validator currently even accepts `on_error: route` with no Response edges, so the
plan should not assume a working Response error route exists.

**Required plan change:** choose the supported behavior. If `continue` remains supported,
the final-output assembler must handle recovered Responses with no text. Otherwise enforce
an explicit fail-only Response policy at validation. Remove the unsupported route claim
or design/test it explicitly. Do not silently turn this into an unexpected worker failure.

**Tests:** default fail and the chosen recovery policy, asserting named failures, absence
of unsafe output/checkpoints and the intended run status. This may require a small Worker
or compiler change in addition to the three currently listed files.

## Test-plan corrections

- **N7 is equivalent under the current `ground_answer` contract.** That function returns
  every registry passage, modifying only citation flags. Canonicalization drops those flags
  and labels. Checking the registry before it therefore checks the same canonical list as
  checking its result. U5 cannot meaningfully kill that mutation without asserting incidental
  call order or substituting a different `ground_answer` contract.
- Replace N7 with a real unsafe mutation: deduplicate by `id` alone, discarding a second
  record with the same id but different metadata. Add one invalid-text/metadata variant;
  both records must be verified and the invalid one must cause failure. Exact duplicate
  removal remains correct and should preserve the displayed labels/list.
- **D/C labels:** a U5 assertion that the new Response verifier is called once fails on
  the baseline, which calls it zero times. U9's injected verifier exception also cannot
  propagate on a baseline that never invokes it. Those enforcement assertions are D;
  unchanged success output and no-evidence behavior can remain C. U7 must require complete
  source coverage, not only a vacuous “every recorded batch is small” assertion.
- The residual race can remain documented, but it is not necessarily milliseconds:
  checking a source in an early batch leaves the remaining verification batches and
  checkpoint work before publication. Avoid a timing guarantee the implementation does
  not establish.

## Evidence and scope

New artifacts: `evals/results/r01-plan-review-65a7774/`.

- Four real-KB/Worker candidate scenarios: ordinary fail closes correctly; cached Response
  reuse closes correctly; `continue` reveals the worker error; cached-loop-before-Response
  reveals the remaining publication bypass.
- Shape probes cover the explicit missing-verifier exception, malformed empty-object
  restore and N7 canonical-list equivalence.
- The candidate is an audit prototype, not an implementation submitted for merging.
  The real application and original plan/probe files were unchanged.
- No full backend suite, mutation campaign, production campaign, frontend or hosted CI was
  rerun for this plan-only review. No real user data, live model or external write was used.

**Recommendation:** keep D1=A. Revise restore dependency coverage, the helper's fail-closed
input/authority contract, and Response recovery semantics, then implement with the added
commit-boundary and malformed-input tests. No new schema or model-based check is needed.
