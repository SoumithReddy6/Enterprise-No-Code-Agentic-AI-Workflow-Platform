# W21 — Held-out evaluation and one hosted model

| | |
|---|---|
| Order | 28 of 29 (Phase 4) |
| Closes | E1, E2, E4 |
| Depends on | nothing (labelling time) |
| Effort | M + labelling |
| Spec | `docs/gap-audit.md` W21. |

## Goal

A held-out, human-labelled grounding set with intervals; one hosted model through the gate; a CI check that re-scores saved outputs.

## Files to touch

- `evals/heldout/` (new): `protocol.md`, `questions.json` (≥ 30 answerable, ≥ 30 unanswerable, new documents), `labels.json` (labeller, date, second-labeller sample), `thresholds.json`
- `scripts/check_grounding_eval.py` (Wilson intervals; question-file hash)
- `backend/app/providers.py:397-448` (`OPENAI_BASE_URL` setting for OpenAI-compatible servers; no streaming)
- `backend/app/operator_metrics.py` (a price table read from a JSON file; cost = tokens × price)
- `.github/workflows/ci.yml` (re-score step)

## Tests to write first

| Test / check | Asserts |
|---|---|
| Git history | The commit with labels and thresholds is an ancestor of the first result file's commit (`scripts/check_eval_order.py`, run in CI) |
| `test_wilson_interval_known_values` | 25 of 30 → about [0.664, 0.927]; 0 of 30; 30 of 30 |
| `test_rescore_fails_when_a_result_file_is_edited_to_pass` | |
| `test_rescore_fails_when_the_question_file_hash_changes` | |
| `test_openai_base_url_setting_is_used_and_egress_rules_still_apply` | |
| `test_cost_uses_reported_tokens_and_marks_unreported_calls` | Unknown is not zero (`README.md:225`) |

## Commands to verify

```bash
# 1. New tests fail on the parent commit (show the output), then pass.
# 2. Full backend suite under five hash seeds:
for s in 0 1 2 3 4; do PYTHONHASHSEED=$s .venv/bin/python -m pytest backend/tests -q || break; done
# 3. Deployed scenario gate (about 5 minutes, local only):
.venv/bin/python -u -m scripts.check_production_scenarios --all --out <scratch>
# 4. Frontend, when a frontend or API contract file changed:
(cd frontend && npm test && npm run typecheck && npm run lint && npm run build)
```

The live runs need Ollama weights or API keys and paid calls: **ask first**, state the expected cost.

## Invariants

Evaluation rule: thresholds are committed before the first run on the set; the set is never used for tuning.

## Done when

- Counts with 95% intervals, guard on and off, for the local model and one hosted model; tokens and cost for each question; labeller agreement. `README.md` §4 is rewritten from these numbers.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W21 (held-out evaluation and one hosted model).
Read docs/tasks/28-W21-held-out-evaluation-and-one-hosted-model.md first, then every file it links.
You cannot write the labels: a person must. Prepare the protocol, the tooling and the question candidates, then stop and ask the user to label.

Rules for this session:
- Read the card, the linked spec and the cited code first. Do not make design decisions that the spec already made. If the spec and the code disagree, stop and ask.
- Write the tests first. Show that each fails on the parent commit for the reason the card states.
- Never weaken an existing invariant (I1-I8 in docs/gap-audit.md section 6) or an existing test to make a new test pass. If they conflict, stop and ask.
- Run mutants only in scratch copies, with an M00 no-change control and a guard that fails when no test ran.
- Ask before any run longer than 5 minutes, any network access, or any API key use. Use a throwaway PostgreSQL container, never the user's database or .data directory. Never use git stash.
- Give exact git add and git commit commands; no Co-Authored-By line; the user commits.
- After the gate passes: update the status table in docs/gap-audit.md section 11, add only verified numbers to docs/resume-evidence.md, and remove the xfail marker of a defect test only in the commit that fixes it.
- Write replies in about 80% Simplified Technical English.
```
