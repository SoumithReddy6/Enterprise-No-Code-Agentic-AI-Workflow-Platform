# Load baseline

**This is a relative baseline for later changes on the same machine. It is not a capacity claim.** One laptop ran the API, the workers, the model stub and (for PostgreSQL) the database container.

- Commit: `beab0b1029ce8cf31b8cfb28b4aff92a9d0fd9d3` (last change to `backend/app`: `24e659c23193746342d3a267186f095fa62927c3`); dirty: False
- Machine: macOS-26.3.1-arm64-arm-64bit-Mach-O, 10 CPUs, 16 GB; Python 3.14.6
- SQLite 3.53.3; PostgreSQL 17.11 (postgres:17-alpine, defaults, max_connections 500)
- Relay settings (fixed in the code): {'slots_per_worker': 4, 'lease_seconds': 30, 'heartbeat_seconds': 0.2, 'idle_poll_seconds': 0.1, 'run_timeout_seconds': 120}
- Mode: closed-queue drain: all runs are queued before the workers start
- Command: `/Users/soumith/Library/Mobile Documents/iCloud~md~obsidian/Documents/Soumith/No Code Agentic AI/scripts/load/run.py --db sqlite,postgres --workers 1,4,16 --workflow w3,w20 --latency-ms 200 --runs 200 --repeats 1 --timeout 300 --out evals/results/load-baseline-2026-11-16/latency-200ms`

Columns: runs/min is the median of the repeats, with the minimum and maximum. Drain time runs from the first claim to the last finish. Claim-to-finish is for one run. Commits and updated rows are database-wide deltas divided by completed runs (PostgreSQL only); they include heartbeats, idle polls and the harness's one status query a second.

## Workflow `w20`

| Database | Latency ms | Workers × slots | Runs | Runs/min (min–max) | Drain s | Claim-to-finish p50 / p95 s | Commits/run | Rows updated/run | Re-claims | Flags |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| postgres | 200 | 1 × 4 | 200 | 221.2 (221.2–221.2) | 54.25 | 0.965 / 1.346 | 49.9 | 77.9 | 0 | — |
| postgres | 200 | 4 × 4 | 200 | 681.2 (681.2–681.2) | 17.62 | 1.201 / 1.733 | 51.0 | 79.7 | 0 | — |
| postgres | 200 | 16 × 4 | 200 | 536.1 (536.1–536.1) | 22.38 | 6.192 / 8.511 | 61.9 | 96.2 | 0 | — |
| sqlite | 200 | 1 × 4 | 200 | 277.8 (277.8–277.8) | 43.19 | 0.763 / 1.104 | unmeasured | unmeasured | 0 | — |
| sqlite | 200 | 4 × 4 | 200 | 465.9 (465.9–465.9) | 25.76 | 1.574 / 3.575 | unmeasured | unmeasured | 0 | — |
| sqlite | 200 | 16 × 4 | 200 | 395.4 (395.4–395.4) | 30.35 | 7.592 / 15.745 | unmeasured | unmeasured | 0 | — |

## Workflow `w3`

| Database | Latency ms | Workers × slots | Runs | Runs/min (min–max) | Drain s | Claim-to-finish p50 / p95 s | Commits/run | Rows updated/run | Re-claims | Flags |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| postgres | 200 | 1 × 4 | 200 | 591.6 (591.6–591.6) | 20.28 | 0.341 / 0.421 | 12.2 | 25.3 | 0 | — |
| postgres | 200 | 4 × 4 | 200 | 1,913.3 (1,913.3–1,913.3) | 6.27 | 0.397 / 0.569 | 11.6 | 25.2 | 0 | — |
| postgres | 200 | 16 × 4 | 200 | 1,726.1 (1,726.1–1,726.1) | 6.95 | 1.113 / 2.074 | 14.2 | 27.2 | 0 | — |
| sqlite | 200 | 1 × 4 | 200 | 656.3 (656.3–656.3) | 18.29 | 0.289 / 0.344 | unmeasured | unmeasured | 0 | — |
| sqlite | 200 | 4 × 4 | 200 | 1,628.5 (1,628.5–1,628.5) | 7.37 | 0.334 / 0.908 | unmeasured | unmeasured | 0 | — |
| sqlite | 200 | 16 × 4 | 200 | 1,652.7 (1,652.7–1,652.7) | 7.26 | 1.125 / 4.069 | unmeasured | unmeasured | 0 | — |

## What this does not show

- Latency below saturation (open-loop arrival): unmeasured.
- Claim latency as its own number: unmeasured (no claim timestamp column exists; queue wait in a drain is the drain order).
- Behavior on several hosts, with a real model, with tools, loops, approvals or the event stream: unmeasured.
- SQL statement counts on SQLite: unmeasured.
- Throughput with tuned PostgreSQL settings or a database on its own host: unmeasured.
