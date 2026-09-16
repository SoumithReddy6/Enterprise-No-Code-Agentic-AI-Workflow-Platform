# Workflow Foundation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deliver the approved local visual AI workflow foundation.
**Architecture:** Canonical workflow JSON is validated against a plugin registry and compiled to LangGraph. FastAPI stores workflow/run records; React Flow edits the same document.
**Tech Stack:** React, TypeScript, React Flow, FastAPI, LangGraph, SQLAlchemy, PostgreSQL/SQLite.
**Spec:** docs/superpowers/specs/2026-09-07-workflow-foundation-design.md

## Global constraints
- Local single-user foundation; no enterprise readiness claim.
- Credentials are referenced by ID and encrypted separately.
- Demo LLM is explicitly labeled.
- Unsupported graph capabilities fail validation.

## Task 1: Workflow contracts and compiler
Files: backend/app/models.py, registry.py, compiler.py; backend/tests/test_compiler.py.
Interfaces: Workflow.model_validate(dict), validate_workflow(Workflow)->list[str], compile_workflow(Workflow, credential_resolver, emit)->CompiledWorkflow. CompiledWorkflow.source is the canonical JSON; graph.ainvoke accepts {values: {}}.
- [x] Write failing tests for prompt substitution, branch selection, invalid graphs and preservation of source layout.
- [x] Run `.venv/bin/python -m pytest backend/tests/test_compiler.py` and confirm missing behavior.
- [x] Implement port validation, ancestor analysis and the real LangGraph compiler.
- [x] Run tests; inspect outputs for the expected result and skipped branch absence.

## Task 2: Persistence, credentials and run API
Files: backend/app/storage.py, main.py; backend/tests/test_api.py.
Interfaces: GET/POST /api/workflows, PUT/GET /api/workflows/{id}, POST /api/validate, GET /api/nodes, POST/GET /api/credentials, POST /api/runs, GET /api/runs/{id}, GET /api/runs/{id}/events, POST /api/runs/{id}/cancel.
- [x] Write failing integration tests for durable round-trip, event history and encrypted credential storage.
- [x] Implement relational persistence and bounded background runs with persisted events.
- [x] Run `.venv/bin/python -m pytest backend/tests`.

## Task 3: Visual editor and handoff
Files: frontend/app/page.tsx, frontend/app/globals.css, frontend/lib/workflow.ts, frontend/components/workflow-editor.tsx, README.md, compose.yaml, scripts/dev.sh.
- [x] Implement a canvas with input/prompt/demo LLM/response template, palette, inspector and save/load.
- [x] Wire validation, credential entry, run inspector and cancellation to backend.
- [x] Build frontend and run API smoke workflow.
- [x] Document local startup, PostgreSQL mode, credential setup and explicit limitations.
