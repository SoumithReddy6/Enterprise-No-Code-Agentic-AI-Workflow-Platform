# Load baseline, 2026-11-16

First load measurement of Relay. Source under test: `backend/app` at commit `24e659c` (unchanged;
the harness commit is `beab0b1`). Harness: `scripts/load/`. Spec: `docs/specs/load-baseline-harness.md`.

**This is a relative baseline for later changes on the same machine. It is not a capacity claim.**
One laptop (Apple M5, 10 CPUs, 16 GB, macOS) ran the API, all worker processes, the model stub and,
for PostgreSQL, the database container (postgres:17-alpine, image defaults, `max_connections=500`).
With 16 worker processes the machine has more processes than CPUs.

## Method

Closed-queue drain. All runs are queued through the real API while no worker runs. Then W worker
processes start (4 slots each, fixed at `backend/app/worker.py:58`). Time runs from the first
claim to the last finish. The model is a local stub with a set latency. Each 0 ms cell ran 3
times; each 200 ms cell ran once. Every run in every cell ended `success`, with no re-claim and
with the expected number of model calls.

Workflows: `w3` = Input → Agent (1 model call) → Response. `w20` = Input → Agent → 17 Prompt nodes
→ Response (20 nodes, 1 model call).

## Results (medians; full tables in each `summary.md`)

| Workflow | Model latency | Database | 1 worker | 4 workers | 16 workers | Unit |
|---|---:|---|---:|---:|---:|---|
| w3 (600 runs) | 0 ms | PostgreSQL | 1,116.6 | 2,455.1 | 2,112.0 (noisy) | runs/min |
| w3 (600 runs) | 0 ms | SQLite | 1,143.6 | 1,572.7 | 1,592.4 | runs/min |
| w20 (200 runs) | 0 ms | PostgreSQL | 248.4 | 640.7 (noisy) | 758.5 (noisy) | runs/min |
| w20 (200 runs) | 0 ms | SQLite | 279.3 | 456.3 | 408.6 | runs/min |
| w3 (200 runs, 1 repeat) | 200 ms | PostgreSQL | 591.6 | 1,913.3 | 1,726.1 | runs/min |
| w3 (200 runs, 1 repeat) | 200 ms | SQLite | 656.3 | 1,628.5 | 1,652.7 | runs/min |
| w20 (200 runs, 1 repeat) | 200 ms | PostgreSQL | 221.2 | 681.2 | 536.1 | runs/min |
| w20 (200 runs, 1 repeat) | 200 ms | SQLite | 277.8 | 465.9 | 395.4 | runs/min |

PostgreSQL, 0 ms, database-wide counters divided by completed runs:

| Workflow | Workers | Commits/run | Rows updated/run | Claim-to-finish p50 / p95, s |
|---|---:|---:|---:|---:|
| w3 | 1 | 10.5 | 23.1 | 0.158 / 0.224 |
| w3 | 4 | 10.8 | 23.6 | 0.216 / 0.530 |
| w3 | 16 | 12.6 | 25.1 | 0.769 / 1.721 |
| w20 | 1 | 48.0 | 77.0 | 0.803 / 1.090 |
| w20 | 4 | 50.6 | 79.2 | 1.209 / 1.841 |
| w20 | 16 | 59.2 | 91.0 | 4.307 / 5.652 |

## What the numbers show

- Throughput rises from 1 to 4 worker processes (PostgreSQL: ×2.2 for `w3`, ×2.6 for `w20`) and
  does **not** rise in proportion from 4 to 16 (PostgreSQL `w3`: 2,455 → 2,112 runs/min; SQLite is
  flat or lower). The time for one run rises at the same time (`w3` p50 0.216 s → 0.769 s; `w20`
  1.2 s → 4.3 s).
- The cause of the flat scaling is **not determined** by this run. Candidates: more processes than
  CPUs on one machine; contention in the claim (`backend/app/storage.py:669-686`); the single
  SQLite writer. A run with the database on its own host, and the claim comparison of
  `docs/specs/concurrency.md`, are needed to separate them.
- A 3-node run costs about 10.5 commits and 23 row updates; a 20-node run about 48 commits and 77
  row updates (about 2.4 commits and 3.9 row updates for each node). Commits for each run rise
  with the worker count (10.5 → 12.6; 48.0 → 59.2).
- With a 200 ms model call and one worker (4 slots), `w3` reaches 592–656 runs/min. Four slots
  that only waited 200 ms for each run would give 1,200 runs/min, so about half of each run's
  time at this setting is not the model call.

## Not measured

Open-loop latency below saturation; claim latency as its own number; the `loop50` workflow and the
idle-load cell of the spec; 32 workers; several hosts; a real model; tools, approvals, the event
stream; statement counts on SQLite; tuned PostgreSQL settings.

## Files

- `w3-0ms/`, `w20-0ms/`, `latency-200ms/`: `results.json` (every cell and repeat) and `summary.md`.
- Process logs for each cell were written under each `logs/` directory. They are not committed
  (58 MB).

## Commands

```bash
.venv/bin/python -m scripts.load.run --db sqlite,postgres --workers 1,4,16 --workflow w3 --latency-ms 0 --runs 600 --repeats 3 --timeout 300 --out evals/results/load-baseline-2026-11-16/w3-0ms
.venv/bin/python -m scripts.load.run --db sqlite,postgres --workers 1,4,16 --workflow w20 --latency-ms 0 --runs 200 --repeats 3 --timeout 300 --out evals/results/load-baseline-2026-11-16/w20-0ms
.venv/bin/python -m scripts.load.run --db sqlite,postgres --workers 1,4,16 --workflow w3,w20 --latency-ms 200 --runs 200 --repeats 1 --timeout 300 --out evals/results/load-baseline-2026-11-16/latency-200ms
```
