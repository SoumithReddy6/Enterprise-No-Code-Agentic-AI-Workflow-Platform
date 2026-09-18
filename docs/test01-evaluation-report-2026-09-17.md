# Test01 retrieval and grounded-generation report

Date: 2026-09-17

Corpus: `evals/corpus`

Questions: `evals/questions.json`

Execution: isolated temporary knowledge base; the signed-in workspace was not modified

## Purpose

Test01 now measures two different system responsibilities:

1. **Retrieval:** whether the required evidence appears in the returned chunks.
2. **Grounded generation:** whether the Agent uses retrieved evidence to answer correctly, cite it, or abstain safely.

The question set contains 20 verbatim questions, 20 paraphrases, 5 reasoning questions, and 5 unanswerable questions. Retrieval metrics use the 45 answerable questions. The five reasoning questions include multi-document evidence and compound-answer requirements.

## Configuration

- Embedding model: `embeddinggemma:latest`
- Vector backend: FAISS
- Candidate count: 20
- Paragraph chunking: size 800, overlap 120, 241 chunks
- Fixed chunking: size 800, overlap 120, 49 chunks
- Retrieval modes: similarity, keyword, hybrid, RRF
- Generation model: `llama3.1:latest`
- Generation temperature: 0
- Generation retrieval baseline: paragraph chunking, similarity, top 4

`embeddinggemma:latest` was the only installed embedding model, so this run compares retrieval and chunking methods but does not compare embedding models.

## Retrieval results

Evidence coverage requires an answer-bearing phrase in the expected document. For multi-document questions, every required evidence group must be retrieved.

### Paragraph chunking, top 4

| Mode | Document R@1 | Evidence coverage@4 | Verbatim | Paraphrase | Reasoning | Mean latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Similarity | 97.8% | 97.8% | 100% | 95% | 100% | 229 ms |
| Keyword | 80.0% | 84.4% | 85% | 80% | 100% | 3 ms |
| Hybrid | 97.8% | 95.6% | 100% | 90% | 100% | 233 ms |
| RRF | 91.1% | 93.3% | 100% | 85% | 100% | 237 ms |

Similarity retrieved complete evidence for 44 of 45 answerable questions. The miss was the paraphrased `$30 lunch` receipt question: the expected document was retrieved, but its receipt paragraph was absent from the top four chunks.

### Paragraph chunking, top 8

| Mode | Evidence coverage@8 |
| --- | ---: |
| Similarity | 97.8% |
| Keyword | 91.1% |
| Hybrid | 97.8% |
| RRF | 95.6% |

Increasing similarity from four to eight chunks did not recover the receipt evidence. It improved keyword, hybrid, and RRF coverage, but also doubles the maximum context passed downstream.

### Fixed chunking, top 4

| Mode | Document R@1 | Evidence coverage@4 | Verbatim | Paraphrase | Reasoning |
| --- | ---: | ---: | ---: | ---: | ---: |
| Similarity | 84.4% | 97.8% | 100% | 95% | 100% |
| Keyword | 82.2% | 88.9% | 100% | 75% | 100% |
| Hybrid | 91.1% | 97.8% | 100% | 95% | 100% |
| RRF | 88.9% | **100%** | 100% | 100% | 100% |

Fixed-window RRF achieved complete Test01 evidence coverage. Its lower document R@1 means the first returned document was less often the expected document, but all required evidence appeared within the top four. This is a result for the small synthetic corpus and is not enough by itself to change the product default.

## Grounded-generation results

Generation used paragraph chunking with similarity retrieval at top 4.

| Metric | Result |
| --- | ---: |
| Answerable questions | 45 |
| Required-answer match | 42/45 (93.3%) |
| Complete evidence retrieved | 44/45 (97.8%) |
| Answer match when evidence was retrieved | 42/44 (95.5%) |
| Citation rate on answerable questions | 100% |
| Citation document match | 97.8% |
| Runtime errors | 0 |
| Mean generation time | 3.56 seconds |

### Failure ownership

1. **Receipt question — retrieval failure followed by an unsafe answer.** The correct document was present, but the receipt rule was not. The Agent incorrectly concluded that a receipt was unnecessary using the travel per-diem passage.
2. **45-day expense submission — reasoning failure.** The 30-day rule was retrieved, but the Agent said the evidence was insufficient instead of concluding that 45 days is late.
3. **Four remote days — reasoning failure.** The three-day maximum was retrieved, but the Agent declined to conclude that four days is disallowed.

The combined hotel-plus-meals calculation, 6% severity threshold, 20-minute excursion, and front/steer-axle terminology cases passed in this run.

## Unanswerable questions

The phrase-based evaluator marked 5/5 as abstentions. Human review found only 4/5 clean abstentions. The payroll-bank response stated that no bank was identified but then offered an unsupported “educated guess” connected to the Finance department.

This difference is material: detecting an abstention phrase does not prove that the rest of the response is safe. Strict abstention needs a structured response contract or claim-level verification.

## Conclusions and next actions

1. Keep retrieval and generation scores separate. Test01 contains both retrieval misses and reasoning failures.
2. Use fixed-window RRF as the Test01 comparison candidate, but do not change the platform default until it also passes the real-document benchmark.
3. Add strict answer validation for comparisons and limits so `45 > 30` and `4 > 3` cannot be replaced by an unnecessary abstention.
4. Enforce a grounded abstention contract that prevents factual guesses after declaring insufficient evidence.
5. Add at least one more embedding model before drawing conclusions about embedding quality. Rebuild the same temporary corpus for every embedding model because vector spaces are not interchangeable.
6. Preserve these reports as the baseline and rerun them after each retrieval, prompt, model, or chunking change.

## Artifacts

- `evals/results/test01-retrieval-embeddinggemma-paragraph-k4.json`
- `evals/results/test01-retrieval-embeddinggemma-paragraph-k8.json`
- `evals/results/test01-retrieval-embeddinggemma-fixed-k4.json`
- `evals/results/test01-generation-llama3.1-similarity.json`

## Follow-up implementation and model comparison

The runtime now enforces a declared abstention after generation. If a grounded answer says the evidence is insufficient, the runtime replaces the entire response with the standard no-evidence message and removes cited-source markings. This prevents responses such as the payroll-bank answer from declaring insufficient evidence and then adding an “educated guess.” The check is limited to the opening decision or final conclusion so a correct answer is not erased merely because a later note says other passages lack information.

The final enforcement-only run is `evals/results/test01-generation-llama3.1-enforced-abstention-final.json`:

- 40/45 answerable questions matched the stricter expected-answer rules (88.9%);
- 44/45 had complete evidence retrieved (97.8%);
- all 5 unanswerable questions returned clean abstentions;
- zero runtime errors and zero unsupported citation labels occurred.

The five answer failures were the receipt retrieval miss, a mixed callback answer converted to a safe abstention, the 45-day and four-remote-day comparison failures, and the incorrect SEV2 classification for a 6% outage. Prompt-only comparison instructions were tested and removed because they caused unrelated regressions. A local `llama3.1:latest` claim judge was also rejected: only 1 of 7 focused judgments satisfied its output contract.

`qwen3-embedding:0.6b` was downloaded and evaluated as a second local embedding model:

| Configuration | Evidence coverage | Document R@1 |
| --- | ---: | ---: |
| EmbeddingGemma, fixed, RRF, top 4 | 100% | 88.9% |
| Qwen3 0.6B, fixed, RRF, top 4 | 100% | 91.1% |
| Qwen3 0.6B, fixed, hybrid, top 4 | 100% | 91.1% |
| Qwen3 0.6B, paragraph, hybrid, top 4 | 97.8% | 97.8% |

Fixed chunks improved retrieval coverage but reduced `llama3.1` generation quality, so they should not become the default. The next retrieval change should keep paragraph-sized evidence and add a reranking stage for the receipt-style semantic miss. Numeric policy reasoning requires either a qualified stronger generation model or a structured verifier that passes calibration; additional prompt wording is not an acceptable production fix.

Additional artifacts:

- `evals/results/test01-retrieval-qwen3-embedding-0.6b-fixed-k4.json`
- `evals/results/test01-retrieval-qwen3-embedding-0.6b-paragraph-k4.json`
- `evals/results/test01-retrieval-qwen3-embedding-0.6b-paragraph-k8-hybrid.json`
- `evals/results/test01-claim-judge-calibration.json`
