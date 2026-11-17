# Load baseline

**This is a relative baseline for later changes on the same machine. It is not a capacity claim.** One laptop ran the API, the workers, the model stub and (for PostgreSQL) the database container.

- Commit: `beab0b1029ce8cf31b8cfb28b4aff92a9d0fd9d3` (last change to `backend/app`: `24e659c23193746342d3a267186f095fa62927c3`); dirty: False
- Machine: macOS-26.3.1-arm64-arm-64bit-Mach-O, 10 CPUs, 16 GB; Python 3.14.6
- SQLite 3.53.3; PostgreSQL 17.11 (postgres:17-alpine, defaults, max_connections 500)
- Relay settings (fixed in the code): {'slots_per_worker': 4, 'lease_seconds': 30, 'heartbeat_seconds': 0.2, 'idle_poll_seconds': 0.1, 'run_timeout_seconds': 120}
- Mode: closed-queue drain: all runs are queued before the workers start
- Command: `/Users/soumith/Library/Mobile Documents/iCloud~md~obsidian/Documents/Soumith/No Code Agentic AI/scripts/load/run.py --db sqlite,postgres --workers 1,4,16 --workflow w20 --latency-ms 0 --runs 200 --repeats 3 --timeout 300 --out evals/results/load-baseline-2026-11-16/w20-0ms`

Columns: runs/min is the median of the repeats, with the minimum and maximum. Drain time runs from the first claim to the last finish. Claim-to-finish is for one run. Commits and updated rows are database-wide deltas divided by completed runs (PostgreSQL only); they include heartbeats, idle polls and the harness's one status query a second.

## Workflow `w20`

| Database | Latency ms | Workers × slots | Runs | Runs/min (min–max) | Drain s | Claim-to-finish p50 / p95 s | Commits/run | Rows updated/run | Re-claims | Flags |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| postgres | 0 | 1 × 4 | 200 | 248.4 (239.3–249.0) | 48.31 | 0.803 / 1.090 | 48.0 | 77.0 | 0 | — |
| postgres | 0 | 4 × 4 | 200 | 640.7 (588.9–699.7) | 18.73 | 1.209 / 1.841 | 50.6 | 79.2 | 0 | noisy |
| postgres | 0 | 16 × 4 | 200 | 758.5 (693.6–955.2) | 15.82 | 4.307 / 5.652 | 59.2 | 91.0 | 0 | noisy |
| sqlite | 0 | 1 × 4 | 200 | 279.3 (261.0–282.3) | 42.96 | 0.721 / 1.202 | unmeasured | unmeasured | 0 | — |
| sqlite | 0 | 4 × 4 | 200 | 456.3 (431.8–462.7) | 26.30 | 1.646 / 4.012 | unmeasured | unmeasured | 0 | — |
| sqlite | 0 | 16 × 4 | 200 | 408.6 (396.3–425.2) | 29.37 | 7.990 / 15.047 | unmeasured | unmeasured | 0 | — |

## What this does not show

- Latency below saturation (open-loop arrival): unmeasured.
- Claim latency as its own number: unmeasured (no claim timestamp column exists; queue wait in a drain is the drain order).
- Behavior on several hosts, with a real model, with tools, loops, approvals or the event stream: unmeasured.
- SQL statement counts on SQLite: unmeasured.
- Throughput with tuned PostgreSQL settings or a database on its own host: unmeasured.
