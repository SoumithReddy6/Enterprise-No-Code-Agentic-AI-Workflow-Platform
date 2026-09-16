# Workflow foundation

Approved scope: local end-to-end foundation of the attached enterprise workflow platform.

React/TypeScript/React Flow provides a node palette, draggable canvas, configuration inspector, JSON import/export, server save/load, validation and a live run inspector. FastAPI owns validation and execution. A versioned canonical JSON document preserves canvas positions and configuration; a compiler builds LangGraph from it without discarding the source representation. Reverse export returns that canonical document, not an attempted reconstruction from LangGraph internals.

Initial node registry: chat input, manual input, prompt template, LLM, condition, response. Every node declares configuration and required input/output ports. Bindings reference upstream node output ports. Workflows have one trigger, acyclic paths, one successor per ordinary node and true/false successors per condition. Parallel joins, loops, nested workflows, approval and distributed execution are future compiler capabilities; unsupported graphs are rejected explicitly.

The backend persists workflows, run snapshots and ordered run events through SQLAlchemy. PostgreSQL is the deployment database. SQLite is an explicit single-user local option. Runs execute in the API process, publish persisted node-start/success/failure events and have cancellation. Interrupted runs become failed on restart. No resumable checkpoints or multi-tenant authentication are claimed in this release.

LLM execution supports an explicit deterministic demo provider and OpenAI via separately encrypted credentials. Workflow JSON stores credential IDs only. Keys stay on the backend. Demo mode never implies a real model response. Node failures terminate the run and appear in the inspector. API error responses do not expose provider response bodies or secrets.

Validation rejects unsupported versions/types, duplicate IDs, invalid links, malformed configs, inline secrets, missing required inputs, bindings outside guaranteed ancestors, missing branch labels, cycles, unreachable nodes and non-output terminal nodes. Limits bound graph size, input length and run duration.

Verification: compiler and real LangGraph execution tests; validation edge cases; credential encryption/redaction; API save/load, run completion, event ordering, cancellation and persistence; frontend TypeScript/build checks. An API smoke run verifies the assembled local app. Real paid API calls require a credential and are not part of automatic verification.
