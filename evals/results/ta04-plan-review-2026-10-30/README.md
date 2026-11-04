# TA04 plan-review evidence

Baseline: `51ba164`. Date: 2026-10-30. Application and original plan/evidence files unchanged.

- `original-agent.txt`: independent rerun of the author's original read-only probe.
- `probe_loop_resume.py` / `loop-resume.txt`: current loop replay with a narrow shim for
  TA04's proposed budget outcome; demonstrates an extra retrieval/action after resume.
- `probe_agent_repair.py` / `agent-repair.txt`: current Agent with narrow shims for the
  proposed typed refusal and repair re-raise; demonstrates failure outside the planning
  loop when an evidenced Agent cannot afford repair.

The two new probes are **plan counterexamples**, not a test suite for an implemented fix.
All network responses are synthetic. No real Relay run or external write was created.
Run either script with `.venv/bin/python -I <file> "$PWD"` from the repository root.
Exit 0 means the asserted counterexample was observed.

Preserved input SHA256 values:

```text
4f2b159f0ce3ee28bdf07cf50694ac891105caf21e63f1465792e2fbf8f402ec  docs/superpowers/plans/2026-10-30-typed-token-budget-refusal.md
30b03b920bb1dff4cae9e815327bf55471e0d69ae9d23e0fccddf530757c4320  evals/results/ta04-plan-2026-10-30/agent_token_probe.py
ccdf57d36116be09b8250e15f9c84a403ba6e7f4bd39dc1bf8ebdb30dcd8fb60  evals/results/ta04-plan-2026-10-30/agent_token_probe.txt
```
