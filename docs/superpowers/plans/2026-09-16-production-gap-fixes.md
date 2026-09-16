# Production Gap Fixes Implementation Plan

Spec: ../../production-gap-analysis.md (findings F1–F10, Tier 1 items 1–6). User instruction: fix the gaps one by one and test each against real data.

- [x] 1. Tool errors as observations; budget-bounded retries; run fails only on an exhausted budget. Test: `test_agent_tools.py`.
- [x] 2. Target descriptions and input hints in `available_targets`; `description` field on tools, Retrieve/Query and Agent; model input validated. Default Python-tool description describes the contract, not the source.
- [x] 3. Rendered observations: retrieval results become numbered passages; specialist and query outputs contribute their evidence.
- [x] 4. Run-wide evidence registry (`Context.run`) with run-unique `[S#]` labels; Response validates citations and reports `sources`; restored checkpoints keep labels. Test: `test_agent_tools.py::test_citation_labels_are_unique_across_the_run_and_validated_at_response`.
- [x] 5. `output_schema` on Agent (JSON Schema, validated at configuration); extraction/classification require a JSON object; one repair, then failure. Test: `test_agent_structured.py`.
- [x] 6. Provider retries with backoff for 429/5xx/529/timeouts; token usage per node on success events and in the execution log; `relay.run` JSON journal. Test: `test_observability.py`.
- [x] Real-document corpus (`evals/real`, four public-domain US government documents, approved download) with 38 labelled questions; harness accepts PDF/HTML and never indexes the question file.

## Completion evidence — 2026-09-16

- Backend 163 tests passed; frontend 19 tests, typecheck, lint passed.
- `scripts/check_workflows.py`: 14/14 shapes green after fixes (before: 4 failed, 5/9/11 hollow). Scenario 4 returns `53.0` through the described calculator tool; scenario 5 answers `22 days` from rendered retrieval; scenario 11's Response validates its citations.
- Real documents, retrieval answer@4: similarity 0.818, keyword 0.818, hybrid 0.879, RRF 0.909; top-k 8 leaves RRF at 0.909.
- Real documents, generation (`llama3.1`, hybrid): accuracy 0.879 = answer-retrieved rate; accuracy 1.0 and false abstention 0.0 when retrieved; 5/5 abstentions on unanswerables; citation faithfulness 0.964; zero unsupported references; 8.2 s/answer.
- New findings recorded: F11 chunking/reranking, F12 entailment, F13 description priming.
- Not done: Tier 2 (native tool calling, per-action approval, action ledger, versions, streaming, ACLs, reranking, entailment). The running `scripts/dev.py` predates these commits and must be restarted. No paid model calls, no external writes, no browser QA.
