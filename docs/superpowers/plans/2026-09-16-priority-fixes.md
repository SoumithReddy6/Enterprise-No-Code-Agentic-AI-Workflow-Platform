# Priority Fixes Implementation Plan

Spec: ../specs/2026-09-16-priority-fixes-design.md

- [x] Git baseline and database backup (`64ef8f6`, `.data/backups/before-priority-fixes-20260916T133341Z.db`).
- [x] Agent grounding: envelope detection, rendered evidence prompt, citation validation, abstention without model call, post-generation verification, `sources` output, run-panel card merging. Tests: `backend/tests/test_agent_grounding.py`, `frontend/tests/grounding.test.ts`.
- [x] Ollama capabilities: `/api/show` in discovery, embedding requirement at configuration/indexing, chat picker filter. Tests in `test_kb_plumbing.py` and `test_kb_services_integration.py`.
- [x] Model upgrade: `llama3.1:latest` enabled in the user's catalog; six saved questions re-run through the real worker on a database copy against the live knowledge services.
- [x] Evaluation corpus, questions and harness; retrieval measured across four modes; hybrid min–max fix and deterministic tie-break; generation measured with `llama3.1` before and after prompt rendering.
- [x] Legacy stack removal with provenance tests rewritten onto the knowledge services; README and docs updated.

## Completion evidence — 2026-09-16

- Backend: 150 tests passed after legacy removal (196 before removal; the retired suites covered the removed stacks). Frontend: 19 tests passed; typecheck, lint and production build passed. 13 visible node types, matching `scripts/smoke.py`.
- Real re-run of the six saved questions on `llama3.1:latest` through `Worker.execute` against the live knowledge services on a copy of the user database: abstained correctly on the off-topic question, answered the résumé questions from evidence, cited `[S1]`/`[S3]`.
- Retrieval (real `embeddinggemma`, 20 docs, 241 chunks, 40 answerable questions): similarity doc R@1 0.975; keyword 0.775 (paraphrase 0.60); hybrid 0.90 before the min–max fix, 0.95–0.975 after; RRF 0.90. Passage-level answer@4: similarity 0.975, keyword 0.825, hybrid 0.95, RRF 0.925.
- Generation (`llama3.1:latest`, 45 questions): raw-envelope prompt 0.65 accuracy / 0.425 false abstention; rendered passages 0.90 (RRF) and 0.925 (hybrid) accuracy, 0.10–0.125 false abstention, 5/5 abstentions on unanswerables, citation faithfulness 0.97–1.0, zero unsupported references, 3.6 s/answer.
- Commits: `64ef8f6` baseline, `ead0bf2` grounding and capability filter, `6c94d12` legacy removal, fusion fix and evaluation, plus this documentation commit.
- Not done: relocating the checkout out of iCloud (user decision); rebuilding the user's existing knowledge base with a real embedding model (one click in the Knowledge panel after restart); per-action write approval and typed outputs (next phase). The running `scripts/dev.py` processes predate these changes and must be restarted. No paid model calls, no external writes, no browser QA.
