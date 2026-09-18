# Test01 Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the manually exercised Test01 questions into a repeatable benchmark that separates retrieval coverage from grounded generation behavior.

**Architecture:** Extend the existing isolated `scripts.eval_retrieval` harness so a question may require evidence from one or several documents. Keep the existing single-document question schema compatible, add the five difficult reasoning questions, then run retrieval and grounded generation against a temporary knowledge base using local Ollama models.

**Tech Stack:** Python 3.14, pytest, asyncio, the existing Relay knowledge services, Ollama `embeddinggemma:latest`, and Ollama `llama3.1:latest`.

**Spec:** `docs/manual-test-findings-2026-09-16.md`

## Global Constraints

- Do not read from or write to the signed-in workspace knowledge bases.
- Keep existing `document` plus `answers` question records compatible.
- Score evidence only when an expected phrase occurs in its expected document.
- Report retrieval and generation separately.
- Preserve every unrelated uncommitted workspace change.

---

### Task 1: Multi-document evidence scoring

**Files:**
- Modify: `scripts/eval_retrieval.py`
- Modify: `backend/tests/test_eval_metrics.py`

**Interfaces:**
- Consumes: question dictionaries with either `document` plus `answers`, or `evidence` entries containing `document` plus `answers`.
- Produces: normalized evidence groups and a coverage score in the inclusive range `0.0..1.0`; existing `answer_present` remains true only when coverage is complete.

- [x] **Step 1: Write failing unit tests**

Add tests showing that one of two required evidence groups produces `0.5` coverage, both produce `1.0`, and a matching phrase in the wrong document does not count.

- [x] **Step 2: Verify the tests fail for the missing interface**

Run: `.venv/bin/python -m pytest backend/tests/test_eval_metrics.py -q`

Expected: failure because the evidence normalization and coverage functions do not exist.

- [x] **Step 3: Implement the evidence helpers and integrate them**

Normalize legacy records to one evidence group. For each group, count it as covered only when one of its answer phrases occurs in a retrieved source from that group’s document. Store `evidence_coverage` per row and aggregate mean coverage per retrieval mode.

- [x] **Step 4: Verify the focused tests pass**

Run: `.venv/bin/python -m pytest backend/tests/test_eval_metrics.py -q`

Expected: all focused tests pass.

### Task 2: Exact difficult Test01 cases and live benchmark

**Files:**
- Modify: `evals/questions.json`
- Create: `evals/results/test01-retrieval-embeddinggemma-paragraph-k4.json`
- Create: `evals/results/test01-generation-llama3.1-similarity.json`

**Interfaces:**
- Consumes: the Test01 corpus under `evals/corpus` and the installed Ollama models.
- Produces: retrieval metrics for similarity, keyword, hybrid, and RRF, plus generation rows that expose retrieval success independently from answer behavior.

- [x] **Step 1: Add the five manually tested reasoning questions**

Add the combined hotel and meals limit, 45-day expense deadline, four-day remote-work request, 6% outage classification, and 20-minute temperature excursion. The combined travel question requires evidence from both `expense-reimbursement.md` and `travel-policy.md`.

- [x] **Step 2: Validate the question file and run focused unit tests**

Run: `jq empty evals/questions.json`

Run: `.venv/bin/python -m pytest backend/tests/test_eval_metrics.py -q`

- [x] **Step 3: Run the retrieval matrix**

Run: `.venv/bin/python -m scripts.eval_retrieval --embedding-model embeddinggemma:latest --chunking paragraph --chunk-size 800 --chunk-overlap 120 --modes similarity,keyword,hybrid,rrf --top-k 4 --out evals/results/test01-retrieval-embeddinggemma-paragraph-k4.json`

- [x] **Step 4: Run grounded generation using the best retrieval mode**

Run: `.venv/bin/python -m scripts.eval_retrieval --embedding-model embeddinggemma:latest --chunking paragraph --chunk-size 800 --chunk-overlap 120 --modes similarity --top-k 4 --generate llama3.1:latest --generate-mode similarity --out evals/results/test01-generation-llama3.1-similarity.json`

- [x] **Step 5: Verify artifacts and summarize failures by layer**

Run: `git diff --check`

Inspect retrieval misses separately from rows where evidence was retrieved but generation failed, then record the exact configuration and limitations in the report.
