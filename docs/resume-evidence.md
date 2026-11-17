# Resume evidence: Relay

A living record of the resume claims for Relay and the evidence behind each one. Re-verify every
row against the current code before you use a bullet. Update the sections in place, and add a
line to the changelog at the bottom.

- Last verified: 2026-11-16, at commit `7c4aa62` (branch `codex/retrieval-answerability-safety`).
- Rule: only DONE work becomes a result bullet. PARTIAL work can appear once as "In progress", with
  no metrics. PLANNED work never appears.

## 1. Bullets

1. **Workflow compiler:** Built a React Flow editor and FastAPI compiler that turns visual graphs into LangGraph StateGraphs (typed conditions, Switch, fork/Join); 12 deployed-process routing, join and binding scenarios pass.
2. **Durable execution:** Built a SQL job queue (SQLAlchemy, PostgreSQL/SQLite) with compare-and-set claims, renewable leases and per-claim fencing; 4 PostgreSQL workers made 50 unique claims, and all 5 stale-worker writes were refused.
3. **External-write safety:** Committed write intent before each send and bound approvals to SHA-256 payload digests; when a worker was killed after a test receiver accepted a write, it logged 1 request and resume returned 409.
4. **Token accounting:** Saved model usage in the same fenced transaction as each checkpoint; SIGKILL tests before and after that commit, on SQLite and PostgreSQL, never restored an answer without its token count.
5. **RAG guard:** Added an opt-in ONNX reader that abstains before generation; on a 53-question calibration set, correct abstentions rose from 9 to 15 of 20 while answer matches fell from 30 to 28 of 33.

Alternate for data-engineering roles (use it in place of bullet 4 or 5):

- **Loop storage:** Moved loop results from the run document into per-item rows with a SHA-256 marker; SQL bytes written by a 100-item SQLite loop fell from 19.5 MB to 0.74 MB (96%), and growth became linear.

Optional final line:

- **In progress:** Repeat node for bounded evaluator-optimizer loops (writer + checker Agent, typed stop rule), with editor support and crash-resume tests; deployed-process scenarios not yet done.

Each line is 230 characters or fewer, including its label.

### Exact technology names (from the code)

| Layer | Names and versions |
|---|---|
| Frontend | React 19.2.9, `@xyflow/react` ^12.11.6 (React Flow), vinext 1.0.0-beta.9 (Vite 8.2.4), TypeScript 5.9.3, Tailwind CSS 4.2.1, Playwright 1.63.0 |
| API | FastAPI 0.141.1, Pydantic 2.13.5, uvicorn 0.52.4, SSE event stream |
| Orchestration | LangGraph 1.2.11 (`StateGraph`, `add_conditional_edges`), langchain-core 1.6.2 |
| Storage | SQLAlchemy 2.0.52 on SQLite or PostgreSQL 17 (psycopg 3.3.5), Alembic 1.20.0, revisions 0001–0006 |
| Retrieval | FAISS (faiss-cpu 1.12.0), Elasticsearch and Pinecone adapters, ONNX Runtime 1.30.0 cross-encoder reranking, `embeddinggemma` embeddings |
| Models | Ollama (`llama3.1`), OpenAI and Claude adapters |
| Security | Fernet (cryptography 50.0.1), PBKDF2-SHA256, DNS-pinned egress, Docker sandbox |
| CI | GitHub Actions, 5 jobs: backend, frontend, browser-layout, postgres-conformance, native-macos |

## 2. Feature status

| Feature | Status | Evidence |
|---|---|---|
| Visual editor (canvas, inspector, run view) | DONE | `frontend/components/workflow-editor.tsx`; 126 unit and 67 Chromium tests (A6.1 Part 2 evidence, 2026-11-13) |
| Compiler to LangGraph `StateGraph` | DONE | `backend/app/compiler.py:8,551,828`; `test_compiler.py` |
| Guaranteed-upstream binding check | DONE | `compiler.py:39-40,225-227`; S08, S22, S23b |
| Typed conditions with exact decimals (A5.1) | DONE | `backend/app/conditions.py`; `test_conditions.py`; S17–S19 |
| Switch multi-way routing (A5.2) | DONE | `backend/app/switch.py`; `test_switch.py`; S20, S21a–c |
| Parallel fork/Join (A5.3 Parts 1–3) | DONE | `test_parallel_branches.py`; S22, S23a–b, S24; `postgres-parallel-sigkill` |
| For-each loops with per-item rows (A4) | DONE | `backend/app/iteration.py`; `test_loop_storage.py`; S09, S15 |
| Leased, fenced SQL job queue | DONE | `storage.py:669-696`; `test_durable.py`; `postgres-four-workers`, `postgres-stale-lease` |
| Per-node checkpoints and resume | DONE | `storage.py` `worker_event`; `postgres-sigkill-loop`; S15, S24 |
| Write-intent markers and uncertain writes | DONE | `worker.py:194-212`, `storage.py:814-845`; S03, S04, S16 |
| Digest-bound approvals | DONE | `approvals.py:55,148-149,183-200`; `test_approvals.py`; S01, S02, S05, S14 |
| Token accounting (TA01–TA08) | DONE | `worker_event` in `storage.py`; `test_accounting_durability.py`; `*-accounting-*-commit` checks |
| Truncation causes and "Incomplete run" | DONE | `_truncation` port; S09 |
| Loop storage bounds (1 MB values, 2,000-char previews, 32 KB events) | DONE | `test_loop_storage.py`, `test_loop_event_bounds.py`; storage probe |
| Alembic migrations, populated downgrade check | DONE | `backend/migrations/versions/0001–0006`; `postgres-migrations`, `populated-downgrade` in CI |
| Knowledge services (ingestion, versioned publish, hybrid search) | DONE | `backend/app/kb/`; `test_kb_*.py` in the full suite |
| Citation validation and evidence registry | DONE | `evidence_registry.py`; full suite |
| Answerability guard (opt-in, default off) | DONE as a feature; evaluation is calibration-set only | `answerability_guard.py`, `kb/answerability.py`; `test_runtime_answerability.py`; `evals/results/product-guard-fresh-2026-09-21*.json` |
| Auth, login budgets, tenant scope, SSRF pinning | DONE | `auth_security.py`, `tool_service.py`; S10–S12 |
| Optimistic concurrency for workflow saves | DONE | S13 |
| Repeat node, backend (A6.1 Part 1) | DONE | `backend/app/repeat.py`; `test_repeat.py` (82 collected tests); `*-repeat-sigkill` (2026-11-11) |
| Repeat node, editor (A6.1 Part 2) | PARTIAL | Committed `7c4aa62`, CI 5/5 (run 43738889884); implementation review not done |
| Repeat deployed scenarios (A6.1 Part 3) | PLANNED | README §6 |
| Elasticsearch and Pinecone backends | PARTIAL | Adapters exist; tests use fakes (`test_kb_search.py:121`), no live service run |
| OpenAI and Claude providers | PARTIAL | Adapters and recorded-shape tests (`test_openai.py`, `test_claude.py`); no hosted model through the grounding gate |
| Live-model workflow shapes (17) | PARTIAL | Last full run 2026-10-06; not re-run since |
| Opt-in request reader | PARTIAL | Passes its safety gate; fails usability: admitted 9 of 20 valid requests (18 required) |
| NLI verifier, section chunking for safety | Rejected | Kept as evidence: NLI agreed with labels 8 of 24 |
| Writes and approvals inside parallel branches (A5.3b) | PLANNED | README §2, §6 |
| Strict token cap (reserve, then settle) | PLANNED | README §2.6 |
| Subgraphs (A6.2), Redis (B2), object storage (B3), orgs and roles (B4), SSO/SCIM (B5), data-source SPI (C1), Text-to-SQL (D), GraphRAG (E), OpenTelemetry (F) | PLANNED | README §6 |
| TLS, managed secrets, backups, load testing | PLANNED | README §7 |

## 3. Evidence

Commands run from the repository root with `.venv/bin/python`. `<scratch>` is a temporary
directory outside the repository.

| Bullet | Claim | Files | Tests and checks | Command | Result | Verified |
|---|---|---|---|---|---|---|
| 1 | Visual graph compiles to a LangGraph `StateGraph` | `backend/app/compiler.py`, `frontend/lib/workflow.ts` | `test_compiler.py`, `test_platform_graph.py` | `PYTHONHASHSEED=0 python -m pytest -q backend/tests/test_compiler.py backend/tests/test_parallel_branches.py backend/tests/test_conditions.py backend/tests/test_switch.py backend/tests/test_platform_graph.py` | 238 passed | 2026-11-16 |
| 1 | 12 deployed scenarios: routing, joins and bindings | `scripts/check_production_scenarios.py`, `scripts/scenarios/*.py` | S08, S17–S19, S20, S21a–c, S22, S23a–b, S24 | `python -u -m scripts.check_production_scenarios --all --out <scratch>` | 33 of 33 rows passed at `7c4aa62` (28 scenarios, backend suite, frontend tests, typecheck, lint, build); `source_files_changed: []`; 3 min 40 s | 2026-11-16 |
| 2 | Compare-and-set claim, renewable lease, per-claim fence | `backend/app/storage.py:669-696`, `backend/app/worker.py:241-246,292` | `test_durable.py`, `test_worker.py`, `test_lifecycle.py` | `PYTHONHASHSEED=0 python -m pytest -q backend/tests/test_durable.py backend/tests/test_worker.py backend/tests/test_lifecycle.py` | 10 passed | 2026-11-16 |
| 2 | 4 PostgreSQL worker processes, 50 runs, 50 unique claims, 100 deliveries with no duplicate | `scripts/check_milestone_storage.py:109-132` | `postgres-four-workers` | CI `postgres-conformance`: `python -u -m scripts.check_milestone_storage --checks postgres-four-workers …` | CI run 43738889884 success at `7c4aa62`; local report `evals/results/a61-part1-2026-11-11/storage-all.json`: `claims: 50` | CI 2026-11-15; local 2026-11-11 |
| 2 | A stalled worker's 5 late writes are refused | `check_milestone_storage.py:135-159` | `postgres-stale-lease` (SIGSTOP past a real 1 s lease) | same CI step | refused: event, finish, reserve, mark_write, settle_write; final run unchanged | CI 2026-11-15; local 2026-11-11 |
| 3 | Intent committed before the send; outcome settled with the result | `worker.py:194-212`, `storage.py:814-845` | S03, S04, S16 | gate command above | S16: `writes: 1`, `uncertain_resume_refused: true` | 2026-11-16 |
| 3 | Approval bound to a SHA-256 digest of canonical JSON | `approvals.py:54-55,148-149,214` | `test_approvals.py`, `test_execution_safety.py`; S01, S14 | `PYTHONHASHSEED=0 python -m pytest -q backend/tests/test_approvals.py backend/tests/test_execution_safety.py` | 31 passed; S01 `distinct_approvals: 3`; S14 `writes: 0` | 2026-11-16 |
| 4 | Usage, event, checkpoint and loop item commit in one fenced transaction | `storage.py:703` `worker_event` | `test_accounting_durability.py`, `test_parallel_accounting.py`, `test_budget_recovery.py` | `PYTHONHASHSEED=0 python -m pytest -q backend/tests/test_accounting_durability.py backend/tests/test_parallel_accounting.py backend/tests/test_budget_recovery.py` | 42 passed, 3 skipped (the PostgreSQL cases need `RELAY_TEST_POSTGRES_URL`) | 2026-11-16 |
| 4 | SIGKILL before and after the commit, SQLite and PostgreSQL | `check_milestone_storage.py` `accounting_commit` | `{sqlite,postgres}-accounting-{before,after}-commit` | CI runs the 2 PostgreSQL checks; the SQLite checks ran locally | before: no checkpoint, 2 model calls, 15 tokens counted; after: checkpoint, 1 call, 15 tokens | CI 2026-11-15; local 2026-11-11 |
| 5 | Unanswerable abstentions 9→15 of 20; answer matches 30→28 of 33; false abstentions 0→2 of 33 | `backend/app/answerability_guard.py`, `backend/app/kb/answerability.py`, `scripts/check_grounding_eval.py` | `test_runtime_answerability.py`, `test_answerability_reader.py`, `test_grounding_gate_cli.py`, `test_eval_metrics.py`, `test_agent_grounding.py` | `python -m scripts.check_grounding_eval evals/results/product-guard-fresh-2026-09-21.json --expected-count 53` (and the same for `answerability-scored-generation.json`) | Saved product-path report accepted; saved baseline rejected (safety, compliance); tests: 73 passed. Generation was not re-run | Live run 2026-09-21; offline re-check 2026-11-16 |
| Alt | Run row 1,005,440 → 38,090 bytes (60 KB items, 100 items) | `storage.py:242-244,567-590` | `test_loop_storage.py`, `test_loop_checkpoint.py`, `test_loop_completeness.py`, `test_loop_event_bounds.py` (145 passed) | `AGENT_RUN_BUDGET=200 PYTHONPATH=. python evals/results/loop-checkpoint-2026-09-30/storage_probe.py OUT.json 60000`, at HEAD and in a `git archive 11f8773` copy | 11f8773: 1,005,440; HEAD: 38,090 (−96%). Total written 5,492,460 → 1,639,312 | 2026-11-16 |
| Alt | Bytes written, 1 KB items, 100 items: 19,470,202 → 738,803 | as above | as above | `…/loop-storage-2026-09-29/storage_probe.py OUT.json 1000` in a `git archive bc62b0b` copy; the checkpoint probe at HEAD | Per doubling of items: before ×3.41, ×3.67; HEAD ×1.95, ×2.07. No value dropped in either | 2026-11-16 |

Measurement limits: the storage probe counts bound SQL parameter bytes on SQLite with one worker
and a scripted tool. It does not count WAL, page or index overhead. The 60 KB comparison to
`bc62b0b` (642,170,768 → 1,639,312 bytes) is not used, because the two versions drop different
numbers of values under their value caps.

## 4. Claims removed or changed

| # | Original claim | Verdict | Change and reason |
|---|---|---|---|
| 1 | Editor + compiler to LangGraph; path-aware validation, typed conditions, multi-way routing, parallel joins; execution tests verify branch synchronization and output availability | Supported | Kept. "State machines" became "StateGraphs" (the class in the code). Result is now the 12 deployed scenarios, not a general "tests verify". |
| 2 | Atomic claiming, renewable leases, stale-worker fencing, durable checkpoints; 50 unique claims across 4 PostgreSQL workers; expired workers cannot overwrite completed results | Supported | Kept. "Atomic job claiming" is a compare-and-set `UPDATE … WHERE`, not `SKIP LOCKED`. The fence is a random claim id per lease, checked in the same transaction as each write. "Overwriting completed results" became the measured "5 stale-worker writes refused". |
| 3 | SHA-256 request fingerprints and persisted execution attempts; crash after an external API accepted a write; single receiver request; unsafe retries blocked | Supported, wording changed | The SHA-256 digest binds *human approvals*. It is not an idempotency key for every write. Writes without approval use a per-invocation intent marker. The receiver is a local HTTP test receiver, not an external API. Relay does not send an idempotency key to the remote system; reconciliation is manual. |
| 4 | Crash-safe token accounting, usage atomic with checkpoints, SQLite and PostgreSQL | Supported, wording changed | "Crash-safe" is too broad. A response lost before the commit is paid for again and is not counted. A provider that reports no usage adds nothing. The limit is an admission limit, not a strict cap. The bullet now states only what the SIGKILL tests show. |
| 5 | Run-record size 1.01 MB → 37 KB (96%) in a 100-item SQLite benchmark | Reproduced (1,005,440 → 38,090 bytes at HEAD); moved to the alternate | The number is correct but narrow. It is only the `runs` row, and the step removed a duplicate copy of results that also lived in item rows. Total bytes written fell 70%, not 96%. The end-to-end 1 KB comparison (−96% bytes written, quadratic → linear) is the stronger and fairer number. |
| 6 | 45% → 75% correct abstentions on 53 questions; expected-fact matches on 28 of 33 | Numbers correct; framing changed | "Retaining 28 of 33" hides a cost: the unguarded baseline matched 30 of 33, and the guard added 2 false abstentions. Counts replace percentages (n = 20). "Labeled" became "calibration set", because the gate thresholds (≥0.70, ≥0.81) were fixed after the same 53-question run (`docs/evaluations/2026-09-21-grounding-fresh-result.md` says so). 15 of the 20 unanswerable labels are assistant annotations with no independent human review. The reader's margin threshold (0) was predeclared and was not tuned. |

### Technical correctness check (gaps between claims and code)

1. **Claiming is atomic, but it is not `FOR UPDATE SKIP LOCKED`.** `claim_next` reads up to 20 candidates, then runs a compare-and-set `UPDATE` with the eligibility predicate and checks `rowcount`. This is correct on PostgreSQL READ COMMITTED (the `WHERE` is evaluated again after the row lock) and on SQLite (one writer). Under heavy contention, workers can block on the same row and waste attempts. There is no throughput measurement.
2. **The fence is a per-claim id, not a monotonic token.** Each claim gets `new_id()` (`worker.py:292`), and `_fence` checks `owner`, `status` and `lease_until > time.time()` in the transaction of the write. This blocks a stale worker's database writes. It cannot block a side effect that a stale worker already sent to a remote system. The write markers handle that case.
3. **Lease time uses each worker's wall clock**, not the database clock. On one host this is safe. With workers on several hosts, clock skew can make two workers both pass the lease check. Deployment today is loopback only.
4. **Heartbeats are frequent.** The run loop renews the lease about every 0.2 s for each active run (`worker.py:244-246`). Each renewal is a database write. This is not measured at scale.
5. **The approval digest is stable.** It is SHA-256 over `json.dumps(sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)`. On resume the worker builds the payload again and compares it with the reviewed payload and digest (`approvals.py:148-149,214`).
6. **Intent is committed before the send.** `mark_write` commits in its own fenced transaction before `tools.execute` (`worker.py:203-206`). The request is checked first (`prepare_write`), so a refusal at that stage sends nothing and is not "uncertain". A completed result is read back first (`completed_action`), so a settled write is never sent again.
7. **Usage and checkpoint commit together.** `worker_event` writes accounting, the event, usage rows, the loop item and the checkpoint in one session and one `commit()`.
8. **Answerability data is not independent of the gate.** See row 6 above. The reader itself was not tuned on these questions. The 7 holdout questions are all unanswerable, so they cannot measure false abstention.
9. **The README sentence "Thresholds were committed before the evaluation that now passes them" is true but can mislead.** The thresholds were set from an earlier run of the same configuration on the same questions. This audit did not change the README.

## 6. Next PARTIAL work with the most resume value

1. **Repeat node, end to end (A6.1 Parts 2 and 3).** Evaluator-optimizer loops are a common agent pattern, and a bounded, crash-resumable version is a strong signal for AI engineering roles.
   - Done means: the Part 2 implementation review is closed. Part 3 adds deployed-process scenarios to `check_production_scenarios.py`: SIGKILL in iteration 2 resumes without repeating iteration 1, a budget stop is reported as incomplete, a checker abstention stops the loop, and a write in the body is refused before any run. CI passes 5 of 5.
   - Measure: model calls and tokens for each iteration on resume against an uninterrupted run (counts, from the accounting rows).
2. **Held-out answerability evaluation.** This closes the biggest defensibility gap in bullet 5.
   - Done means: a new frozen question set, with labels written and reviewed by a person before any run, that includes answerable and unanswerable holdouts (for example 30 and 30). The thresholds are committed before the first run, and the set is never used for tuning.
   - Measure: abstention and false abstention as counts with Wilson 95% intervals, guard on and off, the same model and settings.
3. **One hosted model through the grounding gate (OpenAI or Claude).** This uses the provider adapters and the token accounting together.
   - Done means: the 53-question gate (and the new held-out set from item 2) runs on one hosted model, with model ids and settings recorded.
   - Measure: gate metrics as counts, plus tokens and cost for each question from `run_tokens` rows. This needs API keys and paid calls, so it needs your approval first.

## Changelog

- 2026-11-16: File created at `7c4aa62`. Six draft claims checked; numbers for claims 2, 3, 5 and 6 reproduced or re-checked. Claim 5 moved to an alternate bullet; claims 3, 4 and 6 reworded. Re-ran the deployed gate (33 of 33), 539 targeted backend tests and the storage probe on `bc62b0b`, `11f8773` and HEAD. The PostgreSQL checks and the live grounding run were not re-run (CI and saved evidence used).
- 2026-11-16: Corrected the `test_repeat.py` count (82 collected, not 66) and the `worker_event` line (`storage.py:703`), found while writing `docs/status-report.md`.
