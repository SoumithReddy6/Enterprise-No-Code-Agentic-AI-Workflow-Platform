# Revised TA04 plan audit

Baseline: `51ba164`. No application or original plan/evidence changes.

- `probe_plan_edges.py`: current-code grounded/structured branch observation, plus a model
  of the revised compaction/replay reason rule.
- `prepare_agent_candidate.py`: applies only the proposed Agent early returns and typed
  repair re-raises to a **disposable git-archive export**. It refuses a repository with `.git`.
- `probe_agent_candidate.py`: six scenarios with real `llm_node` and synthetic HTTP, testing
  valid final output, source removal at both fallback sites and strict structured output.
- `preserve_candidate_guards.py`: demonstrates source verification and the minimal strict
  schema typed-failure policy in that disposable candidate only.
- `probe_storage_marker.py`: temporary SQLite marker round trip, stale-owner fencing and
  row/event rollback on failed commit.
- `*.txt`: retained successful probe output; exit 0 means the expected observation held.
- `preserved-inputs.json`: SHA256 of the revised plan and original audit evidence before work.

Reproduction from the repository root (choose a fresh candidate directory):

```bash
candidate=$(mktemp -d /private/tmp/relay-ta04-audit-XXXXXXXX)
git archive 51ba164 | tar -x -C "$candidate"
.venv/bin/python evals/results/ta04-revised-plan-review-2026-10-30/prepare_agent_candidate.py "$candidate"
.venv/bin/python -I evals/results/ta04-revised-plan-review-2026-10-30/probe_agent_candidate.py "$candidate"
.venv/bin/python evals/results/ta04-revised-plan-review-2026-10-30/preserve_candidate_guards.py "$candidate"
.venv/bin/python -I evals/results/ta04-revised-plan-review-2026-10-30/probe_agent_candidate.py "$candidate" --guarded
.venv/bin/python -I evals/results/ta04-revised-plan-review-2026-10-30/probe_plan_edges.py "$PWD"
.venv/bin/python -I evals/results/ta04-revised-plan-review-2026-10-30/probe_storage_marker.py "$PWD"
```

The candidate is incomplete by design: it is not an implementation of TA04's shared policy
or durable loop behavior. These observations identify plan requirements. The guarded
strict-schema behavior is a recommendation to record explicitly, not an implied user decision.
