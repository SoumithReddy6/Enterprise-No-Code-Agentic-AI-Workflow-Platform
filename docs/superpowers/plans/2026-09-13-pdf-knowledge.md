# PDF Knowledge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upload PDFs, retrieve page evidence locally and produce source-linked answers through existing permitted models.
**Architecture:** A knowledge storage/service module owns PDF bytes, chunks and durable indexing leases. Separate extraction subprocesses provide bounded text/OCR processing. Retrieval and grounded-answer nodes integrate with the existing worker, and a Knowledge sidebar manages resources.
**Tech Stack:** FastAPI, SQLAlchemy, pypdf, Poppler/Tesseract subprocesses, React/TypeScript, native existing provider transports.
**Spec:** ../specs/2026-09-13-pdf-knowledge-design.md

## Global constraints

25 MB and 200 pages per PDF; 20 active PDFs per knowledge base; 50,000 chunks per workspace. Default four passages, maximum eight. Local BM25 retrieval; English OCR for printed scans. Workspace scope and permission checks include queue execution, resume and downloads. No paid API calls. Existing workflows remain compatible.

Ruling: execute in the existing workspace because it is not a Git checkout; no Git commit or worktree can be created without introducing unrelated repository setup.
Ruling: use raw application/pdf upload bodies with a filename query parameter, avoiding multipart buffering and preserving binary bytes through the built proxy.
Ruling: store original PDF bytes in the SQL document record, outside frontend assets. This keeps database backup/tenant deletion atomic and avoids filename-based filesystem access.

## Task 1: PDF extraction and knowledge service

Files: new backend/app/knowledge.py, pdf_extract.py, knowledge_api.py; new backend/tests/test_knowledge.py; update backend/requirements.txt only for PDF dependencies.
Interfaces: Knowledge(store) exposes create(name,tenant_id), bases(tenant_id), documents(base_id,tenant_id), upload(base_id,filename,content,tenant_id), remove(document_id,tenant_id), retry(document_id,tenant_id), download(document_id,tenant_id), retrieve(base_id,query,limit,tenant_id), verify_sources(sources,tenant_id), check_base(base_id,tenant_id), serve(). Database models derive from existing storage.Base and must be imported before create_all. install_knowledge_routes(app,knowledge,tenant_dependency) owns /api/knowledge and /api/documents endpoints.

- [x] Write tests for tenant separation, source validation, BM25 known-fact ranking, whole-document publication, limits and failed extraction; run `.venv/bin/python -m pytest backend/tests/test_knowledge.py -q` and observe missing behavior.
- [x] Implement `Knowledge` with server-generated document IDs, SQL binary content, page chunks, fenced indexing claims and bounded subprocess extraction; transactional per-base/per-workspace limits. `retrieve` returns a list of dictionaries `{id,document_id,filename,page,text,score}`; `verify_sources` returns canonical dictionaries and rejects changed/removed/foreign IDs or content.
- [x] Extraction subprocess accepts PDF path and JSON output path. pypdf extracts text; missing text pages use bounded Poppler rendering and Tesseract English OCR. Reject corrupt/encrypted/over-limit files. Safe subprocess arguments, no shell execution. Parent process enforces overall timeout and kills the process group on cancellation.
- [x] API: GET/POST /api/knowledge; GET/POST /api/knowledge/{id}/documents (POST raw PDF); POST /api/documents/{id}/remove and /retry; GET /api/documents/{id}/file. Return upload 202 and bounded read from Request.stream. Metadata includes id,filename,status,error,pages; knowledge bases include id,name.
- [x] Run focused tests; report exact API and service signatures to integration owner.

## Task 2: Editor resource management and sources

Files: new frontend/components/knowledge-settings.tsx, source-panel.tsx, frontend/lib/knowledge.ts and tests; update workflow-editor.tsx, globals.css and proxy route.

- [x] Write helper tests for starter graph and source parsing. Test sources remain plain text and links use only validated server-issued document IDs.
- [x] Knowledge sidebar creates bases, uploads PDFs, polls pending document states, displays limits and errors, retries failed documents, removes documents with retention explanation, and starts a ready-to-wire PDF question workflow.
- [x] Starter graph: chat_input `input` → retrieval `retrieve` (input query=input.message, config knowledge_base_id,limit=4) → grounded_answer `answer` (query=retrieve.query, context=retrieve.context, sources=retrieve.sources; existing LLM config) → response `out` (text=answer.text).
- [x] Inspector uses knowledge-base dropdown for retrieval; numeric limit input; grounded_answer uses existing permitted model selector and system prompt. Source panel consumes successful grounded_answer outputs.sources from run events and shows citation-used vs retrieved-only labels, filename/page/excerpt and authorized file links.
- [x] Proxy upload uses binary-safe body; enforce upload size before buffering; forward PDF content-type/disposition safely. Run frontend tests, typecheck/lint/build.

## Task 3: Runtime and integration

Files: backend/app/registry.py, compiler.py, main.py, worker.py, storage.py, auth.py; new backend/app/knowledge_nodes.py and backend/tests/test_knowledge_runtime.py.

- [x] Write compiler/node tests for retrieval contracts, no-match without a model request, canonical sources, invalid references, model permissions, deleted-source cached resumes and workspace isolation.
- [x] Extend Context and compile_workflow with a knowledge resolver callback. Keep original node signatures compatible. Register retrieval and grounded_answer with string ports.
- [x] Grounded answer obtains canonical sources through Knowledge.verify_sources, verifies context matches canonical excerpts, and uses the existing LLM handler with document-as-evidence instructions. Check `[S1]` style citation IDs; replace invalid references with an explicit unsupported-reference marker. Return text,provider,sources JSON; sources carry `citation` and `cited` fields for the UI. No-match returns insufficient evidence without provider call.
- [x] Include grounded_answer in existing catalog permission validation. Validate selected knowledge bases in API and worker; verify cached retrieval and answer sources before resume and execution. Worker callbacks derive the current fenced run tenant every time.
- [x] Start a knowledge indexing loop with the existing worker lifecycle and import knowledge tables before Base.create_all. First-account claim includes knowledge bases/documents/chunks through their owning tenant records.
- [x] Run all backend and frontend checks and independent review, then resolve findings.

## Task 4: Local acceptance and documentation

- [x] Install required local OCR tools if absent and verify text/scan fixtures without cloud requests. Use the local environment's supported Python package manager and bounded tool subprocesses.
- [x] Test PostgreSQL schema/indexing transactions in an isolated schema where the project-owned service is available.
- [x] Back up the private database, restart only Relay services, verify authenticated routes and an isolated PDF upload-to-Ollama answer with source pages. Preserve the user's account and saved documents.
- [x] Update README with user steps, extraction/search limits and source-retention behavior. Mark plan complete only after evidence; leave app running for user testing.

## Completion evidence

77 backend tests and 13 frontend tests passed. Typecheck, lint and build passed. Isolated PostgreSQL tests passed for workflow persistence, authentication, queue fencing and PDF knowledge storage/retrieval. Real local Ollama returned "The planet that contains the Caloris basin is Mercury. [S1]" from planet-facts.pdf page 2. Independent review found one blank-separator-page issue; fixed and re-reviewed with no remaining findings in scope. User-owned database backed up before restart; no paid cloud requests or browser interaction tests.
