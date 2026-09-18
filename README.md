# Relay — AI Workflow Studio

A working **local platform with authenticated, isolated workspaces** for the visual AI orchestration platform in the supplied PDF. Build workflows from reusable nodes, save portable JSON, compile to LangGraph, and inspect execution events.

## Start

Dependencies have already been installed in this workspace. From the project directory:

```bash
python3 scripts/dev.py
```

Open **http://127.0.0.1:3000**. API documentation is at **http://127.0.0.1:8000/docs**. Ctrl-C stops the editor, API, workflow worker, and the three knowledge services. The launcher refuses to overwrite an existing service on either port.

On first launch, create your account in the browser. The first account claims existing local workflows, credentials, and run history; later accounts have isolated workspaces. A private backup of the original SQLite database is kept in `.data/backups`.

The initial canvas contains Chat input → Agent → Response. To test with **Ollama**, open **Providers** on the left (or the key icon), click **Refresh installed models**, choose a chat model such as the installed `qwen2.5:0.5b`, then click **Enable model**. Select the Agent node and choose the enabled model in the **right-hand inspector**, then click **Run workflow**. Ollama must be running on the backend machine. Demo mode remains available and echoes the prompt without calling a model.

For **OpenAI or Claude**, select that provider on the left, save an API key, select the saved key, and enter the exact chat model ID available to that API account. Click **Enable model**, then select it in the node on the right. Cloud model IDs are entered manually; enabling a model does not verify access or generate a paid request. API keys never appear in the right-hand inspector or exports. Paid cloud calls were not exercised.

The workspace catalog binds each model to its provider and, for cloud models, its credential. Disabled or unapproved models cannot run, including through imported JSON or direct API requests. Permissions are checked on submission, resume, worker startup and before each new model call. Disabling a model cannot undo a request already sent to the provider. Existing workflows remain saved, but their models must be enabled before they can run. Existing credentials migrate as OpenAI credentials. Each account currently owns its workspace; shared workspace roles are future work.

## Setup on another machine

Python 3.12+ and Node.js 22.13+ are required. Tested here on Python 3.14 and Node.js 24. Docker is required for the Python tool and optional for PostgreSQL.

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend
npm ci
cd ..
python3 scripts/dev.py
```

The startup script loads a root `.env` file when present. Copy `.env.example` to `.env` to configure storage.

## What's implemented

- React/TypeScript/React Flow editor: draggable node library, pan/zoom/minimap, editable node configuration, input bindings, edge deletion, undo/redo, save/load, JSON import/export.
- Backend-provided node schemas drive the configuration inspector.
- Thirteen visible node types in six categories: Input, Agent, Tools, Retrieval, Control and Output. Retrieve and Query select a named knowledge base. The original Prompt template and Language model nodes stay loadable for saved workflows and the LLM-free example but are not offered on the palette.
- Deterministic validation and LangGraph compilation. Each node consumes declared bindings and returns only its declared outputs.
- Conditions choose exactly one true/false branch. All paths terminate in a Response node. Unsupported parallel fan-out and cycles are rejected.
- Live server-sent node events, node status highlighting, input/output inspection, execution timing, cancellation, errors, persisted run history, and resume from saved successful-node results.
- Workflow saves and run records in SQLite or PostgreSQL via SQLAlchemy.
- OpenAI, Claude (native Messages API), and local Ollama providers; left-side configuration and workspace model catalog, with permitted-model selection on the right.
- Credentials encrypted with Fernet, referenced only by ID, and never returned by the credential API. Inline unknown credential configuration is rejected, including on draft saves.

Changes are **not autosaved**. Run history includes the executed workflow snapshot and input. Loading a historical run displays its original output without replacing your current canvas. Node highlighting appears only when the executed graph matches the current graph.

## PostgreSQL

SQLite is the zero-setup local default at `.data/workflows.db`. To use PostgreSQL:

```bash
docker compose up -d --wait postgres
cp .env.example .env
```

Uncomment this line in `.env`, then restart Relay:

```dotenv
DATABASE_URL=postgresql+psycopg://relay:relay_local@127.0.0.1:55432/relay
```

PostgreSQL uses the project-owned Docker volume `relay-workflow-foundation_relay_postgres` and binds only to `127.0.0.1:55432`. The supplied password is for this local development database. Switching database URLs does not migrate existing SQLite records. The PostgreSQL verification script creates and deletes its own isolated schema:

```bash
.venv/bin/python -m scripts.check_postgres
```

To stop PostgreSQL without deleting saved data: `docker compose stop postgres`.

## Credential key and data

A generated encryption key is stored at `.data/credential.key` with owner-only file permissions. You can instead supply `CREDENTIAL_ENCRYPTION_KEY` as a Fernet key. Back up the encryption key with the database: changing or losing it makes saved credentials unreadable. The `.data` directory and environment files are ignored by Git.

Execution inputs, outputs, and events are stored locally for debugging. Avoid putting secrets in ordinary prompt fields; use the credential form for provider keys.

## Architecture

```text
React Flow canvas + schema-driven inspector
           │ canonical Workflow v1 JSON
           ▼
FastAPI → validator → durable SQL queue → worker → LangGraph StateGraph
   │                                      │
   └── SQLAlchemy workflow/run store ◀── ordered execution events
                 │
          PostgreSQL / SQLite
```

- `backend/app/models.py`: canonical document models.
- `backend/app/registry.py`: node contracts, typed configuration, provider handlers.
- `backend/app/compiler.py`: graph validation, guaranteed-upstream binding analysis, LangGraph compilation.
- `backend/app/storage.py`: workflow, run, event and encrypted credential persistence.
- `backend/app/main.py`: API, bounded background execution, SSE, cancellation and restart recovery.
- `frontend/lib/workflow.ts`: client types, API boundary, import and save guards.
- `frontend/components/workflow-editor.tsx`: canvas/editor coordination.
- `examples/hello-workflow.json`: importable workflow without an LLM dependency.

The canonical JSON is retained alongside the compiled graph. Export returns that complete source document, including canvas layout. It does **not** claim arbitrary LangGraph objects can be reverse-engineered into lossless canvas documents.

To add a reusable node, register a `NodeDefinition` with a configuration model, required string input ports, string output ports, and async handler in `registry.py`. The inspector consumes the registry schema. New control-flow semantics require compiler support; adding a tool handler alone does not create support for loops or parallel joins.

## Verification

```bash
.venv/bin/python -m pytest backend/tests -q
cd frontend
npm test
npm run typecheck
npm run build
```

Backend tests cover actual LangGraph runs, condition branches, validation, credential isolation, authentication, SSE, cancellation/resume, lease recovery, stale-worker fencing, node-result restoration, agent grounding (citation rewriting, abstention without a model call, post-generation source verification), evidence provenance across API resume, lease recovery and cached replay, and the knowledge services (ingestion, four search modes, staged rebuilds, cleanup, tenant denial, embedding-capability refusal). Provider tests also cover Claude payloads and secret-safe errors, model revocation, credential/provider binding, workspace catalog isolation, and credential schema upgrades. Frontend unit tests cover allowed model selection, credential replacement, save races, import safety, execution highlighting and source-card merging. Browser interaction/visual QA and paid cloud model calls were not performed. Ollama inference, durable recovery, workspace isolation, and PostgreSQL leasing were verified.

For a built frontend, run the API separately and use `npm run build` followed by `npm start` in `frontend`. The built frontend proxies `/api/*` to the local API at port 8000.

## Phase two: authenticated durable execution

Accounts use salted password hashes and revocable, expiring HttpOnly session cookies. Workflows, credentials, runs, event streams, cancellation and resume are tenant-scoped. Each account currently owns one workspace; invitations and role administration are future work. This milestone uses opaque sessions, not JWT or an external identity provider.

The startup script launches the API with `RELAY_EMBEDDED_WORKER=false` and a separate `python -m backend.app.worker` process. Direct API startup defaults to an embedded worker for simpler testing. The relational database is also the durable queue; Redis is not required.

Workers claim jobs atomically, renew 30-second leases and persist every successful node's outputs. If a worker crashes, another worker can claim the expired job and restore successful node results. **Resume saved run** retries failed or cancelled runs from the saved workflow snapshot. An external model call interrupted before its result is committed may be repeated: execution is at-least-once. This is node-result checkpointing, not native LangGraph checkpoint serialization or exactly-once side effects.

Ollama's backend address is set through `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`), not through workflow JSON. The editor discovers installed models through the backend. No key is needed for local Ollama. Native API references: [chat](https://docs.ollama.com/api/chat) and [model discovery](https://docs.ollama.com/api/tags).

## Scope boundaries

This remains a local development platform, **not a finished enterprise release**. Team invitations, role administration, schema-version migration tooling, token streaming, automatic workflow retry policies, MCP, scheduling, loops, parallel execution, and workflow deployment management are later phases. Keep the app on loopback until deployment hardening is complete.

Each worker has four slots. A graph attempt has a 120-second limit and graphs are capped at 100 nodes. SSE streams node lifecycle events, not model tokens. Node outputs currently use string ports. SQLite and PostgreSQL are supported; switching databases does not migrate records automatically.

Recommended next phase: durable per-action approval for external writes, then typed (schema-validated) node outputs. Both are prerequisites for MCP connectors, richer control flow and deployment management. See `docs/superpowers/plans/2026-09-16-priority-fixes.md` for the current state and evidence.

Additional checks used for this build:

```bash
# While the local app is running:
.venv/bin/python -m scripts.smoke
# From frontend:
npm run lint
npm audit
```

Lint covers the application source. Untouched generated shadcn components and their generated mobile hook are excluded from lint; TypeScript still checks them.

Claude uses the native [Messages API](https://platform.claude.com/docs/en/api/http/messages/create). OpenAI uses Chat Completions; only models supporting text chat through that endpoint should be enabled. Ollama models must support chat. Provider-specific image, audio and reasoning controls are outside this milestone. Agent tools use a bounded JSON action protocol over text chat; native provider tool-calling APIs are not used.


## PDF handling

PDFs may cover any subject. Selectable text is extracted locally; printed English scans use local OCR. For another Mac, install the native tools before starting Relay:

```bash
brew install poppler tesseract
```

Python PDF dependencies are included in `backend/requirements.txt`. The ingestion process must have `pdftoppm` and `tesseract` on its PATH. Only installed OCR languages are available; this milestone uses English. Handwriting, charts and complex table layout are not reliably interpreted. Password-protected, corrupt, unreadable or over-limit files fail with an explanation. Truly blank pages retain their page numbers; entirely blank files cannot be indexed.


## Agent platform: nodes, tools and knowledge bases

The left library has these categories:

| Category | Nodes |
| --- | --- |
| Input | Chat input, Manual trigger |
| Agent | Agent node |
| Tools | HTTP/REST API, Email, Jira, Confluence, GitHub, Python |
| Knowledge (left panel, outside the canvas) | Named knowledge bases on FAISS, ChromaDB, Elasticsearch or Pinecone |
| Retrieval | Retrieve, Query |
| Control | Condition |
| Output | Response |

Agent ports are **top: flow input**, **bottom: flow output**, **left: attached tools**, and **right: callable specialist agents**. Multiple tool instances can attach to the same agent. Connect a tool's attachment port to the agent's left port; connect a parent agent's right port to a specialist's top port. These attachments give the agent callable capabilities; they do not execute the attached nodes as ordinary sequential steps. Each specialist has one parent. Retrieve and Query can also attach as tools. Both select a named knowledge base in the inspector.

Select an Agent to customize its role: Planner, Reasoner, Reflection, Critic, Router, Memory, Summarizer, Extraction or Classification. Role presets guide model behavior. The right inspector also provides the allowed model, system prompt, user prompt (`{input}` inserts the incoming text), temperature, top-p, maximum output tokens and call limit. Claude temperature is at most 1 and temperature/top-p cannot both be set for Claude. Keep shared provider credentials and the enabled-model catalog in the left Providers panel.

Agents may make up to six tool/specialist calls per root invocation, shared across nested specialists, with at most three levels of delegation. Models must produce the documented JSON action structure to call attachments; very small models can be less reliable. Plain-text replies are final answers. Every attached target is described to the model — a configurable **description** on the tool, Retrieve/Query or specialist node plus a hint of the input it expects — and the model's input is checked before execution. A failed call is returned to the model as a `tool_error` observation so it can adjust or choose another target; only an exhausted call budget fails the run. Retrieval results are rendered as numbered passages, never as JSON inside JSON. Agents with an **Output JSON schema** (or the extraction/classification role) must return JSON that validates against it: one bounded repair attempt, then the node fails with the validation error rather than passing malformed output downstream. **Model size matters more than any other setting.** In local testing `qwen2.5:0.5b` confabulated answers about retrieved documents and repeated tool calls until the limit; `llama3.2:3b` completed a tool call but did not consistently follow the requested argument; `llama3.1:latest` (8B) answered from evidence, cited passages and abstained when the evidence did not contain the answer. Use an 8B-class local model or a cloud model for anything beyond smoke tests. Treat model-selected actions and answers as fallible and review run events during testing. Attached invocations appear in run events, and their results feed back to the calling agent. Memory agents retain a bounded conversation history under a workspace-scoped memory key; a failed/retried parent may record a memory entry more than once.

### Connections and tools

Configure reusable encrypted connections on the left, then select them in individual tool nodes. Tool configuration is on the right. Several nodes may share a connection while using different operations.

| Tool | Implemented operations |
| --- | --- |
| HTTP/REST | GET, POST, PUT, PATCH, DELETE; relative path and body with `{input}` substitution |
| Email | Send through SMTP with STARTTLS; recipient, subject, body |
| Jira | Search, get issue, create Task, add comment |
| Confluence | Search, get page, create page |
| GitHub | List/get issues, create issue, add comment |
| Python | Execute code with `input_text` in an isolated Docker container |

Enable **Allow external writes** explicitly on each node that sends mail or changes a remote system. If a run stops after starting a write but before saving the owning node's result, Resume is blocked to avoid blindly repeating that write. Inspect the remote outcome before starting a new run. Successful checkpoints are reused. This does not provide distributed exactly-once execution.

HTTP connections use public HTTPS/HTTP endpoints. Private, loopback and metadata addresses and redirects are rejected by shared connection adapters; this also applies to remote vector stores. Local Ollama is configured separately through `OLLAMA_BASE_URL`. For Confluence Cloud, include `/wiki` in the connection base URL. Remote accounts and their own service permissions are required; adapters do not provision those accounts.

Before first Python execution on another machine:

```bash
docker pull python:3.12-alpine
```

The image is already installed here. Python runs with no network, a read-only container root, no capabilities, an unprivileged user, CPU/memory/process/output limits, and a 1–30 second timeout. Only `input_text` and the code enter the container; workspace files and credentials are not mounted. Execution never falls back to running arbitrary code on the host. Only the image's Python standard library is available.

### First named knowledge-base workflow

1. Open **Knowledge** on the left, then **New knowledge base**. Name it and choose FAISS (local), ChromaDB, Elasticsearch or Pinecone.
2. Choose a logical storage path, an installed Ollama embedding model such as `embeddinggemma:latest`, a supported index method, chunking strategy, size and overlap. Keyword-only bases can be created without embeddings.
3. Upload multiple documents and wait for indexing to complete. The Documents tab shows progress, failures, retry, replace, remove and extracted-chunk preview.
4. Use **Test search** to inspect retrieved passages before involving an Agent. Similarity, keyword, weighted hybrid and RRF share one retrieval service. Top-k, candidate count, threshold, filters and fusion settings can be customized.
5. Add a **Retrieve** node to a workflow and select the KB by name. The saved configuration contains its stable ID, so renaming does not break workflows. Shared indexing/storage settings stay in Knowledge; permitted search settings can be overridden per node.
6. One possible flow is **Chat input → Retrieve → Agent → Response**. Connecting Retrieve to Agent automatically binds its question-and-evidence context. Retrieve can also be an attached agent tool; no single workflow shape is imposed.

The context is a JSON envelope carried through the existing string port: original question, passage labels/text, canonical sources, KB ID and version. The question embedding stays inside retrieval. Source links are generated from validated IDs, not model-generated URLs.

When an Agent receives that envelope it applies the same grounding contract as the Query node. The model sees the question and numbered passages (identifiers, URLs and scores stay out of the prompt); citations must use the passage labels; any `[S#]` the evidence does not carry is rewritten to `[unsupported reference]`; sources are re-verified after generation; and an empty evidence set returns the insufficient-evidence message without calling a model. Citation labels are unique across the whole run — a second Retrieve continues numbering where the first stopped, and evidence gathered through a tool call joins the same registry — and the **Response node validates every `[S#]` in its text against everything the run retrieved**, so a label cannot pass unverified through a chain of agents. Retrieve, Agent and Response all report `sources`; the run panel shows one card per passage marked **Cited in answer** or **Retrieved only**. Citation checks verify passage identity, not the factual correctness of every claim; use the passage cards to verify generated claims.

Model calls retry transient provider failures (HTTP 429/5xx/529, timeouts) with exponential backoff; configuration errors surface immediately. Each node's success event carries the model usage it caused (`calls`, `prompt_tokens`, `completion_tokens`, from Ollama, OpenAI and Claude responses) and the execution log shows it. The worker writes one JSON line per run and node lifecycle event to the `relay.run` logger, keyed by `run_id`, without inputs or outputs.

The Knowledge hub includes metadata/settings editing, rebuild/cancel, document replacement, search defaults, job progress and cleanup history. Changing chunking, embeddings or storage creates a staged rebuild. The previous active version stays searchable until the new complete version publishes. A replacement document also leaves the old original available until successful activation. Failed new documents remain visible and retryable.

The earlier PDF-only knowledge stack and the canvas VectorDB resource stack were removed on 2026-09-16; named knowledge bases are the only retrieval path. Databases created before then may still contain empty `knowledge_*` and `vector_*` tables, which are ignored. Saved Retrieve/Query nodes that carry the retired `storage_path` field keep loading; the field is ignored.

Supported files: PDF (including printed English OCR), TXT, Markdown, CSV, JSON, HTML and DOCX. Unsupported binaries are rejected. Limits: 25 MB/file, 200 PDF pages, 20 logical documents/KB, 50,000 logical chunks/workspace and 8 million vector cells; staging has bounded additional capacity so near-limit KBs can rebuild. Extraction text is limited to 16 MB per build. Full snapshots are rebuilt in this version; extraction/embedding checkpoint reuse is not yet implemented. Interrupted attempts restart safely with new identifiers.

| Backend | Current index support | Required configuration |
| --- | --- | --- |
| FAISS | Flat cosine | Managed local storage |
| ChromaDB | HNSW cosine | Managed local storage; each build has an isolated collection |
| Elasticsearch | dense_vector cosine | Public connection and existing matching-dimension index |
| Pinecone | Existing serverless index | Public data-plane connection and matching dimensions |

Storage paths are logical names; actual local indexes use generated ownership IDs under `KB_DATA_DIR/indexes`. Remote namespaces/index segments are also generated and isolated. All embedding generation uses installed Ollama models; the digest is pinned so model changes require a rebuild. Only embedding-capable models (Ollama `/api/show` capabilities) are offered and accepted; a chat model such as `qwen2.5:0.5b` is refused at configuration and indexing time with an explanation. A knowledge base created earlier with a chat model as its embedder keeps answering with that model until it is rebuilt. OpenAI/Claude remain available for downstream Agent/Query generation. Models are never downloaded automatically.

Prompt template and Language model are hidden from the palette but remain supported in saved workflows. Keyword-only knowledge bases (no embedding model) provide BM25 retrieval without embeddings.

Hybrid search min–max normalises the cosine and BM25 candidate lists before the weighted combination, so `vector_weight` means what it says; the earlier scheme compressed cosine scores and let keyword ranking dominate at 0.5 (measured below). Score thresholds are therefore on a 0–1 scale for hybrid, raw cosine for similarity, raw BM25 for keyword and reciprocal-rank sums for RRF. Ties resolve by semantic rank so results are repeatable across index builds.

## Evaluation

`evals/` holds a labelled synthetic corpus: 20 internal-handbook documents for a fictional freight cooperative and 45 questions — 20 phrased with the documents' own terms, 20 paraphrased without them, and 5 that no document answers. `scripts/eval_retrieval.py` runs the real management, search and ingestion code in-process with real `embeddinggemma` embeddings in temporary storage, measures every retrieval mode, and optionally runs grounded generation through the workflow runtime (Chat input → Retrieve → Agent → Response). It never touches the signed-in workspace and makes no paid calls.

```bash
.venv/bin/python -m scripts.eval_retrieval
.venv/bin/python -m scripts.eval_retrieval --generate llama3.1:latest --generate-mode hybrid
```

Retrieval, 40 answerable questions, top-k 4, paragraph chunks of 800 characters (`evals/results/`):

| mode | doc R@1 | doc R@4 | MRR | answer in top-4 | paraphrase answer in top-4 | ms/query |
| --- | --- | --- | --- | --- | --- | --- |
| similarity | 0.975 | 1.0 | 0.988 | 0.975 | 0.95 | ~230 |
| keyword (BM25) | 0.775 | 0.95 | 0.858 | 0.825 | 0.80 | 3 |
| hybrid, before normalisation fix | 0.90 | 0.975 | 0.929 | — | — | ~235 |
| hybrid, after | 0.95–0.975 | 1.0 | 0.971–0.988 | 0.95 | 0.90 | ~230 |
| rrf | 0.90 | 0.975 | 0.933 | 0.925 | 0.85 | ~230 |

"Answer in top-4" checks that a retrieved passage contains the expected answer text, which is what generation actually needs; document-level hits can hide a right-file-wrong-paragraph miss. One question is 0.025, so differences below that are noise. Keyword search is 70× faster and adequate for verbatim questions; it loses paraphrases. Equal-weight RRF inherits keyword's paraphrase misses, which is why it trails similarity here.

Grounded generation with `llama3.1:latest` (8B, temperature 0) through Chat input → Retrieve → Agent → Response, 45 questions:

| metric | raw JSON envelope in prompt (rrf) | rendered passages (rrf) | rendered passages (hybrid) |
| --- | --- | --- | --- |
| answer accuracy (40 answerable) | 0.65 | 0.90 | 0.925 |
| accuracy when the answer passage was retrieved | — | 0.973 | 0.974 |
| false abstention | 0.425 | 0.10 | 0.125 |
| abstention on 5 unanswerable | 5/5 | 5/5 | 5/5 |
| citation rate | 0.625 | 0.925 | 0.95 |
| citation faithfulness (cited passage from the expected document) | 1.0 | 1.0 | 0.974 |
| `[unsupported reference]` rewrites | 0 | 0 | 0 |
| seconds per answer (Apple M5) | 8.5 | 3.6 | 3.7 |

The first column is what the Agent path produced when the model received the retrieval envelope as raw JSON: it abstained on 42% of answerable questions even when the right passage was in front of it. Rendering the evidence as numbered passages fixed most of that without changing the model. Nearly all remaining misses are retrieval misses where abstaining is the right behaviour; the genuine model errors (one wrong-passage pick, one over-eager citation) are recorded in `evals/results/*.json` with the full answers. Before this work, the same workflow on `qwen2.5:0.5b` produced confident, wrong answers about an uploaded résumé.

These numbers come from a small synthetic corpus and one local 8B model; they show the harness works and where the failure modes are, not production quality.

### Real documents

`evals/real/` holds four public-domain US government documents — IRS Publication 463 (travel and car expenses, 62 pages), NIST SP 800-63B-4 (authentication, 129 pages), 29 CFR 1910.178 (powered industrial trucks) and 49 CFR Part 395 (driver hours of service) — and 38 questions labelled against their text (15 in the documents' own terms, 18 paraphrased, 5 unanswerable). Indexing produces about 2,900 chunks, so the retrieval problem is real: many near-duplicate passages per document.

```bash
.venv/bin/python -m scripts.eval_retrieval --corpus evals/real --questions evals/real/questions.json
.venv/bin/python -m scripts.eval_retrieval --corpus evals/real --questions evals/real/questions.json --modes hybrid --generate llama3.1:latest --generate-mode hybrid
```

Retrieval, 33 answerable questions:

| mode | answer in top-4 | paraphrase answer in top-4 | answer in top-8 | ms/query |
| --- | --- | --- | --- | --- |
| similarity | 0.818 | 0.722 | 0.879 | ~245 |
| keyword (BM25) | 0.818 | 0.722 | — | 20 |
| hybrid | 0.879 | 0.833 | 0.879 | ~265 |
| rrf | **0.909** | **0.889** | 0.909 | ~260 |

Unlike the synthetic handbook, real regulatory prose rewards fusion: keyword and vector signals miss different paragraphs, and RRF recovers most of both. Widening top-k to 8 does not recover the remaining misses — they are "right document, wrong paragraph" cases deeper in the ranking, which points at section-aware chunking and reranking, not a larger window. Document-level recall is 1.0 for every mode and says nothing here.

Grounded generation with `llama3.1:latest` over hybrid retrieval, 38 questions: answer accuracy **0.879**, exactly the share of questions whose answer passage was retrieved; **accuracy 1.0 and false abstention 0.0 when the passage was retrieved**; 5/5 abstentions on unanswerable questions; citation rate 0.848, citation faithfulness 0.964, zero `[unsupported reference]` rewrites; 8.2 s per answer. Generation is now retrieval-bound. One grounding error remains on record: when retrieval missed the NIST password-hint rule, the model inferred an answer from an unrelated passage instead of abstaining.

Extend `evals/real/questions.json` — or point the harness at your own documents — before trusting any figure for your use case.

`scripts/check_workflows.py` executes every supported workflow shape for real — plain chat, grounded QA, Query, agent with a Docker Python tool, agent with Retrieve as a tool, router with specialists, condition branches, an HTTP tool step, extraction, memory across runs, chained agents, manual trigger, and the two submission gates — against an in-process knowledge base built from `evals/corpus`, and prints what each one actually did. It is a diagnostic report, not a gate: known gaps are labelled. The findings and the prioritised list of updates toward production-grade workflows are in [docs/production-gap-analysis.md](docs/production-gap-analysis.md).

```bash
.venv/bin/python -m scripts.check_workflows
```


## Knowledge service architecture and recovery

```mermaid
flowchart TB
    UI[Knowledge hub / Retrieve node] --> API[Authenticated Relay gateway]
    API --> M[Knowledge Management Service :8011]
    API --> S[Retrieval and Index Service :8012]
    I[Ingestion Worker Service] -->|Claim / renew / publish / cleanup status| M
    I -->|Staged chunks and vectors| S
    M --> MD[(Management database: metadata, originals, versions and jobs)]
    S --> SD[(Search database: manifests, chunks, keyword postings and archives)]
    S --> V[(Selected vector backend)]
    I --> O[Ollama embeddings]
    API -->|Question embeddings| O
```

`python3 scripts/dev.py` starts both internal HTTP services before the gateway, plus ingestion and workflow workers. Internal `/rpc` endpoints require a shared service key and an allowlisted operation. The gateway supplies the authenticated tenant; public requests cannot override it. Health endpoints expose only service health. Ports 8011/8012 bind to loopback in the launcher. Each service owns separate SQL tables and a separate database by default. The ingestion worker performs no direct SQL writes.

Local defaults are `.data/knowledge/management.db` and `.data/knowledge/search.db`. Original document bytes are stored transactionally in the management database rather than a separate filesystem/object store; this makes upload metadata, original storage and job creation atomic. Index files are under `.data/knowledge/indexes`. PostgreSQL is supported through `KB_MANAGEMENT_DATABASE_URL` and `KB_SEARCH_DATABASE_URL`; provision the named databases (or separate schemas) before starting. Switching URLs does not migrate data automatically. The local launcher profile uses ports8011/8012; separately deployed services can use the configurable URLs in `.env.example`.

Each upload has an idempotency key and content fingerprint. Jobs have unique attempts, renewable30-second leases and bounded crash recovery. Superseded, cancelled and expired workers cannot publish. An active build/version pointer changes only after a completed index is accepted in a fenced transaction. Uploading another file while indexing may supersede the pending full snapshot; the worker processes the latest desired document set.

Failure recovery crosses service boundaries through durable cleanup records. Before vector writes, the search service records the attempt and reserves its chunk IDs. Failed/cancelled attempts remain unsearchable. Cleanup tombstones repeatedly remove abandoned vectors, postings and partial chunks; success is rechecked after300 seconds to catch delayed writes. Failed cleanup uses exponential backoff capped at300 seconds. These records remain visible in Activity; storage outages do not erase cleanup responsibility.

Retired successful versions preserve canonical passage metadata for saved-source verification while vectors/postings are removed. These explicit provenance archives are not searchable. KB deletion purges them; document removal/replacement immediately revokes source authorization even before physical cleanup. Existing Relay run history and backups can still contain earlier excerpts. This is eventual physical cleanup with immediate access revocation, not a cross-database atomic transaction.

Search uses persisted keyword postings instead of retokenizing every document for each question. Model digest/dimension checks prevent incompatible embedding reuse. Searches of active versions do not acquire the mutation lock held by staging builds. Resource failures return errors; they are never represented as an empty matching set. A120-second workflow deadline includes KB preflight and execution. Ingestion is separate from workflow capacity, but both can contend for the same local Ollama hardware.

Back up the Relay database/encryption key, both knowledge databases, index files and `.data/kb-service.key` together. The service key also encrypts stored remote connections. Keep authenticated production mode when creating real KBs; `auth_enabled=False` is a test-only harness, and anonymous test KBs are not transferred during first-account registration.

Verification commands:

```bash
# Three actual service processes, isolated temporary storage, real local embeddings:
.venv/bin/python -m scripts.check_knowledge_services
# Separate disposable schemas in the project PostgreSQL container:
.venv/bin/python -m scripts.check_kb_postgres
```

The service smoke covers indexing, four search modes, direct named-KB workflow execution, rebuild activation, retained provenance, deletion and RPC authentication. Fault tests cover partial vector writes, stale leases, retries, cleanup failure/backoff, replacement failure, duplicate requests, dimension changes, quota headroom and cross-tenant denial. Remote ES/Pinecone operations use mock transports; no paid cloud calls or real remote writes were made. Browser interaction/visual QA remains for user testing.


### Reliability and evaluation corrections

Failed external writes now stop the active agent run, including through specialist delegation. Inspect the remote system and reconcile the result before attempting the action again. Read-only tool failures remain bounded observations. The existing durable write marker continues to block unsafe resume; this is conservative stop-and-inspect behavior, not an automated action ledger or exactly-once delivery.

A structured Agent with no retrieved evidence and no attached tools fails explicitly instead of returning plain text that violates its output schema. Ordinary text-answer agents retain their no-evidence response. Ollama timeouts now use the bounded provider retry policy, subject to the overall workflow deadline.

New generation evaluation reports use `metric_version: 2`: `answer_substring_match_rate`, `substring_match_when_answer_retrieved`, and `citation_document_match`. These are heuristic checks, not semantic accuracy or claim-level citation faithfulness. Answer-passage matching also requires the expected source document. Each report carries these limitations. Previously saved reports retain their original names and values; they have not been rerun or retroactively rescored. Human review or a separately validated claim-evidence evaluator is still required to assess entailment.

### Optional claim-level evaluation (local Ollama)

Run ` .venv/bin/python -m scripts.eval_retrieval --generate llama3.1:latest --judge llama3.1:latest --limit 5 --out evals/results/claims-latest.json` from the project root. The judge runs only in the evaluation harness; live workflow answers are unchanged. No cloud API credits are required.

Each answer receives exact quoted claims, citation labels, support/contradiction/insufficient-evidence verdicts and rationale. Invalid citations, malformed judgments and unavailable models produce explicit evaluation errors, not passing scores. Empty claim lists receive no support score. Inputs over 50,000 characters are rejected instead of silently truncating evidence. Reports include judged/error/skipped counts and the mean supported fraction for judged answers only.

These are model estimates. A judge may omit claims, assign citations incorrectly, or be wrong about entailment; using the same model to answer and judge can create correlated errors. Calibrate with human-reviewed examples before using this as a release gate. Existing substring and document-match metrics remain separately labeled.
