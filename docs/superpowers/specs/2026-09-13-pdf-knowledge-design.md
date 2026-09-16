# Phase 3A: PDF knowledge bases

Status: approved and implemented on 2026-09-13. Verification: 77 backend tests, 13 frontend tests, typecheck/lint/build, isolated PostgreSQL checks and a real PDF-to-Ollama answer with a validated page reference.

## Agreed direction

Users can upload PDFs on any subject and ask questions about their contents. Preserve local testing without cloud API credits, workspace isolation, left-side resource configuration and right-side node selection. MCP connectors and other file formats follow later.

## User flow

1. Open Knowledge on the left and create a named knowledge base.
2. Upload one or more PDFs. Each file shows queued, processing, ready, or failed status, with an actionable error when needed.
3. Readable text is extracted page by page. Printed scanned pages receive local OCR. Mixed PDFs use OCR only for pages without useful extracted text. Preserve original page numbers.
4. Select a Retrieval node on the canvas. In its right inspector select an accessible knowledge base and the number of passages to retrieve (default 4, range 1–8).
5. Connect Question → Retrieval → Grounded answer → Response. The Grounded answer node selects an enabled Ollama, OpenAI or Claude chat model using the existing model catalog.
6. Run a question. Show the answer, retrieved excerpts, filenames and page references. A source reference opens the authorized original PDF at that page.

Provide a starter PDF-question workflow so the first test does not require manually binding every port. Existing workflows and the ordinary Prompt and LLM nodes keep their current behavior.

## Initial retrieval approach

Use local BM25 keyword ranking over overlapping page-based text chunks. This requires neither an embedding model download nor API credits. It is a useful first retrieval baseline, but paraphrased questions can miss relevant passages. Semantic embeddings and hybrid ranking are later upgrades behind the same retrieval interface.

Alternative considered: Ollama embeddings from the start. This improves semantic matching but adds an embedding-model installation, a second model configuration and vector-index lifecycle to the first PDF milestone. A hosted vector service also adds external setup. Keep this milestone local and self-contained.

## PDF boundaries and processing

Support ordinary PDFs with selectable text and printed scans. Local OCR uses Tesseract with the installed language packs; English is the initial supported OCR language. Handwriting, image/chart interpretation, exact reconstruction of complex tables and additional OCR languages are later upgrades. Explain these limits in upload help.

Reject corrupt, password-protected or unsupported PDFs with specific messages rather than reporting successful ingestion. Initial limits: 25 MB and 200 pages per PDF, 20 active PDFs per knowledge base, 50,000 indexed chunks per workspace. Enforce limits on the server, including streamed upload size. Limit extraction/OCR runtime and process concurrency; never silently discard pages or mark partially failed extraction as fully ready.

Store files under server-generated IDs outside public frontend assets. Never construct storage paths from uploaded filenames. Run extraction/OCR in a separate, killable process with a timeout. Do not execute PDF scripts, embedded attachments or external references.

Indexing is a durable background job separate from workflow execution. Persist processing state and retry eligibility. Publish chunks only when the entire document succeeds; interrupted attempts must not expose a partial index. Replacement uploads create a new document revision.

## Runtime and data contracts

Add a focused knowledge module for knowledge bases, documents, page chunks, indexing jobs and retrieval. Use the existing SQLAlchemy database and worker conventions. Avoid adding a separate database server.

The Retrieval node accepts a query string and returns string ports for query, context and JSON-serialized source metadata, preserving the existing workflow port contract. Source metadata contains stable document/revision IDs, page numbers, excerpt IDs and retrieval scores. Bound context size before model generation.

The Grounded answer node accepts those outputs, verifies source metadata against authorized stored chunks, and invokes the existing provider transports. Keep this node separate from the ordinary LLM node to make document-specific behavior explicit. Document passages are untrusted evidence, never application instructions. The system prompt requests an answer based only on supplied passages, with passage references, and asks the model to acknowledge insufficient evidence.

If retrieval yields no matching passages, return a clear insufficient-evidence result without a model call. Model-produced citations must be checked against the retrieved passage IDs. Invalid references cannot become clickable citations; report an unsupported reference instead. The UI must distinguish retrieved sources from validated inline references. Citation validation proves a reference exists, not that every generated claim is correct.

## Ownership and history

Knowledge bases, document downloads, upload/status routes, searches and indexing jobs are workspace-scoped. Check resource access at API submission, resume and worker execution. Imported workflow IDs do not grant access to another workspace. Preserve existing provider/model permission checks in the new answer node.

Checkpoint the retrieved excerpts and document revisions with each run. Resuming a run reuses its recorded retrieval results rather than silently searching changed documents. Removing a document prevents new retrieval and download access, including on resume; fail clearly if a resumed run requires a removed source. Existing run history may retain already-recorded excerpts under the current run-history retention behavior. Explain this at document removal.

## Verification and acceptance

Test text PDFs, printed scans, mixed pages, malformed/encrypted documents, limits, indexing interruption/recovery, tenant isolation and unauthorized downloads. Use deterministic fixtures with known page facts to test passage ranking, page references, no-match behavior, forged source metadata and invalid model citations.

Test PDF removal between queueing and execution and between failure and resume. Verify existing workflows still run. Test knowledge-base selection and upload-state handling in the frontend, then run type checks, lint and build.

Verify a complete local PDF upload → indexing → question → Ollama answer → source-reference workflow with a temporary workspace/database. Use simulated responses for cloud-provider tests; do not spend API credits. User performs the first interactive acceptance test after delivery.
