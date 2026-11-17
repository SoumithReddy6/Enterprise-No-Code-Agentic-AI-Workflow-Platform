# Relay gap audit and closing plan

Audit date: 2026-11-16. Commit: `24e659c` on `codex/retrieval-answerability-safety`. No source code
was changed. Tracked files had no local changes at the start of the audit.

Conventions:

- "unmeasured": no measurement exists in the repository.
- "not run": this audit did not run the check (it needs the network, API keys, or more than 5 minutes).
- "from the code": a reading of the code, not a measurement.
- Cost: S < 1 day, M 1–5 days, L > 5 days. Costs are estimates.
- Each resume line has `[measured value]` placeholders. No value in Part B is a measurement.

What this audit ran: the full core-code read (`storage.py`, `worker.py`, `compiler.py`,
`approvals.py`, `auth.py`, `auth_security.py`, `schema.py`, `main.py`, `kb/search.py`, `kb/rpc.py`,
`kb_gateway.py`, migrations `0001`–`0006`, both CI workflows), backend test collection, and the
deployed scenario gate (`--all`). It did not run: GitHub CI status (network), the PostgreSQL
checks, the browser suite, the grounding gate, the live-model campaign.

---

## 1. Verdict

Relay is a strong single-user correctness project. It is not yet a platform. The write protocol,
the fence, the atomic checkpoint-with-accounting commit and the digest-bound approvals are real,
tested with process kills, and better than most portfolio work. But the three things the capstone
must prove beyond the author's job are absent: **multi-tenancy** (one tenant per account, no
roles, no actor on a run or an approval, isolation in application code only), **performance**
(no throughput, latency or write-rate number exists; the highest tested concurrency is 4 runs), and
**inspectable code quality** (no formatter, linter or type check; lines up to 670 characters; two
functions of more than 200 lines). The evaluation numbers are honest but weak: one 8B local model,
assistant-written labels, thresholds fixed after a run on the same questions. The project also
cannot be tried by a reviewer: no container image, no demo, loopback only. The correct next step is
not A6.2. It is to turn the proven single-user core into a measured, multi-tenant, deployable
system, and to cut the rest of the old roadmap.

---

## 2. Part A1 — verified state

### 2.1 Is A6.1 complete?

| Check | Result | Evidence |
|---|---|---|
| Backend (Part 1) | Done | `backend/app/repeat.py`; `evals/results/a61-part1-*` |
| Editor (Part 2) and its review | Done; review closed | `docs/reviews/a61-part2-review-fix-recheck-2026-11-16.md`; fix commit `8705b7f` |
| Deployed scenarios (Part 3) present | Done: S25, S26a–c, S27, S28, S29 | `scripts/scenarios/repeat_loops.py`; `docs/production-scenario-testing.md:55-71` |
| Deployed scenarios pass | See §2.3 (gate run in this audit) | scratch output of `check_production_scenarios --all` |
| CI green on `24e659c` | **not run** (network). The earlier report gives run 43990904714, 5 of 5 | not re-verified |
| Part 3 review recorded | **Gap**: no Part 3 review file in `docs/reviews/` (only the Part 2 recheck) | `ls docs/reviews \| grep a61` |
| Repeat crash checks in CI | **Gap**: `{sqlite,postgres}-repeat-sigkill` exist but CI does not run them | `scripts/check_milestone_storage.py:588`; `.github/workflows/ci.yml:126-130` |
| Deployed gate in CI | **Gap**: the gate workflow runs only by manual dispatch, so "CI 5 of 5" does not include the 34 scenarios | `.github/workflows/production-scenarios.yml:2-3` |

Conclusion: A6.1 is functionally complete. Three evidence gaps remain (review record, two CI
omissions). W04 closes them.

### 2.2 README and docs claims that code or tests do not support

| # | Claim | Location | What the evidence shows |
|---|---|---|---|
| 1 | "never send an external write twice" | `README.md:3` | True for Relay's own second send on the tested paths. Relay sends no idempotency key and has no reconcile path, so it cannot make this promise for the remote system. The body bounds it correctly (`README.md:154`) |
| 2 | "thresholds fixed before measurement" | `README.md:13`, `README.md:404` | The thresholds were chosen after an earlier run of the same configuration on the same 53 questions. The evaluation document calls the result "a repeatability check, not an independent held-out safety evaluation" (`docs/evaluations/2026-09-21-grounding-fresh-result.md:33`) |
| 3 | "53 labelled questions" | `README.md:404` | The labels are assistant annotations, with no independent human review, and the holdout has no answerable question (`docs/answerability-safety-report.md:45`) |
| 4 | "17 live-model workflows" as current verification | `README.md:13` | Last full run 2026-10-06 (`README.md:426`), before Switch, fork/Join and Repeat |
| 5 | Stack: "OpenAI, Claude · FAISS, Elasticsearch, Pinecone" | `README.md:12` | The provider adapters exist (`backend/app/providers.py:344-411`). The earlier audit found no live run of OpenAI, Claude, Elasticsearch or Pinecone (`docs/status-report.md:361`, `:491`). This audit found no evidence that changes that |
| 6 | "27 scenarios" | `README.md:425` | The header says 34 (`README.md:13`). The code has 34 S-rows (S01–S29 with variants). Line 425 is out of date |
| 7 | "Every query is tenant-scoped" | `README.md:390` | Tenant scope is a parameter that each `Store` method must apply by hand, with the default `'local'` (`backend/app/storage.py:350,368,372,487,495,509`). Operator metrics are deployment-wide by design (`backend/app/operator_metrics.py:99-125`). `completed_action` reads by run id with no tenant (`storage.py:820-824`). No database-level rule exists |
| 8 | "Alembic owns the schema" | `README.md:313` | True for the core database only. The knowledge services use `create_all` and an ad hoc `ALTER TABLE` at startup (`backend/app/kb/search.py:52-57`, `backend/app/kb/management.py:52-69`). Two data migrations also run at startup outside Alembic (`storage.py:268-271`) |
| 9 | "A read-only copy of a real workspace database was upgraded with all 27 table row counts preserved" | `README.md:318` | Verified only at revision `0003` on 2026-10-05; the 2026-11-11 run of the same check failed for lack of a workspace copy (`docs/status-report.md:493`). Not checked at `0006` |
| 10 | "an independent reviewer" | `README.md:432` | The review files I checked name no reviewer. An interviewer will ask who it was. If the reviewer was a second AI assistant, say so |
| 11 | Repeat is "PARTIAL" / "PLANNED" | `docs/status-report.md:47`, `docs/resume-evidence.md:25,67-68` | Out of date: A6.1 Parts 2 and 3 are done at `24e659c` |

Verified as correct: 2,157 backend tests collected (this audit); five CI jobs (`ci.yml:13,43,55,76,100`).

### 2.3 Deployed gate result (this audit)

Command: `.venv/bin/python -u -m scripts.check_production_scenarios --all --out <scratch>`, run
on 2026-11-16 at `24e659c`, SQLite, local fixtures, one laptop.

| Item | Result |
|---|---|
| Rows passed | 40 of 40: 34 scenarios (S01–S29 with variants), M01, and 5 stages (backend, frontend tests, typecheck, lint, build) |
| S25, S26a, S26b, S26c, S27, S28, S29 | all passed |
| Backend stage | 2,154 passed, 3 skipped (the 3 need PostgreSQL), 116.05 s |
| `source_files_changed` | `[]` |
| Wall time | 258.5 s |

Not verified by this run: PostgreSQL behavior, the browser suite, GitHub CI.

Note: `docs/status-report.md` and `docs/resume-evidence.md` are untracked files in git. Commit them
or they are not part of the public record.

---

## 3. Part A2 — gap register

Severity is the damage to the portfolio goal, not only to correctness.

### Area 1 — code quality and readability

| ID | Sev | Gap | Evidence | Why a staff interviewer cares | Cost |
|---|---|---|---|---|---|
| Q1 | High | No formatter, linter or type check for Python. Dense style: many statements per line, lines up to 670 characters | No `pyproject.toml`, `ruff.toml` or `mypy.ini` in the repository root; no lint step in `ci.yml:23-32`. `worker.py:229` (670 characters), `storage.py:878`, `approvals.py:80`, `storage.py:736`. `storage.py` has 53 `;`-joined lines, `agent_runtime.py` 44 | The first 30 seconds on GitHub decide the read. This style reads as generated or minified code, and it hides the good design | M |
| Q2 | Medium | Two functions carry most of the runtime. `compile_workflow` has 24 parameters and a nested handler of about 220 lines. `Worker._execute` holds a nested string-dispatch `platform_resolver` | `compiler.py:564`, `compiler.py:625-842`; `worker.py:67-274`, `worker.py:137-221` | Shows whether the author can keep a growing system changeable. These functions cannot be unit-tested in parts | M |
| Q3 | Medium | Imports inside functions in many places, to avoid import cycles. The module layers are not clean | `storage.py:178,251-254,533,543,546,605`; `compiler.py:567-572,651,656`; `auth.py:212-214` | A layering problem that will get worse when a `Principal` must pass through every layer | M |
| Q4 | Low | Repository hygiene: stray files at the root, runtime and test dependencies in one file, legacy nodes in the registry | `:memory:.ses` and `probe-results.json` at the root; `backend/requirements.txt` holds `pytest==9.1.1`; `registry.py:286-287,308,331-358` | Small, but each one costs trust | S |
| Q5 | Medium | Test coverage is unmeasured | No coverage tool configured (`ci.yml`) | "1.7× test code" is not a coverage number | S |

### Area 2 — concurrency and durability under load and multiple hosts

| ID | Sev | Gap | Evidence | Why it matters | Cost |
|---|---|---|---|---|---|
| C1 | High | Leases use each process's wall clock (`time.time()`), stored as a float. No database clock, no monotonic source | `storage.py:670,676,690,696`; `approvals.py:74,116`; `readiness.py:29` | From the code: with clock skew between hosts, a worker with a fast clock takes a lease that is still live. The owner check still blocks the old worker's database writes, so writes are not doubled. But a run with a marked write then fails as "uncertain" (`worker.py:125`), and paid model calls repeat. "N worker processes" (`README.md:43`) is proven on one host only | S–M |
| C2 | High | Heartbeat every 0.2 s for each active run: one `SELECT`, two `UPDATE`s and one commit each time | `worker.py:244-247`; `storage.py:693-701` | From the code: about 5 commits/s for each run. 16 workers × 4 slots gives about 320 commits/s of heartbeat only. Unmeasured | S |
| C3 | High | Claim reads the 20 oldest candidates with no lock, then tries a compare-and-set on each. All idle workers read the same head rows. Each idle worker polls 10 times/s. The only index is on `status`. The order is global FIFO | `storage.py:669-686`; `worker.py:291-300`; `0001_baseline_baseline.py:106` | From the code: on PostgreSQL the losers block on the winner's row lock until its commit, and that commit also inserts an event and rewrites the run JSON (`storage.py:678-681`). Thundering herd; no fairness. Unmeasured | M |
| C4 | Medium | Synchronous database calls on the event loop. In the worker they share the loop with the heartbeats of the other slots. In the API, `async def` routes call the synchronous store | `worker.py:124,125,138,198,203,211,249,267,273`; `main.py:120,122,146,150,159,163` | One slow commit stalls every heartbeat in the process and every API request | M |
| C5 | High | No attempt counter and no maximum attempts. A run that kills its worker (out of memory, native crash) is claimed again after each lease expiry, without end | `storage.py:112-119` (no attempt column); `storage.py:669-686` | The classic poison-message question. Today the answer is "it loops forever and takes a slot" | S |
| C6 | Medium | No graceful stop on `SIGTERM`: only `KeyboardInterrupt` is handled. A rolling restart leaves runs to lease expiry (30 s) and re-runs nodes in flight | `worker.py:301-303,314-316` | Deploys cause stalls and repeated paid calls | S |
| C7 | High | No per-tenant fairness or quota. One tenant can fill the global queue. Limits are per run only | `storage.py:673` (`ORDER BY created`); `main.py:155-161` (no submission limit); `README.md:467` | The first multi-tenant question after isolation | M |

### Area 3 — data model integrity

| ID | Sev | Gap | Evidence | Why it matters | Cost |
|---|---|---|---|---|---|
| D1 | High | No foreign key in any table | `grep ForeignKey backend/app` returns nothing; `0001_baseline_baseline.py` | Acceptable while nothing is deleted. Not acceptable with retention, deletion and organizations | M |
| D2 | Medium | No `CHECK` on status columns. Three time formats: ISO strings sorted as text, float epochs, integer epochs | `storage.py:22,30,31,50,115,117`; `approvals.py:40-42`; `auth.py:42`; `storage.py:521` | A wrong status string is silently stored. Text-sorted timestamps are fragile | S–M |
| D3 | High | The run is one JSON document (workflow snapshot, checkpoints, write-intent markers, agent frames). It is rewritten whole on each checkpoint, each write marker and each claim. The safety-critical `write_nodes` list is a JSON array changed by read-modify-write | `storage.py:679,736-737,757,818,842-844`; `approvals.py:193-194,202` | The same quadratic-write class as the loop bug that was fixed (`README.md:197-215`), now for checkpoints. Markers cannot be queried or constrained, so a reconcile screen has nothing to list. Write bytes for a many-node run: unmeasured | M |
| D4 | Medium | Two migration authorities plus unmanaged knowledge-service schemas. Every process upgrades the schema at startup | `storage.py:264-271,323-344`; `operator_metrics.py:88-97`; `kb/search.py:52-57`; `kb/management.py:52-69` | A rolling deploy runs old code against a new schema. The knowledge databases have no downgrade or drift check | M |
| D5 | Medium | `tenant_id` is absent from most run child tables | `storage.py:54-67` (`run_loop_items`), `:99-110` (`run_action_results`), `:112-119` (`execution_jobs`); `0005_admission_metrics.py:12-18` | Row-level security and per-tenant retention need the column on every table | M |
| D6 | Medium | Each knowledge base is one JSON row (documents, versions, jobs, uploads). Every management call takes a global lock and loads and deep-copies all rows of the tenant (all tenants for `claim`) | `kb/management.py:146-160` | Every retrieval calls `resolve`, so retrieval is serialized across all tenants. Unmeasured | M–L |

### Area 4 — security and multi-user readiness

Every single-user assumption found:

| ID | Sev | Gap | Evidence | Cost |
|---|---|---|---|---|
| S1 | Critical | One tenant for each account. No organization, membership or role. Each registration makes a new tenant. Every route authorizes by tenant only | `auth.py:24-29` (account holds `tenant_id`), `auth.py:208`; `main.py:105-195`; `tool_api.py:11-15`; `kb_gateway.py:97-146`; `README.md:473` | L |
| S2 | Critical | No actor on a run or an approval. A run stores no initiator. A decision is recorded with the tenant only. Separation of duties is impossible, and "who approved this write?" has no answer | `storage.py:472-478`; `main.py:159`; `approvals.py:26-44` (no `decided_by`), `approvals.py:141-171` | M |
| S3 | High | Isolation is in application code only. Scope is a parameter with a default of `'local'`; one forgotten filter is a silent cross-tenant read. No row-level security. Operator access is "the first tenant", and its metrics cover all tenants | `storage.py:350,368,372,378,383,391,404,420,427,455,472,487,495,501,509,771,866`; `storage.py:820-824`; `auth.py:144-153`; `operator_metrics.py:99-125` | M–L |
| S4 | High | No audit log. Security events go to stdout through a field allowlist that has no actor field | `observability.py:18,27-35`; `auth.py:240,252`; `approvals.py:170` | M |
| S5 | High | Authentication is password and cookie only. No OIDC, no API token or service account, no password change, no session list or revoke-all. Fixed 7-day sessions | `auth.py:19-20,121-133,229-254` | M–L |
| S6 | High | One Fernet key for all tenants, from the environment or a file next to the database. No key id, no rotation. Credentials cannot be rotated or deleted. The knowledge services share one static key and accept the tenant that the caller states | `storage.py:130-138,250`; `main.py:54,145-148`; `kb/rpc.py:22-32,59,78,86-87` | M |
| S7 | Medium | Loopback assumptions in code: fixed trusted hosts, fixed CORS origins, fixed proxy target, no client IP forwarding (all users share one IP login budget) | `main.py:65-66`; `frontend/app/api/[...path]/route.ts:5,40-50`; `auth_security.py:33-41`; `README.md:398` | S |
| S8 | High | Retrieval permission is tenant-wide. The worker runs with tenant authority, not the initiator's. With roles, a user who cannot read a knowledge base can build a workflow that reads it (confused deputy) | `kb_gateway.py:33-43`; `kb/search.py:223-229`; `worker.py:124,138,213` | M |
| S9 | Medium | No prompt-injection control or test set for text that reaches an Agent from tools or retrieval. Only approvals and citation checks limit the effect | `docs/status-report.md:308` | M |
| S10 | Medium | Approval rate limit and agent memory are tenant-wide; memory writes take one global lock for all tenants | `approvals.py:127-139`; `agent_memory.py:21-22` | S |

### Area 5 — evaluation methodology

| ID | Sev | Gap | Evidence | How defensible is the number | Cost |
|---|---|---|---|---|---|
| E1 | High | Grounding gate: thresholds set after a run on the same 53 questions; assistant labels; the holdout has only unanswerable questions; rates reported to three decimals with n = 33 and n = 20 and no interval | `docs/evaluations/2026-09-21-grounding-fresh-result.md:33`; `docs/answerability-safety-report.md:45`; `README.md:406-412` | Weak. It is a regression check on a calibration set. 15 of 20 has a wide interval | M |
| E2 | High | One 8B local model. No hosted model has run through the gate or a workflow | `README.md:476`; `docs/status-report.md:361` | The quality claims do not transfer to any model an employer uses | S–M |
| E3 | Medium | Scoring is substring match and abstention heuristics. The NLI judge was rejected (8 of 24). No validated judge exists | `README.md:414-416` | "Answer contains expected fact" is not correctness. Honest, but thin | M |
| E4 | Medium | Evaluation is not a CI gate. The live campaign is 6 weeks old and predates three features | `README.md:426,430` | Numbers can rot without notice | S |
| E5 | Medium | No workflow-level or agent-level evaluation: task success, tool-call correctness, Repeat convergence with a real checker, cost and latency per task. The scenarios use scripted models | `docs/production-scenario-testing.md:70-71` | The scenarios prove control flow, not model behavior | M |
| E6 | Low | The request reader fails its own usability gate: 9 of 20 valid requests admitted, 18 required | `README.md:477` | A failed feature in the main path. Honest, but it must be frozen or fixed | S (freeze) |

### Area 6 — performance and scalability

| ID | Sev | Gap | Evidence | Where it breaks first (from the code) | Cost |
|---|---|---|---|---|---|
| P1 | Critical | No load measurement of any kind: runs/min, claim latency, queue wait, database writes for each run are all unmeasured. Highest tested concurrency: 4 runs | `docs/status-report.md:247-254`; `scripts/check_milestone_storage.py:109-132` | Unknown; see C2, C3, P2, P3 for the candidates | M |
| P2 | High | The event stream polls the database every 0.15 s for each open client: a session read (2 queries), a status read, and an event read that reads the status again | `main.py:202-216`; `auth.py:125-129`; `storage.py:501-504` | About 5 queries per tick, about 33 queries/s for each open browser tab. Probably the first API limit. Unmeasured | M |
| P3 | High | Write amplification for each run: the whole run JSON on each checkpoint (D3); one `SELECT max(seq)` for each event; the run page returns all events with no limit; cancel loads the full run up to 12 times | `storage.py:538,736-737`; `storage.py:493`; `main.py:176-184` | A 100-node graph (the limit, `models.py:43`) rewrites a growing document 100 times. Unmeasured | M |
| P4 | Medium | Retrieval path: 5 sequential RPCs, each with a new HTTP client and connection; the global management lock (D6); all chunk metadata of a segment loaded into Python for each query, BM25 in Python; limits of 20 documents and 50,000 chunks | `kb/rpc.py:58`; `kb_gateway.py:34-53`; `kb/search.py:128,231,246-251` | Latency is measured only on 45 synthetic questions (`docs/status-report.md:234`). Concurrency: unmeasured | M |
| P5 | Medium | Fixed limits: run timeout 120 s, 4 slots, 30 s lease, default connection pool. No token streaming, so time to first token does not exist | `worker.py:19,58,310`; `storage.py:249,669`; `providers.py:150,258` | Long agent runs fail at 120 s. No tuning is possible without a code change | S |

### Area 7 — operability

| ID | Sev | Gap | Evidence | Cost |
|---|---|---|---|---|
| O1 | High | An uncertain write stops the run, and then nothing helps the operator: no reconcile route or screen, and no idempotency key sent to the remote system | `worker.py:193,209`; `storage.py:862-864`; no reconcile route in `main.py`; no `Idempotency` in `tool_service.py` | M |
| O2 | High | No deletion and no retention. Events, loop items, usage rows, approval payloads and sessions grow without limit. An organization cannot be deleted | `main.py:66` (no `DELETE` method); no purge code; `auth.py:159` (sessions purged only at login) | M |
| O3 | High | No metrics endpoint and no traces. One SQL aggregate endpoint and JSON lines on stdout. Queue depth, queue age, claim latency, lease expiries and heartbeat failures are not exposed | `main.py:100-103`; `observability.py:27-35`; `operator_metrics.py:99-125` | M |
| O4 | Medium | Timeouts do not agree: run 120 s (fixed), knowledge RPC 180 s, tool HTTP 15 s. No per-node timeout | `worker.py:19,239`; `kb/rpc.py:58`; `tool_service.py:223` | S |
| O5 | Low | Backup and restore are not tested | `README.md:474` | S–M |

### Area 8 — deployability, demo-readiness and scope

| ID | Sev | Gap | Evidence | Cost |
|---|---|---|---|---|
| X1 | High | Not deployable: no application image; `compose.yaml` holds only PostgreSQL; loopback only | `compose.yaml`; `README.md:11,474` | M |
| X2 | High | No demo: no video, no screenshot in the README, no hosted instance | `docs/status-report.md:397-402` | S–M |
| X3 | Medium | CI does not run the deployed gate on push, and omits the Repeat crash checks | `production-scenarios.yml:2-3`; `ci.yml:126-130` vs `check_milestone_storage.py:588` | S |
| X4 | Medium | Breadth without signal: 24 node types (5 hidden), integrations tested with fixtures only, two vector backends never run live, a 519-line README with no numbers on the first screen, a buzzword repository name | `docs/status-report.md:32-60`; `README.md:489` | S |
| X5 | Low | The roadmap promises Redis, SCIM, Text-to-SQL and GraphRAG | `README.md:440-469` | S |

Signal against breadth: the write protocol, fence, approvals, accounting and typed conditions add
signal. Jira, Confluence, GitHub, SMTP, Elasticsearch, Pinecone, the request reader and the legacy
nodes add breadth only. Subgraphs would add breadth.

---

## 4. Part A3 — adversarial interview

| # | Question | Answer today | Closed by |
|---|---|---|---|
| 1 | "Two workers on two hosts, 5 seconds of clock skew. Walk me through a lease takeover." | **Weak.** The owner check saves the database, but the lease compares against each host's wall clock (`storage.py:670,690`). Tested on one host only | C1 → W06 |
| 2 | "What is your throughput, and what breaks first when I add workers?" | **None.** Unmeasured. From the code: heartbeats, claim contention, then run-JSON rewrites | P1, C2, C3, P3 → W05, W06, W07, W20 |
| 3 | "Why compare-and-set and not `SKIP LOCKED`? Show the contention numbers." | **Weak.** The mechanism is explained (`docs/resume-evidence.md:117`); no number exists | C3 → W07 |
| 4 | "A run crashes its worker every time. What happens?" | **None.** It is claimed again without end (`storage.py:669-686`) | C5 → W07 |
| 5 | "Prove that a user in organization X cannot read organization Y's runs, even when a developer forgets a `WHERE`." | **None.** One tenant for each account; filters by hand; no database rule. S12 tests the application filter only | S1, S3 → W09, W10, W11 |
| 6 | "Who approved this write? Can the person who started the run approve it?" | **None.** No actor is stored (`approvals.py:141-171`) | S2, S4 → W12, W13 |
| 7 | "The receiver accepted the write and the response was lost. What does the operator do next, and why is there no idempotency key?" | **Weak.** The protocol and S16 are strong. After the stop there is nothing | O1 → W17 |
| 8 | "Who labelled the grounding set? Was the threshold fixed before you looked? What is the interval on 15 of 20?" | **Weak.** The documents are honest about it; the README is not | E1 → W21, W03 |
| 9 | "Does it work with a hosted model, and what does one run cost?" | **None.** One 8B local model; tokens counted, no price | E2 → W21 |
| 10 | "It is 3 a.m. and runs are stuck. What do you look at?" | **Weak.** JSON logs and one SQL endpoint. No queue metric, no trace | O3 → W19 |

Questions the repository already answers strongly (keep them on the first screen of the README):
"kill -9 after the receiver accepted the write" (S16), "a stale worker wakes up" (`postgres-stale-lease`),
"the approved payload changes before the send" (S14), "a crash at the accounting commit"
(`*-accounting-*-commit`).

---

## 5. Part A4 — direction

| | A. Multi-tenant platform + measured scale | B. Evaluation + safety depth | C. Feature breadth |
|---|---|---|---|
| Content | Organizations, roles, OIDC, API tokens, row-level security, separation of duties, audit log, fair queue; load harness; clock, heartbeat and claim fixes; reconcile, retention, traces; container deploy | Human-labelled held-out sets, hosted models, a validated judge, injection red-team set, agent-task evaluation, evaluation in CI, vLLM serving | A6.2 subgraphs, writes in branches (A5.3b), triggers, more integrations |
| What it proves | Platform design, database depth, performance engineering, security design. These are the skills the job did not show | Evaluation rigor for LLM systems | That more nodes can be added |
| Effort | 6–8 weeks | 3–4 weeks, plus labelling time and API cost | 3–5 weeks each |
| Risk | Row-level security and the principal change touch every query; they can break the fence if done carelessly | Overlaps the job history (RAG, release gating). Numbers depend on models, so they age | Each feature adds surface without closing one interview question in §4 |
| How a hiring manager reads it | "This person can build and operate a shared system, and measures it." | "Solid evaluation, like the previous job." | "A large side project." |

**Recommendation: direction A, with a thin slice of B** (one held-out human-labelled set, one
hosted model, intervals, a CI check). Reason: A closes 8 of the 10 questions in §4 and proves the
three skills the job could not. The slice of B closes the other 2 at low cost. C closes none.

**A6.2 (subgraphs): later, not now.** Reasons: (1) it closes no gap in §3; (2) it needs published
workflow versions first (`README.md:466`), which is new scope; (3) a subgraph raises a permission
question — with whose authority does the inner workflow run? — that has no answer until roles
exist (W10, W14). Reconsider it after Phase 2. If no time remains, never is acceptable.

---

## 6. Part B1 — work items

### Invariants that no work item may weaken

| ID | Invariant | Where it lives |
|---|---|---|
| I1 | Each worker write runs in a transaction that first proves lease ownership | `storage.py:688-691` and each caller |
| I2 | Write intent is committed before the send; the request is checked before the marker | `worker.py:194-206`; `storage.py:814-818` |
| I3 | The write result and the marker removal are one commit | `storage.py:826-846` |
| I4 | Event, checkpoint, loop item, usage rows and accounting are one commit | `storage.py:703-738` |
| I5 | Saved token spend never goes down | `storage.py:625-638` |
| I6 | An approval is bound to a SHA-256 digest; the worker builds the payload again and compares | `approvals.py:54-55,148-149`; `worker.py:181-183` |
| I7 | An unsettled write blocks resume | `storage.py:862-864`; `worker.py:125` |
| I8 | A pause releases the lease in the same commit as its accounting | `approvals.py:59-96` |

"Standard gates" below means: full backend suite under `PYTHONHASHSEED` 0–4, the deployed scenario
gate (`--all`), the PostgreSQL checks in `ci.yml:126-130`, and a mutant set with an M00 control.

---

### W01 — Python tooling baseline (closes Q1, Q5; starts Q3)

- **Design.** Add `pyproject.toml` with ruff (format + lint) and mypy. Run `ruff format` as one
  commit with no logic change, proven by an AST comparison of each file before and after. Then
  lint fixes in separate commits. mypy in strict mode on the core only: `storage.py`,
  `worker.py`, `approvals.py`, `accounting_state.py`, `execution_policy.py`. Add `pytest-cov`.
  Split `requirements.txt` into runtime and dev files.
- **Rejected.** black + flake8 (two tools, slower); mypy on all files first (weeks of noise);
  reformat mixed with logic changes (cannot be reviewed).
- **Invariants.** All. The format commit must not change behavior.
- **Acceptance.** (1) A script compares `ast.dump` of every file at the parent commit and the
  format commit; all equal. (2) CI has `ruff format --check`, `ruff check`, `mypy` steps; each fails
  on the commit before its fix. (3) Coverage is reported in CI. (4) Standard gates.
- **Measurement.** Line and branch coverage of `backend/app`, total and for the five core files.
  mypy error count before and after.
- **Effort.** M. No dependency.
- **Resume line.** "Enforced ruff and strict mypy on the execution core in CI; [measured value]%
  branch coverage across [measured value] backend tests."

### W02 — Split the two large functions (closes Q2, Q3)

- **Design.** (a) Move `platform_resolver` out of `Worker._execute` into a class
  `RunPlatform(store, tools, kbs, memory, run, owner, principal)` with one method for each action
  (`tool`, `review`, `kb_retrieve`, ...). The write path (`worker.py:194-212`) becomes one method
  `send_write`, which is the single place that holds I2 and I3. (b) Replace the 24 parameters of
  `compile_workflow` with a `RunContext` dataclass. (c) Move the restore branch
  (`compiler.py:634-698`) and the success branch into two module-level functions. Do it in Phase 2,
  when the principal must pass through these functions anyway.
- **Rejected.** A rewrite of the compiler; a plugin system for actions.
- **Invariants.** I1–I8. The order "check, mark, send, settle" must be visibly the same.
- **Acceptance.** No test changes except imports. Standard gates. The existing mutants for the
  write path (TA and A5 sets) are still killed.
- **Measurement.** Longest function in `worker.py` and `compiler.py`, in lines, before and after.
- **Effort.** M. After W01.
- **Resume line.** None of its own; it supports W01's line.

### W03 — Truth pass and scope freeze (closes §2.2, Q4, X4, X5, E6)

- **Design.** Correct each row of §2.2 in the README. Put four numbers and one diagram on the first
  screen. Move the failure-mode narrative to `docs/design.md`. State the stack as "run live" and
  "adapter, tested with fixtures". Replace the roadmap with the plan in §8. Mark the request
  reader "experimental, fails its usability gate" and remove it from the palette. Do **not** delete
  the hidden legacy nodes or the request reader yet (see §9, point 2). Remove the two stray root
  files. Update `status-report.md` and `resume-evidence.md` for A6.1.
- **Acceptance.** A script `scripts/check_readme_claims.py` reads the counts in the README (tests,
  scenarios, CI jobs) and compares them with `pytest --collect-only`, the scenario list and
  `ci.yml`; it runs in CI and fails on the current README (`README.md:425`).
- **Measurement.** Count of unsupported claims: 11 now, 0 after.
- **Effort.** S. No dependency.
- **Resume line.** None.

### W04 — CI completeness (closes X3 and the A6.1 evidence gaps)

- **Design.** Run the deployed gate on each push to `main` and on pull requests (it takes under
  4 minutes locally). Add `postgres-repeat-sigkill` to the PostgreSQL job. Record the Part 3 review
  in `docs/reviews/`.
- **Acceptance.** A pull request that breaks S16 (mutant: skip `mark_write`) fails CI.
- **Measurement.** CI wall time for each job, before and after.
- **Effort.** S.
- **Resume line.** None.

### W05 — Load harness and baseline (closes P1; measures C2, C3, P2, P3)

- **Design.** New `scripts/loadtest/`: a deployed-process harness on PostgreSQL that starts 1 API
  process, N worker processes, and a model fixture with a set latency (0 ms, 200 ms, 2 s). Three
  fixed workflows: 3 nodes, 20 nodes, and a 100-item loop. Open-loop arrival at a set rate, and a
  closed-loop saturation mode. It counts SQL statements and commits with `pg_stat_statements` and
  `pg_stat_database`, and reads claim latency and queue wait from new timestamps on
  `execution_jobs` (`queued_at`, `claimed_at`). Results go to `evals/results/load-<date>/` as JSON
  plus one chart. **Run it first on the unchanged code** so that every later item has a
  before-number.
- **Rejected.** Locust or k6 against the HTTP API only (cannot see claim latency or SQL counts);
  SQLite (one writer; not the target).
- **Invariants.** The harness adds two timestamp columns only. No behavior change.
- **Acceptance.** The harness is deterministic in workload (fixed seed). It asserts correctness
  while under load: every run finishes once, the receiver gets exactly one request for each write,
  and no fenced write from a lost lease is accepted. Two runs on the same machine differ by less
  than a stated tolerance, which the report records.
- **Measurement.** Runs/min at 1, 4, 16 and 32 workers for each workflow and model latency; claim
  latency p50/p95/p99; queue wait p95; commits and SQL statements for each run; heartbeat share of
  all commits; database CPU. Hardware and PostgreSQL settings recorded.
- **Effort.** M. Needs Docker and PostgreSQL locally; the full matrix takes more than 5 minutes, so
  ask before a run.
- **Resume line.** "Built a deployed-process load harness for a leased SQL job queue; measured
  [measured value] runs/min at 32 workers with claim p95 of [measured value] ms on PostgreSQL 17."

### W06 — Database-clock leases and a lighter heartbeat (closes C1, C2; part of C4)

- **Design.** On PostgreSQL the claim, the fence and the heartbeat use the database clock:
  the lease is set and compared in SQL with the database's epoch
  (`extract(epoch from clock_timestamp())`). `lease_until` stays a float column, so there is no
  type migration. SQLite uses its own SQL clock, and the documents say that SQLite is one host
  only. Spec: `docs/specs/concurrency.md`. The heartbeat interval becomes lease/3 with ±10%
  jitter, in one statement: `UPDATE ... SET lease_until = ... WHERE <fence> RETURNING
  cancel_requested`. That removes the separate cancel read (`storage.py:698-701`) and the second
  update (`storage.py:696`). Cancel latency rises to at most lease/3; to keep cancel fast, the
  worker also listens on a PostgreSQL `NOTIFY` channel for cancels (optional step). The lease and
  the interval become settings. Move the remaining synchronous store calls in `_execute` to
  `asyncio.to_thread`.
- **Rejected.** NTP discipline as the answer (not provable); a monotonic fencing token alone (it
  does not fix early takeover); Redis leases (new dependency).
- **Invariants.** I1 exactly as now: the fence stays an `UPDATE` in the same transaction. The
  per-claim owner id stays.
- **Acceptance.** (1) New test: two stores whose Python clocks differ by ±60 s (injected clock)
  against one PostgreSQL; a live lease is never taken, and an expired lease is taken within one
  poll. It fails on the current code. (2) `postgres-stale-lease` still refuses the 5 stale
  operations. (3) S15, S16, S24, S28 pass with their recovery time equal to the lease. (4) Mutants:
  fence compares with the Python clock; heartbeat without the fence; `RETURNING` ignored.
- **Measurement.** Heartbeat commits/s for each active run, before and after (from W05). Runs/min
  at 16 and 32 workers, before and after. Cancel latency p95.
- **Effort.** S–M. After W05.
- **Resume line.** "Moved lease checks to the database clock and cut heartbeat writes from
  [measured value] to [measured value] commits/s per run; throughput at 32 workers rose from
  [measured value] to [measured value] runs/min."

### W07 — Claim path, poison runs, graceful stop (closes C3, C5, C6)

- **Design.** (a) Implement a second claim strategy on PostgreSQL: one statement,
  `UPDATE execution_jobs SET ... WHERE run_id = (SELECT run_id FROM execution_jobs WHERE <eligible>
  ORDER BY ... LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING run_id`. Keep compare-and-set behind a
  setting. Measure both with W05 and keep the winner; record the loser's numbers. (b) Add a partial
  index on `(created) WHERE status = 'queued'` and an index for expired leases. (c) Idle workers
  back off (100 ms to 2 s) and wake on `NOTIFY`. (d) Add `attempts` to `execution_jobs`, increased
  at each claim. When `attempts` exceeds a limit (default 5), the claim transaction fails the run
  with the cause `max_attempts` and does not execute it. A run with an unsettled write keeps
  today's rule (I7). (e) Handle `SIGTERM`: stop claiming, release the runs in flight (the existing
  path, `worker.py:264-269`), exit.
- **Rejected.** `SKIP LOCKED` without a measurement (the claim is the least understood part; the
  comparison is the interview answer); a separate dead-letter table (the failed run with a cause is
  the dead letter).
- **Invariants.** I1; one claim, one owner id; the claim still writes the `running` event in the
  same commit.
- **Acceptance.** (1) `postgres-four-workers` extended to 16 processes and 500 runs: 500 unique
  claims, no duplicated item row. (2) New check `postgres-poison-run`: a workflow whose worker
  exits at a failpoint on every attempt ends `failed` with `max_attempts` after exactly 5 claims;
  it fails (does not end) on the current code. (3) New check `sigterm-release`: `SIGTERM` during a
  model call; the run is claimed by a second worker in under 2 s, not 30 s. (4) Mutants: no
  attempt increment; limit off by one; `SKIP LOCKED` removed.
- **Measurement.** Claim p50/p95/p99 and wasted claim attempts for each strategy at 4, 16 and 32
  workers; idle polls/s for each worker; recovery time after `SIGTERM`.
- **Effort.** M. After W05, W06.
- **Resume line.** "Compared compare-and-set against `FOR UPDATE SKIP LOCKED` claiming under 32
  workers: claim p95 [measured value] ms vs [measured value] ms; added a bounded-attempt rule that
  stops a crashing run after [measured value] claims."

### W08 — Data integrity (closes D1, D2, D5)

- **Design.** One Alembic revision for each step, each with a tested downgrade:
  (a) `tenant_id` on `run_loop_items`, `run_action_results`, `run_token_usage`,
  `run_guard_decisions`, `run_admission_metrics`, `execution_jobs`, backfilled from `runs`.
  (b) `UNIQUE (id, tenant_id)` on `runs`, then composite foreign keys
  `(run_id, tenant_id) → runs (id, tenant_id) ON DELETE CASCADE` on every child table. The
  composite key makes "child tenant ≠ run tenant" impossible. On PostgreSQL add them `NOT VALID`,
  then `VALIDATE`. (c) `CHECK` constraints on `runs.status`, `execution_jobs.status`,
  `run_approvals.status`. This item does **not** touch the write-intent markers; that is W24.
- **Rejected.** Split the whole run document into tables now (too large; W20 moves checkpoints);
  foreign keys without the composite tenant key (allows cross-tenant children); JSONB conversion
  (no query needs it yet); moving the write-intent markers in the same change (W24 is alone, by
  rule).
- **Invariants.** I1–I8. No change to `mark_write`, `settle_write` or `check_resume_writes`.
- **Acceptance.** (1) `populated-downgrade` extended over the new revisions. (2) A test inserts a
  child row with another tenant's id and gets an integrity error. (3) Standard gates.
- **Measurement.** Count of foreign keys and check constraints (0 → [measured value]). Migration
  time on a database with [measured value] runs.
- **Effort.** M. Before W11 and W18.
- **Resume line.** "Added composite tenant foreign keys and state constraints across
  [measured value] tables with reversible Alembic migrations, verified by populated
  upgrade/downgrade checks on PostgreSQL."

### W24 — Write-intent table (closes the marker part of D3). Alone, by rule

- **Rule.** This item is never combined with another change. Its commits touch only the marker
  storage. No refactor, no formatting, no other schema change goes in the same pull request.
- **Design.** New table `run_write_intents (run_id, tenant_id, invocation, state CHECK IN
  ('marked','settled'), marked_at, settled_at, PRIMARY KEY (run_id, invocation))`. Three releases:
  (1) *double write*: `mark_write` inserts the row and still writes the JSON `write_nodes` list;
  `settle_write` and `approvals.begin`/`complete` update both, each in the same single commit as
  today; resume refuses when **either** store says unsettled. (2) *read the table only*, JSON still
  written. (3) *stop writing JSON*. The migration copies each existing JSON marker
  into the table as `marked` (an exact copy, not a guess). Release 3 is allowed only when no run
  has a JSON-only marker (a startup check refuses otherwise). Spec:
  `docs/specs/d3-write-intent-table.md`.
- **Rejected.** One-step cutover (a crash between old and new code can lose a marker); a backfill
  that guesses whether an old marker settled (the copy keeps every marker as `marked`, and the
  existing `_write_settled` rule still decides).
- **Invariants.** I2 (marker row committed before the send), I3 (settle and result are one
  commit), I7 (an unsettled write blocks resume). A disagreement between the two stores refuses
  resume; it never picks one.
- **Gate (all must pass at each of the three releases).** Deployed scenarios S03, S04, S16, S24.
  PostgreSQL checks `postgres-sigkill-loop`, `postgres-parallel-sigkill`,
  `postgres-repeat-sigkill`, `postgres-accounting-before-commit`,
  `postgres-accounting-after-commit`, `postgres-stale-lease`. The SQLite twins of the SIGKILL
  checks.
- 2026-11-17: aligned W06 and W24 with the specs in `docs/specs/` (float epoch on the database
  clock; exact copy of existing markers). Defects found by the hunt are in `docs/defects.md`. Full suite under seeds 0–4.
- **Acceptance.** New tests, written first: delete the JSON marker by hand → resume still refused
  because of the row; delete the row by hand → resume still refused because of JSON (release 1);
  kill between marker commit and send → the row is `marked` and resume is refused; settle → row is
  `settled` and the result row exists in the same commit (a failpoint between them proves it).
  Mutants with an M00 control: marker insert after the send; settle without the row update; resume
  reads one store only; `approvals.begin` skips the row.
- **Measurement.** None of performance. Count of write paths covered: [measured value] of
  [measured value].
- **Effort.** M. After W08 (needs `tenant_id` and the composite key). Before W17.
- **Resume line.** None of its own; it makes W17 possible.

### W23 — Remove `self_approval` from the scenarios (follows W12)

- **Why.** `self_approval = true` for migrated organizations (§7.5) is **temporary**. It exists
  only so that S01, S02, S05, S14 and S29, which approve with the account that started the run,
  keep passing while W12 lands.
- **Design.** (1) The scenario lab creates two accounts in one organization: an initiator
  (Operator) and an approver (Approver). (2) Rewrite S01, S02, S05, S14 and S29 so that the
  initiator starts the run and the approver decides. Each scenario adds one assert: the
  initiator's own decision returns 403 and sends nothing. (3) Remove every `self_approval` setting
  from the scenario setup; the lab organizations use the default. (4) After that, the migration
  default for existing organizations stays `true` only for real upgraded data; a new organization
  is `false`.
- **Invariants.** I6. The scenarios must keep every assert they have today (digest mismatch 409,
  duplicate approval no-op, changed destination blocked, restart repeats nothing, cached Repeat).
- **Acceptance.** (1) New test `test_new_org_self_approval_default_false`: a newly created
  organization has `self_approval = false`, and its initiator's approve returns 403. (2) A test
  greps the scenario sources and fails when `self_approval` appears. (3) S01, S02, S05, S14, S29
  pass with two accounts. (4) Mutant: remove the participant check in `decide()` → all five
  scenarios fail on the new assert.
- **Effort.** S–M. After W12.
- **Resume line.** None of its own; it makes the W12 line true in the deployed gate.

### W09–W16 — multi-user (design in §7)

| Item | Closes | Summary | Acceptance (short; details in §7.9) | Effort | Depends on |
|---|---|---|---|---|---|
| W09 Organizations, identity, tokens | S1, S5, S7 | Organizations, memberships, local passwords, OIDC, service-account tokens, active organization on the session, configurable origins | OIDC test against a local fake issuer (bad signature, wrong `aud`, wrong `nonce`, expired token all refused); token hash stored, secret shown once; a user in two organizations sees each separately | L | W08 |
| W10 Roles and the route matrix | S1, S3 (application level) | `Principal`, one `authorize()` function, a permission on each route, removal of every `tenant_id='local'` default | The generated role × route × organization matrix; a route with no rule fails CI | M–L | W09 |
| W11 Row-level security | S3 | PostgreSQL policies on every tenant table with `FORCE ROW LEVEL SECURITY`. The application role does not own the tables, is not a superuser and has no `BYPASSRLS`. `SET LOCAL` for each transaction | Every acceptance test **connects as the application role**. A test asserts the role's attributes (`rolsuper`, `rolbypassrls` false; not the owner of any tenant table) and that each tenant table has `relforcerowsecurity`. With the application filter removed (mutant), a cross-tenant read still returns no row | M | W08, W10 |
| W12 Approvals with separation of duties | S2 | Initiator on the run, decider on the approval, the rule in code and as a `CHECK` | The initiator's approve returns 403; a second user's approve succeeds; S01, S02, S05, S14 pass | M | W10 |
| W13 Audit log | S4 | Append-only, hash-chained table written in the same transaction as the change | `UPDATE` and `DELETE` on the table fail in PostgreSQL; the chain verifies; each sensitive route has a row | M | W10 |
| W14 Permission-aware retrieval | S8 | Grants on knowledge bases, a signed resolution from the management service, the run executes as its initiator | A user with no grant gets no passage, the same scores with or without the forbidden document, and cannot submit a workflow that reads the base | M | W10 |
| W15 Fair scheduling | C7, S10 | Per-organization concurrency limit and least-recently-served order in the claim | Starvation test: organization B's queue wait stays bounded while A has 1,000 queued runs | M | W07, W09 |
| W16 Secrets | S6 | Key id and `MultiFernet` rotation, credential rotate and delete with a reference check, signed knowledge RPC | A rotation re-encrypts all rows; the old key alone no longer decrypts; a credential in use by a saved workflow is refused deletion with the list | M | W10 |

Resume lines:

- W09–W11: "Designed multi-tenant isolation for an agent workflow platform: organizations, 6
  roles, OIDC and service tokens, with PostgreSQL row-level security as a second barrier; a
  generated [measured value]-case authorization matrix fails CI when a route has no rule."
- W12–W13: "Enforced separation of duties on external-write approvals in code and as a database
  constraint, with a hash-chained audit log of [measured value] action types."
- W14: "Moved retrieval permission into the search path, so restricted documents never enter
  ranking; verified with [measured value] isolation tests."
- W15: "Added per-organization fair scheduling to a SQL job queue: under a [measured value]-run
  backlog from one tenant, another tenant's p95 queue wait stayed at [measured value] s (FIFO:
  [measured value] s)."

### W17 — Reconcile path and idempotency key (closes O1)

- **Design.** (a) Send an `Idempotency-Key` header on HTTP writes, opt-in for each connection:
  `sha256(run_id : invocation)` — stable across retries of the same action, different for each
  action. (b) Reconcile API: `GET /api/reconcile` lists unsettled intents (from
  `run_write_intents`, W24) with the request preview. `POST /api/runs/{id}/writes/{invocation}/
  reconcile` with `{outcome: 'delivered' | 'not_delivered', evidence, digest}`. `delivered`
  records a result and settles the intent, so resume continues and never sends again.
  `not_delivered` clears the intent, and resume sends once. Both need the `runs:reconcile`
  permission, write an audit row, and are subject to the separation-of-duties rule. (c) One screen
  that lists them.
- **Rejected.** Automatic retry with the key (the key helps only when the remote honours it; the
  rule "writes are never retried" stays); a free-text database fix.
- **Invariants.** I2, I3, I7. Reconcile is the only new way to settle an intent, and it is a human
  decision with a record. The key must not make Relay retry.
- **Acceptance.** New scenarios: S30 (kill after accept → reconcile `delivered` → resume → the
  receiver still has 1 request); S31 (`not_delivered` → resume → 1 request in total); S32 (the
  receiver sees the same key on the uncertain attempt and on the resend). Mutants: reconcile
  without settle; key derived from the attempt.
- **Measurement.** Receiver request counts in S30–S32. Time from uncertain stop to resolved, in
  the scenario.
- **Effort.** M. After W24, W10, W13.
- **Resume line.** "Closed the uncertain-write loop with an audited reconcile step and stable
  idempotency keys; in kill-after-accept scenarios the receiver logged exactly [measured value]
  request per write."

### W18 — Retention and deletion (closes O2)

- **Design.** Retention setting for each organization (days for events, run payloads, approval
  payloads). A leased purge job deletes in batches by `(tenant_id, created_at)`, never a run that
  is active or has an unsettled intent. Run deletion and organization deletion use the cascades of
  W08. The audit log is kept and records the deletion. Session purge on a timer.
- **Rejected.** Table partitioning by time (right at larger scale; note it as the next step);
  soft delete (does not bound growth).
- **Invariants.** I7: an unsettled write is never purged.
- **Acceptance.** Tests: purge removes exactly the eligible rows; an uncertain run survives; after
  an organization deletion, a count over every tenant table for that id is 0 (the table list comes
  from the metadata, so a new table cannot be forgotten).
- **Measurement.** Rows and bytes removed for each batch; purge time for [measured value] runs;
  table size before and after.
- **Effort.** M. After W08.
- **Resume line.** "Added per-tenant retention and cascading deletion; a purge of
  [measured value] runs completed in [measured value] s without blocking active runs."

### W19 — Observability (closes O3; part of O4)

- **Design.** OpenTelemetry traces: one trace for each run, a span for each claim, node, model
  call, tool call and commit, with run id and organization id (no content, the same rule as the
  journal). Prometheus `/metrics`: queue depth and oldest age for each organization, claim latency
  histogram, lease expiries, heartbeat failures, fence refusals, uncertain writes, approvals
  pending, tokens by model. One Grafana dashboard as JSON. Make the run timeout a setting and add
  a per-node timeout.
- **Rejected.** A vendor SDK; logging prompt text into spans.
- **Acceptance.** A test runs one workflow with an in-memory exporter and asserts the span tree.
  A test scrapes `/metrics` after a forced lease expiry and sees the counter move. The W05 harness
  reads its numbers from `/metrics`, which proves the metrics against SQL counts.
- **Measurement.** Overhead: runs/min with tracing on and off.
- **Effort.** M.
- **Resume line.** "Instrumented the run lifecycle with OpenTelemetry and Prometheus (queue age,
  claim latency, fence refusals) at [measured value]% throughput overhead."

### W20 — Event stream and run-document writes (closes P2, P3; the checkpoint part of D3)

- **Design.** (a) Event stream: one query for each tick (events after the cursor, with the
  status), session check every 5 s, not every tick, and wake on `NOTIFY` in place of the 0.15 s
  poll on PostgreSQL. (b) Move checkpoints out of the run JSON into `run_checkpoints (run_id,
  tenant_id, node_id, outputs)`, written in the same commit as the event (I4). The run JSON then
  holds only small state. (c) Allocate `seq` from a counter column on the job row that the fence
  already locks. (d) Paginate events in `GET /api/runs/{id}`.
- **Rejected.** WebSockets (no gain over SSE here); Redis pub/sub (new dependency).
- **Invariants.** I4 is the risk: the checkpoint row, the event, the usage and the accounting stay
  one commit. I1 for the counter.
- **Acceptance.** `*-accounting-*-commit`, `*-sigkill-loop`, `*-parallel-sigkill`,
  `*-repeat-sigkill` pass. A populated migration test moves old JSON checkpoints and resumes a run
  across the upgrade. Mutants: checkpoint row in a second commit; seq without the lock.
- **Measurement.** Bytes written and statements for each run, for the 20-node and 100-node
  workflows, before and after; queries/s for each open stream, before and after; API p95 with 200
  open streams.
- **Effort.** M. After W05, W08.
- **Resume line.** "Cut SQL bytes written per 100-node run from [measured value] to
  [measured value] and event-stream load from [measured value] to [measured value] queries/s per
  client."

### W21 — Evaluation that an interviewer accepts (closes E1, E2, E4; part of P5, E5)

- **Design.** (a) A new frozen set, labelled by a person before any run: at least 30 answerable
  and 30 unanswerable questions over documents not used before, with a short labelling protocol
  and a second labeller on a sample for agreement. Thresholds committed before the first run. The
  set is never used for tuning. (b) Report counts with Wilson 95% intervals. Note: with n = 30 an
  interval is wide (for example 25 of 30 gives about 0.66–0.93 by the formula), so the README must
  show intervals, and 60 + 60 is better if labelling time allows. (c) One hosted model through the
  old and the new set, with tokens and cost from `run_token_usage` and a price table. (d) An
  OpenAI-compatible `base_url` setting on the OpenAI provider, which also covers vLLM; no
  streaming in this item. (e) CI: a deterministic check that re-scores the saved outputs and fails
  on a threshold breach or on a changed question file hash; the live run stays manual or nightly,
  because it needs keys and money.
- **Rejected.** An LLM judge as the main score without a calibration set (the NLI attempt shows
  why); tuning on the new set.
- **Invariants.** None of I1–I8. The evaluation rule: thresholds before the run.
- **Acceptance.** The commit history shows labels and thresholds before the first result file.
  The CI check fails when a result file is edited to pass.
- **Measurement.** Abstention, false abstention, fact match, citation rate as counts with
  intervals, guard on and off, for the local model and the hosted model; tokens and cost for each
  question; labeller agreement (Cohen's kappa) on the sample.
- **Effort.** M, plus labelling time. Needs API keys and paid calls: ask first.
- **Resume line.** "Evaluated a pre-generation answerability guard on a held-out, human-labelled
  set (n = [measured value]): correct abstention [measured value] (95% CI [measured value]) at
  [measured value] false abstention, on [measured value] models."

### W22 — Deploy and demo (closes X1, X2; rest of P5, S7)

- **Design.** One multi-stage image for the Python services and one for the editor; a
  `compose.yaml` with PostgreSQL, API, 2 workers, knowledge services, a fixture receiver, a local
  OIDC issuer, Prometheus and Grafana. One small cloud VM with TLS from a reverse proxy that
  forwards the client IP. The public instance uses fixture receivers only and a hosted model with a
  hard token limit for each organization. A 3-minute video: build a workflow, kill a worker, see
  the resume, approve as a second user, read the audit row and the trace. A public design document.
- **Rejected.** Kubernetes (no signal for this scope, much cost); a public instance with real
  write tools.
- **Acceptance.** `docker compose up` on a clean machine reaches `/api/ready` = ready, and the
  deployed gate passes against the compose stack. CI builds the images.
- **Measurement.** Cold start time; image sizes.
- **Effort.** M.
- **Resume line.** Supports all others (a link a reviewer can open).

---

## 7. Part B2 — multi-user design

### 7.1 Model

The organization id **is** the existing `tenant_id` value. The column name stays `tenant_id`. A
rename across 10 tables adds risk and no value. Each current account's tenant becomes an
organization with the same id, so no run, event or credential row is rewritten.

```
organizations (id PK, name, slug UNIQUE, self_approval BOOL DEFAULT false,
               max_concurrent_runs INT, retention_days INT, created_at, deleted_at)
users         (id PK, email UNIQUE, password_hash NULL, oidc_issuer NULL, oidc_subject NULL,
               disabled_at, UNIQUE (oidc_issuer, oidc_subject))      -- from auth_accounts
memberships   (tenant_id FK, user_id FK, role CHECK IN (...), created_at,
               PRIMARY KEY (tenant_id, user_id))
service_accounts (id PK, tenant_id FK, name, role CHECK IN (...), created_by FK, disabled_at)
api_tokens    (id PK, service_account_id FK, prefix, token_hash UNIQUE, scopes JSON,
               expires_at NOT NULL, last_used_at, revoked_at)
auth_sessions (+ active_tenant_id, + idle_expires_at)
kb_grants     (tenant_id, kb_id, role_or_principal, permission CHECK IN ('search','manage'))
audit_log     (see 7.6)
```

Rule: every organization has at least one Owner (checked in the transaction that changes a
membership).

### 7.2 Roles and permissions

Six fixed roles. No custom roles: a fixed set can be tested completely. The code checks
permissions, never role names; the table below is the one source, and the documentation is
generated from it.

| Permission | Owner | Admin | Builder | Operator | Approver | Viewer |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| Organization: delete, transfer, set `self_approval` | ✔ | | | | | |
| Members, service accounts, API tokens: manage | ✔ | ✔ | | | | |
| Audit log: read | ✔ | ✔ | | | | |
| Model allowlist: change | ✔ | ✔ | | | | |
| Model allowlist: read | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Credentials and connections: create, rotate, delete | ✔ | ✔ | | | | |
| Credentials and connections: use by reference in a workflow | ✔ | ✔ | ✔ | | | |
| Credentials: read the secret | | | | | | |
| Workflows: create, edit, delete | ✔ | ✔ | ✔ | | | |
| Workflows: read | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Runs: start, resume, cancel | ✔ | ✔ | ✔ | ✔ | | |
| Runs: read (events, outputs) | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| Runs: reconcile an uncertain write | ✔ | ✔ | | ✔ | | |
| Approvals: decide (never on a run the principal took part in) | ✔ | ✔ | | | ✔ | |
| Knowledge bases: create, ingest, rebuild, delete | ✔ | ✔ | ✔ | | | |
| Knowledge bases: search | by grant; default all members | | | | | |
| Organization metrics: read | ✔ | ✔ | | ✔ | | |

Platform operator (cross-organization metrics, `auth.py:144-153`) becomes a separate flag on the
user, set only from the command line. It is not a role in an organization.

### 7.3 Authentication

- **Local passwords** for development: the existing PBKDF2 code and login budgets, enabled by a
  setting. Add password change (which revokes all other sessions) and "sign out everywhere".
- **OIDC**: authorization code flow with PKCE, `state` and `nonce`; ID token checked for signature
  (JWKS, with key rotation), `iss`, `aud`, `exp`, `nonce`; `email_verified` required. The identity
  key is `(iss, sub)`, never the email. A new user joins an organization only through an invite
  or a verified domain mapping set by an Owner. Use a maintained library (`authlib`); do not write
  JWT validation by hand.
- **Sessions**: opaque cookie as now, plus an active organization, an idle timeout and an absolute
  timeout, and rotation at sign-in.
- **Service-account tokens**: format `relay_<prefix>_<secret>`, 256 bits of entropy, stored as a
  SHA-256 hash (a slow hash is not needed for a random secret), shown once, with a required expiry
  (maximum 365 days), scopes that are a subset of the account's role, `last_used_at`, and
  revocation. Sent as `Authorization: Bearer`. Bearer requests skip the origin check, because they
  carry no cookie. A service account cannot decide approvals.
- **Configuration**: allowed origins, trusted hosts and the trusted proxy come from settings
  (closes S7).

### 7.4 Tenant isolation

**Application level.** Replace the `tenant_id='local'` default on every `Store` method with a
required scope. `store.scoped(principal)` returns a facade whose methods take no tenant argument,
so a call with no scope is a type error that mypy reports (W01). The worker gets its scope from the
claimed run, as now (`storage.py:447-450`).

**Database level (PostgreSQL).**

- Three roles: `relay_owner` (migrations only; owns the tables), `relay_app` (API) and
  `relay_worker`. The two application roles **must not own any table, must not be superusers and
  must not have `BYPASSRLS`**: PostgreSQL skips row-level security for superusers and, without
  `FORCE`, for the table owner. Startup checks these three facts and refuses to start in
  multi-user mode when one is false.
- Every table with `tenant_id`: `ENABLE` **and** `FORCE ROW LEVEL SECURITY` (so that even the
  owner role is subject to the policy), with the policy
  `USING (tenant_id = current_setting('relay.tenant_id', true)) WITH CHECK (same)`.
- Each transaction starts with `SET LOCAL relay.tenant_id = ...`, set by a SQLAlchemy
  `after_begin` listener from the scope. `SET LOCAL` ends with the transaction, so pooled
  connections cannot leak a tenant. With no setting, the policy matches no row: it fails closed.
- The claim must see all organizations. `execution_jobs` gets a policy for `relay_worker` that
  permits the claim columns only; the table holds no payload. After the claim, the worker sets
  `relay.tenant_id` to the run's organization for every later transaction, and the fence is
  unchanged (I1).
- The composite foreign keys of W08 stop a child row from pointing at another organization's run.
- The knowledge-service databases get the same policies.
- SQLite has no row-level security. Position: SQLite is for single-user development; multi-user
  deployments need PostgreSQL, and startup refuses multi-user mode on SQLite.

Rejected: a schema for each tenant (migrations × tenants, pool pressure); a database for each
tenant (operations cost); application filters only (the current state).

### 7.5 Approvals and separation of duties

- `runs` gains `initiated_by_type`, `initiated_by_id`. `run_participants (run_id, principal_id,
  action)` records the initiator and each principal who resumed the run.
- `run_approvals` gains `initiator_id` (copied at pause), `decided_by_type`, `decided_by_id`,
  `decided_at`.
- Rule in `decide()`: the principal has `approvals:decide`, is a human, and is not a participant
  of the run. The digest check, the expiry and the state machine do not change (I6).
- The same rule as a constraint: `CHECK (decided_by_id IS NULL OR decided_by_id <> initiator_id
  OR self_approval_at_decision)`. The flag is copied from the organization at decision time, so
  the row explains itself.
- `organizations.self_approval`: `false` for new organizations. `true` for organizations migrated
  from single-user data. **This is temporary.** It keeps the current behavior and scenarios S01,
  S02, S05, S14 and S29 while W12 lands. W23 then rewrites those five scenarios with separate
  initiator and approver accounts, adds a test that a new organization defaults to `false`, and
  removes the flag from the scenario setup. Only an Owner can change the flag, and the change is
  audited.
- New: an approval inbox, `GET /api/approvals?status=pending`. Today approvals appear only inside
  a run (`storage.py:491-493`).
- The rate limit moves from the tenant to the principal, with an organization ceiling.
- Not included: "the workflow author cannot approve". It is a stricter policy; record it as a
  known limit.

### 7.6 Audit log

```
audit_log (tenant_id, seq BIGINT, at timestamptz DEFAULT clock_timestamp(),
           actor_type, actor_id, action, resource_type, resource_id,
           outcome, request_id, ip, detail JSONB, prev_hash, hash,
           PRIMARY KEY (tenant_id, seq))
```

- Written **in the same transaction** as the change, so a change cannot exist without its row.
- Append-only: `relay_app` and `relay_worker` have `INSERT` and `SELECT` only, and a trigger
  raises on `UPDATE` and `DELETE`.
- Hash chain for each organization: `hash = sha256(prev_hash || canonical row)`. A verify command
  walks the chain. This makes tampering by the owner role visible; it does not prevent it.
- Actions: sign-in success and failure, membership and role changes, token create and revoke,
  credential create, rotate and delete, model allowlist changes, workflow save and delete, run
  start, cancel and resume, approval decisions, reconcile decisions, knowledge-base changes and
  grants, retention changes, deletion, denied authorization.
- No content: ids and digests only, the same rule as the journal (`observability.py:18`).

### 7.7 Retrieval

- The management service owns the grants. `resolve` takes the principal and returns the permitted
  document ids for that principal.
- The management service **signs** the resolution (Ed25519; search holds only the public key):
  `{tenant, principal, kb_id, segment_id, version, document_ids_hash, exp ≤ 60 s}`. The search
  service accepts a search only with a valid signature that matches the request. Today it accepts
  any tenant and any document list from a caller that has the shared key (`kb/rpc.py:78,86-87`).
- The filter stays a **pre-filter** inside the search query (`kb/search.py:229`), so a forbidden
  chunk never enters ranking, the BM25 statistics (`kb/search.py:244-249`) are computed over the
  permitted set only, and `top_k` is not reduced after ranking.
- A run executes with its **initiator's** permissions, checked when the retrieval runs, not when
  the run was submitted. A grant revoked during a run takes effect, which agrees with the existing
  rule that a source must be valid at publication (`README.md:348`). Submission and save refuse a
  workflow that names a knowledge base or credential the principal cannot use.
- The evidence registry and the Response check use the same principal.

### 7.8 Fair scheduling

- `execution_jobs` has `tenant_id` (W08). The claim picks the oldest eligible job of the
  organization that was **served least recently** and is below its concurrency limit:

```sql
SELECT j.run_id FROM execution_jobs j
JOIN organizations o ON o.id = j.tenant_id
WHERE <eligible>
  AND (SELECT count(*) FROM execution_jobs r
       WHERE r.tenant_id = j.tenant_id AND r.status = 'running'
         AND r.lease_until > clock_timestamp()) < o.max_concurrent_runs
ORDER BY o.last_served_at NULLS FIRST, j.created
LIMIT 1 FOR UPDATE OF j SKIP LOCKED
```

- `last_served_at` is updated in the claim transaction. The running count is computed, not kept
  in a counter: a counter drifts after a crash.
- Submission has a per-organization limit on queued runs; over the limit returns 429.
- Rejected: Redis (the old roadmap, `README.md:467`) — not needed at this scale and it adds a
  second source of truth; weighted fair queuing with virtual time — correct, but more than this
  needs.

### 7.9 Authorization test strategy

1. **One rule function.** `authorize(principal, permission, resource)`. Each route declares
   `Depends(require('<permission>'))`.
2. **No route without a rule.** A test walks `app.routes`. Each route must carry a permission or
   be in an explicit public list (health, ready, auth status, login, register, OIDC callback).
   A new route with neither fails CI.
3. **Generated matrix.** A checked-in file gives the expected status for each
   route × role × {own organization, other organization, no session, service token}. A
   parametrized test calls each cell against a real application with two seeded organizations. A
   route that is not in the file fails. The permission table in §7.2 is generated from the same
   file.
4. **No table without a policy.** A test walks the SQLAlchemy metadata. Each table must have
   `tenant_id` with a policy, or be in an explicit global list (`users`, `organizations`,
   `alembic_version`, `schema_migrations`, `auth_login_budgets`, `worker_heartbeats`).
5. **The second barrier is real.** These tests open their own connection **as `relay_app`** (and
   again as `relay_worker`), never as the owner or a superuser; a test run that connects as a
   superuser proves nothing, so the fixture asserts `current_user`, `rolsuper = false`,
   `rolbypassrls = false`, and that the role owns no tenant table. With organization A set, a
   `SELECT`, `UPDATE` and `DELETE` on each tenant table for organization B affects 0 rows, and an
   `INSERT` with organization B's id is refused. With no setting, also 0. A catalog test asserts
   `relrowsecurity` and `relforcerowsecurity` for every tenant table.
6. **Mutants.** M00 control. Remove the application filter in `Store.run`: the matrix fails, and
   test 5 still passes. Disable a policy: test 5 fails. Remove the participant check in
   `decide()`: the separation test fails, and the `CHECK` still refuses the row.
7. **Deployed scenarios.** S33: two organizations, cross-reads return 404. S34: the initiator's
   approve returns 403 and a second user's approve sends once. S35: a Builder with no grant cannot
   submit a workflow that reads a restricted base. S36: organization A's backlog does not starve
   organization B.

### 7.10 Migration from the single-user schema

Expand, move, contract. Each step is one revision with a tested downgrade and a populated
round-trip check.

| Revision | Change |
|---|---|
| 0007 | Create `organizations`, `memberships`. For each distinct `auth_accounts.tenant_id`: an organization with that id and `self_approval = true`, and an Owner membership. The `'local'` tenant (authentication off) becomes the organization `local` |
| 0008 | `auth_accounts` → `users` (new nullable OIDC columns); `service_accounts`, `api_tokens`; session columns |
| 0009 | W08: `tenant_id` on child tables, composite foreign keys, check constraints |
| 0009a | W24 only: `run_write_intents` (its own revision, its own pull request) |
| 0010 | `runs.initiated_by_*`, `run_participants`, approval actor columns. Old runs get the organization Owner with `initiator_inferred = true`, so the history does not pretend |
| 0011 | `audit_log` and its trigger |
| 0012 | Row-level security policies and grants (PostgreSQL only). Role creation is a documented deploy step, because a migration must not need superuser |
| 0013 | Contract: drop `auth_accounts.tenant_id` reads. (The JSON `write_nodes` list is removed by W24 release 3, not here) |

The first-account data move (`auth.py:209-216`) becomes "the first account owns the `local`
organization". The knowledge-service databases come under Alembic in the same phase (closes D4
for them).

---

## 8. Part B3 — plan (8 weeks)

Each phase ends with CI green, the deployed gate passing, and the PostgreSQL checks passing.

| Phase | Weeks | Work items, in order | State at the end |
|---|---|---|---|
| 0. Truth and baseline | 1 | W03, W04, W01, W05 (baseline run on unchanged code) | Honest README; lint, types and coverage in CI; the gate in CI; the first performance numbers exist |
| 1. Durability under load | 2 | W06, W07, W20 (a) and (c) | Database-clock leases; measured claim strategy; poison-run rule; before and after numbers |
| 2. Multi-user | 3–5 | W08, W24 (alone), W09, W10 (with W02), W12, W23, W13, W11, W14, W15, W16 | Organizations, roles, OIDC, tokens, row-level security, separation of duties, audit, permission-aware retrieval, fair queue. S33–S36 pass. Load harness rerun to show the cost of isolation |
| 3. Operability | 6 | W17, W18, W19, W20 (b) and (d) | Reconcile, idempotency key, retention, deletion, traces, metrics |
| 4. Evaluation | 7 | W21 | Held-out human-labelled result with intervals; one hosted model; a CI check |
| 5. Deploy and demo | 8 | W22, final README and design document, resume evidence | A link, a video, a design document |

If time runs short, cut in this order: W16 rotation (keep delete), W20 (b), W15 limits (keep the
order rule), the Grafana dashboard, the second labeller.

**Cut or freeze from the old roadmap:**

| Item | Decision | Reason |
|---|---|---|
| A6.2 subgraphs | Later; decide after Phase 2 | Closes no gap; needs versions and a permission model |
| A5.3b writes in branches | Freeze | A new runtime; the current refusal is a defensible design |
| Strict token cap | Freeze | The admission limit is documented and honest |
| Request reader | Freeze as experimental; out of the palette | Fails its gate; do not tune on the old set |
| B2 Redis | Cut | PostgreSQL does leases, fairness and limits; one source of truth |
| B3 object storage | Cut | No interview question needs it |
| B5 SCIM | Cut | OIDC is enough signal |
| C1 data-source SPI, D Text-to-SQL, E GraphRAG | Cut | Breadth |
| Elasticsearch, Pinecone | Keep the code; remove from the headline stack | Never run live |
| Jira, Confluence, GitHub, SMTP live runs | Freeze | Fixtures are enough for the write protocol |

---

## 9. Part B4 — comparison with the other reviewer's plan

**Agreement.** Lint, types and coverage; README corrections; organizations, six roles, separation
of duties, permission-aware retrieval, row-level security, OIDC, audit log, fair queue, generated
authorization matrix; load harness; heartbeat at lease/3; database-clock leases; the
compare-and-set against `SKIP LOCKED` comparison; held-out human-labelled set with Wilson
intervals; one hosted model; OpenTelemetry and Prometheus; retention and deletion; reconcile and
`Idempotency-Key`; images, deployment with fixture receivers, video, design document; and almost
all of the cut list.

**Disagreement.**

1. **Order of the load harness.** The other plan measures in Phase 2, after multi-user. Measure in
   Phase 0, on unchanged code. Without a baseline, the clock, heartbeat and claim changes have no
   "before", and the cost of row-level security and audit rows cannot be shown. The fixes are also
   small, so they come before the large multi-user change, not after it.
2. **Removing the hidden legacy nodes and the request reader in Phase 0.** Do not. The contract-2
   entry rule requires `Input → Request reader` or `Greeting cache → Request router`
   (`compiler.py:420-422`), the Switch requires contract 2 (`compiler.py:394-396`), and the Switch
   trust rule uses the verifier set (`compiler.py:444-447,505-521`). Removal would break A5.2 and
   scenarios S20–S21. The demo provider of the `llm` node is used by the example workflow
   (`examples/ai-workflow.json:47`). Freeze and hide now; remove only with a design for the
   contract-2 entry.
3. **Six weeks of scope in the multi-user phase without its preconditions.** Row-level security
   needs `tenant_id` on every child table (D5), and separation of duties needs an actor on the run
   (S2). The other plan lists the features but not these schema steps or a migration plan.
4. **vLLM with streaming.** The job history already proves vLLM. A configurable OpenAI-compatible
   base URL is a small change and is enough. Streaming through a checkpointing engine is a real
   design problem (token deltas must be transient events that are never checkpointed or counted),
   and it closes no gap in §3 except perceived latency. Do it last or cut it.
5. **Evaluation as a CI gate.** A live hosted-model run in CI needs keys and money and is not
   repeatable. Gate CI on re-scoring saved outputs and on file hashes; run the live evaluation by
   hand or nightly.
6. **30 + 30 questions.** Accept as the minimum, but show the intervals: they are wide at n = 30.
7. **Cloud deployment.** One VM with compose. Not a cluster.

**What the other plan misses.**

- The poison run with no attempt limit (C5).
- Data integrity: foreign keys, state constraints, the write-intent table (D1–D3).
- No actor on runs and approvals as its own gap (S2); the approval inbox.
- Service-account tokens.
- The `self_approval` setting, without which a one-person organization and the existing scenarios
  S01, S02, S05, S14, S29 stop working.
- The SQLite position: row-level security exists only on PostgreSQL.
- The knowledge services: outside Alembic (D4), a caller-stated tenant with a shared key (S6), a
  global lock on every call (D6).
- The key model: one Fernet key, no rotation, no credential deletion (S6).
- Event-stream polling cost (P2) and run-document rewrites (P3, D3).
- Synchronous database calls on the event loop (C4) and no `SIGTERM` handling (C6).
- CI gaps: the deployed gate is manual, and the Repeat crash checks are not in CI (X3).
- A prompt-injection test set (S9). It is also not in this plan's eight weeks; it is the first
  item after them.

---

## 10. Traceability

| Gap | Work item | Acceptance test | Resume line |
|---|---|---|---|
| Q1, Q5 | W01 | AST-equality script on the format commit; ruff, mypy, coverage steps in CI | Ruff and strict mypy on the core; [measured value]% branch coverage |
| Q2, Q3 | W02 | Unchanged tests; existing write-path mutants still killed | (supports W01) |
| §2.2, Q4, X4, X5, E6 | W03 | `check_readme_claims.py` in CI | — |
| X3 | W04 | A pull request that breaks S16 fails CI | — |
| P1 | W05 | Correctness asserts under load; repeatability within stated tolerance | [measured value] runs/min at 32 workers; claim p95 [measured value] ms |
| C1, C2, C4 | W06 | ±60 s skew test on PostgreSQL; `postgres-stale-lease`; S15, S16, S24, S28 | Heartbeat [measured value] → [measured value] commits/s per run |
| C3, C5, C6 | W07 | 16-process, 500-run claim check; `postgres-poison-run`; `sigterm-release` | Claim p95 [measured value] vs [measured value] ms; stop after [measured value] claims |
| D3 (markers) | W24 | Double-store resume tests; S03, S04, S16, S24; PostgreSQL SIGKILL checks at each release | — |
| S2 (scenarios) | W23 | New-organization default `false`; S01, S02, S05, S14, S29 with two accounts; no `self_approval` in scenario sources | — |
| D1, D2, D5 | W08 | Cross-tenant child insert refused; populated round trip | Composite tenant keys on [measured value] tables |
| S1, S5, S7 | W09 | OIDC negative tests against a local issuer; token tests | (with W10, W11) |
| S1, S3 | W10 | Route walk; generated role × route × organization matrix | [measured value]-case authorization matrix |
| S3 | W11 | As the application role (not owner, not superuser, `FORCE` on): cross-tenant SQL affects 0 rows; catalog and role-attribute asserts; filter-removal mutant | Row-level security as a second barrier |
| S2 | W12 | S34: initiator 403, second user sends once; `CHECK` refuses | Separation of duties in code and as a constraint |
| S4 | W13 | `UPDATE`/`DELETE` refused; chain verify; a row for each sensitive route | Hash-chained audit log, [measured value] action types |
| S8 | W14 | S35; score-equality test with and without a forbidden document | Permission inside the search path; [measured value] isolation tests |
| C7, S10 | W15 | S36 starvation test | p95 queue wait [measured value] s vs FIFO [measured value] s |
| S6 | W16 | Rotation re-encrypts; old key fails; in-use delete refused | — |
| O1 | W17 | S30, S31, S32 | Exactly [measured value] request per write after reconcile |
| O2 | W18 | Purge exactness; uncertain run kept; organization delete leaves 0 rows | Purge of [measured value] runs in [measured value] s |
| O3, O4 | W19 | Span-tree test; metric moves on forced lease expiry | [measured value]% tracing overhead |
| P2, P3, D3 | W20 | Crash checks at the commit; populated checkpoint migration | Bytes per run [measured value] → [measured value]; stream queries/s [measured value] → [measured value] |
| E1, E2, E4 | W21 | Labels and thresholds committed before results; CI re-score check | Held-out n = [measured value]; abstention [measured value] (95% CI [measured value]) |
| X1, X2, P5 | W22 | Clean-machine `compose up`; gate passes against the stack | (the link) |
| D4, D6, P4, S9, E3, E5, O5 | Not in the 8 weeks | — | — |

Gaps left open on purpose: D4 for the core startup migrations, D6 and P4 (knowledge-service
storage and latency), S9 (prompt injection), E3 and E5 (judge validity, agent-task evaluation), O5
(backup test). State them in the README as known limits.

---

## 11. Status of work items

| Item | Status |
|---|---|
| W01–W24 | Not started. Implementation starts only on "implement <phase>" |

Changelog:

- 2026-11-16: created at `24e659c`.
- 2026-11-16: amendments. (a) `self_approval = true` for migrated organizations is temporary; new
  item W23. (b) Row-level security: the application role is not the owner and not a superuser,
  `FORCE ROW LEVEL SECURITY`, tests connect as the application role. (c) The write-intent marker
  move is its own item W24, never combined, gated by S03, S04, S16, S24 and the PostgreSQL SIGKILL
  checks.
- 2026-11-17: aligned W06 and W24 with the specs in `docs/specs/` (float epoch on the database
  clock; exact copy of existing markers). Defects found by the hunt are in `docs/defects.md`.
