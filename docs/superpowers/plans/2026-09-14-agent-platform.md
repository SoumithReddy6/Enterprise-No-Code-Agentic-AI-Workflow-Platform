# Agent Platform Implementation Plan

> Use superpowers:subagent-driven-development for independent implementation tasks and review. Current directory is not a Git checkout; preserve it without introducing Git setup.

Spec: ../specs/2026-09-14-agent-platform-design.md

- [x] Tools/connections backend: new tool_service.py/tool_api.py, encrypted tenant connections, HTTP/SMTP/Docker adapters, tests. Expose ToolService(store).execute(node_type,config_dict,input_text,tenant_id) async -> string; connection routes /api/connections and GET/POST plus PUT /{id}; TYPES/CONFIGS or node specs metadata supplied to parent. TENANT_MODELS and ToolService.check(config,tenant) for execution validation. Memory service read/write can be parent-owned.
- [x] Vector backend: new vector_service.py/vector_api.py/vector_adapters.py, real ingestion/embedding/indexing/search with tenant paths and sources, tests. Expose VectorService(store).serve() async; retrieve(resource_id,query,options,tenant) async -> list canonical sources; verify_sources(list,tenant) -> canonical sources; resource(id,tenant), resources(tenant); TENANT_MODELS. Routes /api/vector-resources GET/POST; /{id}/files GET/POST raw binary filename query; /api/vector-files/{id}/file GET, /remove and /retry POST; /api/vector-capabilities GET. Parent worker starts serve loop and routes installation. Vector config resource_id only authoritative; backend is vector_faiss/chroma/elasticsearch/pinecone node type.
- [x] Frontend redesign: exact palette/categories, typed four-port nodes, attachment-aware connecting, Agent inspector and model params, tool connection/settings inspector, vector upload cards/resource setup and retrieval/query options, source panels, tests. New types agent,tool_http,tool_email,tool_jira,tool_confluence,tool_github,tool_python,vector_faiss,vector_chroma,vector_elasticsearch,vector_pinecone,retrieve,query. Attachment/flow contracts per spec. New runtime inputs agent/tool nodes input:string; retrieve/query query:string; outputs agent text/provider, tools text, retrieve query/context/sources, query text/provider/sources; vectors no flow inputs/outputs.
- [x] Parent runtime: register nodes, add bounded agent/tool/delegate execution, extend flow compiler validation and edge model, enforce permissions/API/resume rules, hyperparameters per provider, workspace memory, integrate service schema/auth/worker/API. Use test-first regressions for every changed execution contract.
- [x] Integrate and review: run old/new tests, local real vector/agent smoke and remote mock adapters, frontend checks; independent code review/fixes; backup/restart Relay; document supported operations/filetypes/backends and resource prerequisites. No paid calls/external writes.

Ruling: connections and vector resources are workspace-owned; all users currently own their workspace. Remote write tests use mock transports. Tool consent is a saved explicit node setting, not an automatic permission implied by attaching a tool. Python execution must not fall back to host exec.


Completed 2026-09-14. Evidence:
- Backend: 129 tests passed; frontend: 17 tests passed, typecheck/lint/build passed.
- Dependency consistency: pip check passed.
- Real local smoke: installed embeddinggemma embeddings, FAISS and Chroma persistence/search (similarity, keyword, hybrid, RRF), Ollama Query/Agent generation, canonical sources and Docker Python execution passed in temporary storage.
- Real agent tool probe: llama3.2:3b invoked Python and returned its output, but argument adherence was inconsistent. qwen2.5:0.5b repeated calls until the bounded limit. Documented model-quality limitations; deterministic tool routing/delegation tests use mocked model replies.
- PostgreSQL isolated-schema verification passed including new vector tables, ingestion/removal, encrypted connections and agent memory; container restored to stopped.
- Independent runtime and tools/vector review completed. Follow-up fixes cover retained source dependencies on agent resume, duplicate logical paths, deferred segment cleanup and negative-cosine fusion.
- Database backup: .data/backups/before-agent-platform-20260914T192817Z.db. Relay restarted with the new schema/API/worker and frontend at http://127.0.0.1:3000.
- No paid model calls or real external connector writes; no browser interaction/visual QA. Remote adapters verified using mock responses. README includes supported formats, backend prerequisites and testing steps.
