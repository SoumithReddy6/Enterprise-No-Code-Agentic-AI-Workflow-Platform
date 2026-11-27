# Load baseline

**This is a relative baseline for later changes on the same machine. It is not a capacity claim.** One laptop ran the API, the workers, the model stub and (for PostgreSQL) the database container.

- Commit: `beab0b1029ce8cf31b8cfb28b4aff92a9d0fd9d3` (last change to `backend/app`: `24e659c23193746342d3a267186f095fa62927c3`); dirty: False
- Machine: macOS-26.3.1-arm64-arm-64bit-Mach-O, 10 CPUs, 16 GB; Python 3.14.6
- SQLite 3.53.3; PostgreSQL 17.11 (postgres:17-alpine, defaults, max_connections 500)
- Relay settings (fixed in the code): {'slots_per_worker': 4, 'lease_seconds': 30, 'heartbeat_seconds': 0.2, 'idle_poll_seconds': 0.1, 'run_timeout_seconds': 120}
- Mode: closed-queue drain: all runs are queued before the workers start
- Command: `/Users/soumith/Library/Mobile Documents/iCloud~md~obsidian/Documents/Soumith/No Code Agentic AI/scripts/load/run.py --db sqlite,postgres --workers 1,4,16 --workflow w3 --latency-ms 0 --runs 600 --repeats 3 --timeout 300 --out evals/results/load-baseline-2026-11-16/w3-0ms`

Columns: runs/min is the median of the repeats, with the minimum and maximum. Drain time runs from the first claim to the last finish. Claim-to-finish is for one run. Commits and updated rows are database-wide deltas divided by completed runs (PostgreSQL only); they include heartbeats, idle polls and the harness's one status query a second.

## Workflow `w3`

| Database | Latency ms | Workers × slots | Runs | Runs/min (min–max) | Drain s | Claim-to-finish p50 / p95 s | Commits/run | Rows updated/run | Re-claims | Flags |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| postgres | 0 | 1 × 4 | 600 | 1,116.6 (1,069.5–1,149.7) | 32.24 | 0.158 / 0.224 | 10.5 | 23.1 | 0 | — |
| postgres | 0 | 4 × 4 | 600 | 2,455.1 (2,410.9–2,487.5) | 14.66 | 0.216 / 0.530 | 10.8 | 23.6 | 0 | — |
| postgres | 0 | 16 × 4 | 600 | 2,112.0 (2,023.8–2,427.3) | 17.05 | 0.769 / 1.721 | 12.6 | 25.1 | 0 | noisy |
| sqlite | 0 | 1 × 4 | 600 | 1,143.6 (1,140.7–1,163.3) | 31.48 | 0.108 / 0.233 | unmeasured | unmeasured | 0 | — |
| sqlite | 0 | 4 × 4 | 600 | 1,572.7 (1,509.7–1,595.8) | 22.89 | 0.219 / 1.540 | unmeasured | unmeasured | 0 | — |
| sqlite | 0 | 16 × 4 | 600 | 1,592.4 (1,561.5–1,624.2) | 22.61 | 1.289 / 4.902 | unmeasured | unmeasured | 0 | — |

## What this does not show

- Latency below saturation (open-loop arrival): unmeasured.
- Claim latency as its own number: unmeasured (no claim timestamp column exists; queue wait in a drain is the drain order).
- Behavior on several hosts, with a real model, with tools, loops, approvals or the event stream: unmeasured.
- SQL statement counts on SQLite: unmeasured.
- Throughput with tuned PostgreSQL settings or a database on its own host: unmeasured.
