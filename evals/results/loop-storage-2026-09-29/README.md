# Loop storage measurements, 2026-09-29

`storage_probe.py` runs one loop through a real `Worker` against SQLite and records, per
store, the final stored bytes and the total bytes written. "Written" is the size of the
string/bytes parameters bound to every INSERT/UPDATE on that table: what the database was
asked to store, not on-disk pages. `AGENT_RUN_BUDGET=200` so every item can run.

- `before*.json`: commit `bc62b0b`, progress and accounting inside the run document.
- `after*.json`: this change (item rows, accounting column, event previews, 1 MB
  earliest-values budget).
- `*.json`: each item returns 60,000 bytes (under the 64,000-byte tool cap).
- `*-1kb.json`: each item returns 1,000 bytes, so no value is dropped and per-item
  scaling is visible.

Rerun from the repository root:

    AGENT_RUN_BUDGET=200 PYTHONPATH=. .venv/bin/python evals/results/loop-storage-2026-09-29/storage_probe.py OUT.json [VALUE_BYTES]

Limits: SQLite only; one worker; no concurrent runs; bytes are bound parameters, not
write-ahead-log or page amplification.
