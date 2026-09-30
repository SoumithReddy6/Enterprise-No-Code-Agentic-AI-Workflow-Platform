# Nested agent event storage, 2026-09-29

`nested_probe.py` runs a loop whose body is an agent through a real `Worker` against
SQLite. In every item the agent calls one read tool and then answers; the model and tool
transport are fixtures. It reports stored `run_events` bytes, the largest stored event,
and how many nested tool events carry the loop identity (`loop_node_id`, `item_index`).

- `before.json`: commit `ad98dd5`, tool results of 40,000 bytes.
- `after.json`: this change, tool results of 40,000 bytes.
- `after-4kb.json`: this change, tool results of 4,000 bytes. Per-item storage is the
  same as with 40,000-byte results: it no longer depends on tool output size.

The largest stored event in the "after" runs is top-level, not loop work: the Jira read
(bounded by its 64,000-byte projection), the loop's own running event (its input list)
and its success event (its results, bounded by the retained-value budget). Loop-context
events stay within the preview bound.

Rerun from the repository root (40,000 is the default and stays under the agent's
60,000-character request limit):

    AGENT_RUN_BUDGET=200 PYTHONPATH=. .venv/bin/python evals/results/nested-agent-events-2026-09-29/nested_probe.py OUT.json [VALUE_BYTES]

Limits: SQLite, one worker, one tool call per item; bytes are stored payload lengths.
