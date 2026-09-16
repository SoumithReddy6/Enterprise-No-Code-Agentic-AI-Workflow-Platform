# Agent, tool and vector platform redesign

Approved by the user with “proceed” after the proposed design. Implement in the current workspace, preserving old JSON workflows and data.

## Library and configuration
Input: chat input/manual trigger. Agent: one Agent node with nine selectable roles. Tools: HTTP/REST, Email, Jira, Confluence, GitHub, Python; multiple instances supported. VectorDB: Elasticsearch, FAISS, ChromaDB, Pinecone. Retrieval: Retrieve and Query. Control: Condition. Output: Response. Legacy prompt/llm/PDF retrieval/grounded nodes remain loadable but hidden in new palette.

Agent ports: flow input top, output bottom, tools left (multiple attachments), specialists right. The right inspector edits role, enabled model, supported temperature/top-p/max output tokens, system and user prompts. Shared encrypted connections and model allowlists stay left. Specialist agents are callable delegates; bottom continues the main flow.

## Edges and execution
Keep workflow v1 compatible; add edge kind (flow default, tool, agent, store) and targetHandle. Tool edges run from tool/retrieve/query to agent (targetHandle tools). Agent edges run from parent agent (sourceHandle agents) to specialist. Store edges run from vector node to retrieve/query (targetHandle store). Only flow edges enter LangGraph; attachment dependencies are validated separately for type, ownership, cycles and one parent for each specialist. Attached nodes may omit flow input bindings because calls supply input. Disconnected resources cannot execute.

Agents use a bounded tool-call loop (maximum six calls, specialist depth three), with explicit action/final JSON supported across the existing text providers. Plain model responses are final answers. Tools are attached configured capabilities, never arbitrary tool names supplied by the model. Router chooses a specialist using the same mechanism. Memory Agent uses bounded workspace-scoped conversation memory selected by memory_key; other roles supply concrete editable instruction presets. Tool/specialist events are distinct from flow checkpoints so attachments do not accidentally replay under a different call. Automatic resume is blocked after an interrupted write-capable tool to avoid blindly repeating a side effect; operators can start a fresh run after checking the external result.

## Tools
Use reusable encrypted connections for provider-specific endpoints/auth; no secrets in workflow JSON. Provide real HTTP adapters for GitHub/Jira/Confluence and SMTP Email, with explicit configured operations. Read operations work immediately with configured credentials; write/send operations require node-level enable_writes set deliberately in the UI. No external writes are made while implementing/testing. Python runs only inside a resource-limited Docker container with no network or host mounts; fail actionably when Docker/image is unavailable. HTTP addresses are connection-level, redirects disabled, timeout/output limits enforced.

## Vector ingestion and search
Each vector node selects a persistent workspace resource with backend, logical storage_path, chunk strategy/size/overlap, embedding model and backend-supported indexing strategy. Upload button on each vector node supports multiple files. Accept all file picker types but parse supported formats explicitly (PDF incl existing OCR, UTF-8 text/Markdown/CSV/JSON/HTML and DOCX initially), rejecting unsupported binaries with an explanation; do not claim universal parsing.

Use real Ollama embeddings (user-selected installed model) for local free testing, real FAISS/Chroma local stores, Elasticsearch/Pinecone remote adapters requiring a configured connection. Logical paths map to tenant-owned directories or remote namespaces; never arbitrary host filesystem paths. Different paths remain separate collections. Persist immutable ingestion settings per resource; create a new resource to change dimensions/chunking/index layout. Index asynchronously, publish documents only on success, and enforce bounds/tenant authorization. No provider/model downloads automatically.

Retrieve reuses one service for similarity, keyword, hybrid weighted fusion and RRF; parameters include top_k, candidate_k, score threshold, metadata filters and RRF k. Backend capabilities restrict unsupported index methods. Query calls the same retrieve service then generates a sourced answer using an allowed model and parameters. Tenant-owned canonical source IDs validate citations and authorize original-file downloads. Storage-path selection is explicit; no search across other paths implicitly.

## Acceptance
Tests cover graph validation/attachment cycles, roles and hyperparameters, bounded delegation/tool loops, credentials and resource ownership, write opt-in/resume behavior, Python isolation command, vector paths/chunking/filters/fusion/source validity, uploads and compatibility. Integration tests use local FAISS, Chroma and Ollama when installed, simulated remote services/SMTP (no real emails/issues/commits). Existing suites, typecheck/lint/build and independent review must pass. Leave updated local app available for user testing and disclose untested remote credentials/services.
