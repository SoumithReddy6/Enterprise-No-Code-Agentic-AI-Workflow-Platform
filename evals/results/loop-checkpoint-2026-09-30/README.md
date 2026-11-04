# Compact loop checkpoint measurements, 2026-09-30

Same probe as `evals/results/loop-storage-2026-09-29/` (method and limits described
there): one loop run through a real `Worker` on SQLite, `AGENT_RUN_BUDGET=200`.

- `before-*.json`: commit `11f8773`, loop results embedded in the checkpoint and in the
  loop's success event.
- `after-*.json`: this change, a compact `{stored_in, count, sha256}` marker in both.
- `*-60000.json` / `*-1000.json`: bytes returned per item.

With the change, the final `runs` row and the bytes written to `runs` are the same for
60,000-byte and 1,000-byte values (37 KB and 0.26 MB at 100 items): they no longer depend
on retained result size. What still grows with the item count is the upstream Jira
checkpoint (the loop's input list, capped at 64,000 bytes by the tool). Retained values
are written once, to `run_loop_items`.

Rerun from the repository root:

    AGENT_RUN_BUDGET=200 PYTHONPATH=. .venv/bin/python evals/results/loop-checkpoint-2026-09-30/storage_probe.py OUT.json VALUE_BYTES
