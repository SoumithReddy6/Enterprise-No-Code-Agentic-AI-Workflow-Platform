# Spec: load baseline harness (audit W05; gap P1)

Status: ready to implement. Written 2026-11-16 against source `24e659c`.
Phase A needs **no source change** and gives the baseline. Phase B adds two columns and more
metrics, after the baseline is saved.

## 1. Current behavior

| Topic | Evidence |
|---|---|
| Throughput, claim latency, queue wait, SQL writes for each run | Unmeasured (`docs/status-report.md:247-254`) |
| Highest tested concurrency | 4 worker processes with concurrency 1, 50 runs, no duration recorded (`scripts/check_milestone_storage.py:109-132`) |
| Process launcher that exists | `python -m scripts.scenarios.lab api|worker --port N` starts the real API or worker in an isolated data directory (`scripts/scenarios/lab.py:12-43`); the gate's `Lab` class starts them and registers the first account (`scripts/check_production_scenarios.py:99-123`) |
| Model stub that exists | An Ollama-compatible HTTP fixture, selected with `OLLAMA_BASE_URL` (`scripts/check_production_scenarios.py:393,408`; `scripts/scenarios/repeat_loops.py:33-71`) |
| Timestamps that exist | `runs.created_at` (`backend/app/storage.py:477`); the claim's `running` run event with its timestamp (`storage.py:680`, `:539-540`); `runs.data['finished_at']` (`storage.py:757`); each node event's timestamp |
| Fixed settings | 4 slots for each worker process (`backend/app/worker.py:58`); lease 30 s (`storage.py:669`); heartbeat every 0.2 s (`worker.py:247`); idle poll 0.1 s (`worker.py:300`); run timeout 120 s (`worker.py:19`) |

## 2. Target design

### 2.1 Files

```
scripts/load/__init__.py
scripts/load/fixtures.py     -- model stub and write receiver (HTTP servers in the harness process)
scripts/load/workflows.py    -- the fixed workflows
scripts/load/stack.py        -- start and stop API and worker processes, register, allow the model
scripts/load/measure.py      -- read timings and database counters, compute statistics
scripts/load/run.py          -- command line; one cell = one (database, workers, workflow, latency)
scripts/load/report.py       -- results.json -> summary.md (one table for each workflow)
```

No file under `backend/app` changes in phase A. The processes are started through
`scripts.scenarios.lab`, so the code under test is the shipped API and worker.

### 2.2 Workflows (fixed; version them by name, never edit one in place)

| Name | Shape | Model calls | Engine work |
|---|---|---|---|
| `w3` | Input → Agent (stub model, no tools) → Response | 1 | 3 nodes, 3 checkpoints |
| `w20` | Input → Agent → 17 Prompt nodes in a chain → Response | 1 | 20 nodes, 20 checkpoints, each rewriting the run document (`storage.py:736-737`) |
| `loop50` | Input → HTTP GET that returns a 50-item JSON list → For each (body: HTTP GET to the receiver) → Response | 0 | 50 item rows, 50 item commits, 51 reads |

The Agent uses provider `ollama`, model `load-stub`, `max_steps` 1, and the stub answers a final
text of 200 characters with a reported usage of 100 prompt and 50 completion tokens. `loop50` is
used in place of a 100-item loop because the default action budget is 40 and its maximum is 200
(`backend/app/agent_runtime.py:217-226`): the harness sets `AGENT_RUN_BUDGET=200` for the workers
and records it.

### 2.3 Stub behavior

- Model stub: `POST /api/chat` sleeps `latency_ms`, then answers. It also answers the model-list
  and capability calls that the API makes (`backend/app/providers.py:284-343`). It counts calls by
  run (it cannot see the run id; it counts the total).
- Receiver: `GET /items` returns the list; `GET /item` returns `{}` after `latency_ms`; it counts
  requests. Connections use the hostname `scenario-provider.invalid`, which the lab entry point
  maps to loopback for the fixture port only (`scripts/scenarios/lab.py:17-22`).
- Latency levels: `0` ms (engine overhead only) and `200` ms (a fast hosted model).

### 2.4 One cell

1. Make a new data directory and, for PostgreSQL, a new **database** in a throwaway container
   (never the user's database; never a schema inside it, because the counters of 2.5 are for each
   database).
2. Start the API. Register the account. Allow the model. Create the connection.
3. **Submit all N runs while no worker runs.** N = 200 for `w3` and `w20`, 40 for `loop50`.
   Record the submit wall time separately; it is not part of the drain.
4. Read the database counters (before).
5. Start W worker processes at the same instant (`W` in 1, 4, 16; 32 only on PostgreSQL). Each
   has 4 slots.
6. Wait until every run is `success`, `failed` or `cancelled`, or until the cell timeout
   (10 minutes). Record the wall time from step 5 to the last `finished_at`.
7. Read the counters (after). Stop the workers with `SIGTERM`, then the API.
8. Check correctness (2.6). Write the cell's rows.

This is a closed-queue drain: it measures the maximum rate at which W workers empty a full
queue. An open-loop arrival test is phase B.

### 2.5 What is measured, and how

| Metric | Method | Limits to state in the report |
|---|---|---|
| Throughput, runs/min | `completed / drain_seconds * 60` | Drain of a full queue; includes worker start-up |
| Queue wait, p50/p95/p99, s | For each run: timestamp of its first `running` run event − `created_at` | Dominated by the drain order in this mode; reported, not a latency claim |
| Claim-to-finish, p50/p95/p99, s | `finished_at` − first `running` event | Includes model latency |
| Engine overhead for each run, s | Claim-to-finish − (model calls × latency) | Only for `w3` and `w20` |
| Re-claims | Count of `running` run events − number of runs | Above 0 means a lease expired or a worker lost a run |
| Commits for each run (PostgreSQL) | Δ `xact_commit` of `pg_stat_database` for the cell's database ÷ completed runs | Includes heartbeat, polling and the harness's own status polls; the harness polls at most once a second with one query and reports its own count |
| Rows written for each run (PostgreSQL) | Δ `tup_inserted`, Δ `tup_updated` ÷ completed runs | |
| Statement mix (PostgreSQL, optional) | `pg_stat_statements` top 15 by calls, when the extension is loaded | The throwaway container is started with `-c shared_preload_libraries=pg_stat_statements` |
| Idle load | A second short cell: W workers, empty queue, 30 s: Δ `xact_commit` ÷ 30 ÷ W | Polling and worker heartbeat only |
| Database size (SQLite) | File size of the database and its journal, before and after | No statement counters on SQLite |
| Failures | Counts by run status and by error text; worker log lines that contain `database is locked` or `poll_failure` | A cell with failures is reported with its numbers and marked `degraded` |

Percentiles: nearest-rank on the sorted values. With N = 200, p99 is the 198th value; say so.

Each cell runs **3 times**. Report the median and the minimum–maximum of throughput. A spread
over 15% marks the cell `noisy`.

### 2.6 Correctness under load (asserted in every cell)

- Every run ends `success`. A failed run is reported; the cell is `degraded`.
- Model stub calls = runs × calls for each run (no call repeated, none missing), when re-claims = 0.
- Receiver item requests = runs × 50 for `loop50`.
- No run has two `success` run events.
- `git status --porcelain -- backend/app` is empty and the commit hash is recorded.

### 2.7 Output

`evals/results/load-baseline-<YYYY-MM-DD>/`:

- `results.json`: `{"commit", "dirty": false, "started_at", "machine": {"platform", "cpu_count", "memory_gb", "python"}, "database": {"kind", "version", "settings"}, "relay_settings": {...}, "cells": [{"database", "workers", "slots", "workflow", "latency_ms", "runs", "repeat", "submit_seconds", "drain_seconds", "runs_per_min", "queue_wait": {"p50","p95","p99"}, "claim_to_finish": {...}, "reclaims", "commits_per_run", "rows_inserted_per_run", "rows_updated_per_run", "harness_queries", "statuses": {...}, "errors": {...}, "flags": []}]}`
- `summary.md`: conditions, one table for each workflow, the limits of 2.5, and a section "What
  this does not show".
- `logs/` for each cell: API and worker logs.
- `README.md`: the exact command.

### 2.8 Phase B (after the baseline; small source change, its own card)

- Columns `execution_jobs.queued_at`, `claimed_at` on the database clock, so claim latency does
  not depend on event timestamps.
- Open-loop arrival at a set rate (Poisson, fixed seed) for 5 minutes, for latency under a load
  below saturation.
- An event-stream cell: K open `GET /api/runs/{id}/events` clients, API p95 for a status call.
- `SIGKILL` of one worker in the middle of a cell: every run still ends once.

## 3. Rejected alternatives

| Alternative | Why not |
|---|---|
| Locust or k6 against the HTTP API | They see request latency, not claim latency, re-claims or SQL counts; the system is queue-driven |
| In-process workers (threads in the harness) | Not the deployed shape; hides process start-up and connection pools |
| A real model | Adds variance that hides the engine; costs money; the stub's latency is the variable |
| Use the user's PostgreSQL container | The counters are for each database, and the user's data must not be touched |
| A temporary schema in a shared database | `pg_stat_database` cannot separate schemas |
| A 100-item loop | Over the default action budget; `loop50` with the budget setting recorded is honest |
| Report one run of each cell | No measure of noise |

## 4. Invariants that must not break

Phase A changes no source file, so I1–I8 cannot change. The harness itself must obey:

- It never points at the user's `.data/` directory or database. `stack.py` refuses a data
  directory without the marker file `SCENARIO_LAB_ONLY` (the lab entry point also refuses,
  `lab.py:16`) and refuses a PostgreSQL database whose name does not start with `relay_load_`.
- It never estimates. A metric that could not be read is `null` in JSON and "unmeasured" in the
  summary.
- It records the commit hash and refuses to run when `backend/app` has uncommitted changes
  (override flag `--allow-dirty` marks every cell `dirty`).

## 5. Change list, in commit order

| # | Commit | Content |
|---|---|---|
| 1 | Tests first | `backend/tests/test_load_harness.py`: H01–H08 of §6 (red: the modules do not exist) |
| 2 | `fixtures.py`, `workflows.py` | The three workflows validate against the real validator (`compiler.validate_workflow`) |
| 3 | `stack.py` | Start, register, allow model, connection, stop; the two refusals of §4 |
| 4 | `measure.py` | Timings from the API (`GET /api/runs/{id}`), counters from PostgreSQL, statistics |
| 5 | `run.py`, `report.py` | The cell loop, 3 repeats, JSON and summary |
| 6 | Smoke cell in CI (optional) | One cell: SQLite, 1 worker, `w3`, 0 ms, 10 runs, as a test of the harness, not a measurement |
| 7 | Baseline run and evidence | `evals/results/load-baseline-<date>/` on the unchanged source commit |

## 6. Acceptance tests (`backend/tests/test_load_harness.py`)

| Id | Test name | Asserts |
|---|---|---|
| H01 | `test_the_three_workflows_are_valid` | `validate_workflow` returns no error for `w3`, `w20`, `loop50` (with a connection id placeholder replaced) |
| H02 | `test_w20_has_twenty_nodes_and_one_model_call` | Node count 20; exactly one Agent |
| H03 | `test_percentiles_use_nearest_rank` | For 1…200: p50 = 100, p95 = 190, p99 = 198 |
| H04 | `test_stack_refuses_a_directory_without_the_marker` | `RuntimeError` |
| H05 | `test_stack_refuses_a_database_not_named_for_load` | `RuntimeError` for a URL whose database is `relay` |
| H06 | `test_a_cell_with_a_failed_run_is_flagged_degraded` | Given a fake cell result with one failed run, `flags` contains `degraded` |
| H07 | `test_a_missing_counter_is_null_not_zero` | With no PostgreSQL counters, `commits_per_run` is `None` and the summary prints "unmeasured" |
| H08 | `test_smoke_cell_drains_ten_runs` | One real cell (SQLite, 1 worker, `w3`, 0 ms, 10 runs): 10 `success`, 10 model calls, 0 re-claims, `runs_per_min > 0`. Marked slow; under 60 s |

The harness is accepted for a baseline when two full runs of the matrix on the same machine give
throughput medians within 15% of each other for every cell that is not `noisy`. Record both.

## 7. Conditions that must be recorded with every result

Commit hash; machine model, CPU count, memory; operating system; Python version; database kind
and version; PostgreSQL settings that differ from the image defaults; whether the database is in a
container; worker count and slots; `AGENT_RUN_BUDGET`, lease and timeout values; stub latency;
runs for each cell; the date. Throughput on a laptop with the database in a container is a
**relative** baseline for later changes on the same machine. It is not a capacity claim. The
summary must say this in its first paragraph.

## 8. Failure-mode analysis

| Failure | Handling |
|---|---|
| A cell does not drain in 10 minutes | Stop it; record the completed count and `flags: ['timeout']`; continue with the next cell |
| SQLite lock errors with many workers | Expected at some W; record the counts; the cell is `degraded`, not skipped |
| The stub becomes the bottleneck | It is a threaded server; at 0 ms a call costs well under a millisecond. The summary reports the stub's own maximum rate from a self-test (1,000 calls) so that a reader can compare |
| The harness's polling changes the result | One query a second; its count is reported and can be subtracted |
| Worker start-up time | Included in the drain; with N = 200 it is a small part. Report the time from worker start to the first claim |
| Another process loads the machine | Three repeats and the `noisy` flag |
| A process is left running after an error | `stack.py` stops every child in a `finally`; a second run finds free ports because ports are chosen new each time |

## 9. Rollback

Phase A adds files under `scripts/load/`, one test file and one results directory. Remove them.
Nothing else depends on them.

## 10. Done when

- H01–H08 pass.
- `evals/results/load-baseline-<date>/` holds `results.json`, `summary.md`, logs and the command,
  for the unchanged source commit.
- `docs/resume-evidence.md` has one new row with the measured throughput and its conditions.
- `docs/gap-audit.md` §11 marks W05 done.
