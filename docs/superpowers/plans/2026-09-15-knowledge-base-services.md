# Unified Knowledge Base Implementation Plan

> Required: superpowers:subagent-driven-development; execute approved work continuously.

Goal: unify named KB management with three runnable services and durable failure recovery.
Architecture: management RPC at 8011 owns metadata and jobs; search RPC at 8012 owns immutable index artifacts; ingestion worker claims management jobs and invokes search. Relay gateway translates authenticated public requests; same RPC contracts used by services. Separate databases, original blobs managed by management. Existing vector APIs remain legacy compatibility only.
Tech: FastAPI, SQLAlchemy, PostgreSQL/SQLite, httpx, Ollama, existing vector adapters, React.
Spec: ../specs/2026-09-15-knowledge-base-services.md

Global constraints: 25MB/file,200pages,20 docs/KB,50kchunks/workspace,20 results. No paidcalls, externalconnectorwrites, automaticmodeldownloads, or replacingexistinguseraccounts. No git repository.

## Task 1: Management domain (agent)
Files kb/management.py, kb/management_models.py, tests/test_kb_management.py. Constructor Management(database_url, root, secret_key). Sync call(action, tenant, payload)->JSON dict/list. Defines own SQL Base. Service operations exact contract in kb/contracts.md. Use serialized writes/advisory locks, atomic jobs and document metadata, encrypted private settings, lease fencing, active manifests, staged version promotion and document lifecycle. Tests failfirst for idempotent uploads, partial indexing, retries, deletionduringprocessing, rebuild while oldactive, cleanupvisibility and tenant denial.
- [x] Implement and run focused tests; review ownership and atomic transitions.

## Task 2: Search/index domain (agent)
Files kb/search.py, kb/search_models.py, tests/test_kb_search.py. Constructor Search(database_url,root). Async call(action,tenant,payload)->JSON. Independent SQL Base. Build attempt inventory before sideeffects; immutable artifact publication; idempotent retries, persistent cleanup tombstones and indexing cancellation races; real persistent keyword postings + existing vector adapters. Contracts in kb/contracts.md. Tests failfirst staging invisibility/cross-tenant/partialupsert/latewriter/realFAISSChroma/keywordfusion/cleanupfailure. Model embedding is ingestion/gateway owned; search accepts vectors.
- [x] Implement and run focused tests; review cleanup and source contract.

## Task 3: UI (agent)
Files frontend/components/knowledge-hub.tsx, knowledge-settings.tsx ifneeded, platform-settings.tsx, workflow-editor.tsx, frontend/lib/knowledge-hub.ts, relevant tests. Public endpoints and types in kb/contracts.md. Replace left Knowledge tab with hub; connection tab onlyconnections; hidevector nodes via backendhidden; retrieve/query knowledge_base_id selection (newKB list separate legacyKnowledge IDs). Searchplayground, progresspolling,racesafeforms,errorstates,rebuild/rename,documentreplace/retry/remove,cancel. Agent input default should bind retrieve.context JSON envelope automatically. Preserve old bindings and legacyKB flows. No browser calls.
- [x] Implement and test frontend unit/typecheck/lint/build.

## Task 4: Parent service plumbing/runtime/migration
Files kb/contracts.md, rpc.py, management_app.py, search_app.py, ingestion.py, embedding.py; kb_gateway.py; main.py/worker.py/platform_* / registry.py; scripts/dev.py and examples/docs. Implement secret-authenticated allowlisted RPC; external user routes tenant-bound; ingestion viaRPC only; model fingerprint, stages, leases,cleanuporchestration; launchservices beforemain. Versioned retrieval source verification and rootagent provenance; no-vector directKB retrieval alongside legacy. Read-only legacy import copies olddocs idempotently to newKB asuser-scoped operation; keep oldhistory/endpoints.
- [x] Wire services, write integration/fault tests; preserve old tests.

## Task 5: Review and verification
- [x] Independent domain/runtime review and fixes. Wholebackend/front checks. Multi-process real local endtoend, worker recovery, PostgreSQL separateowneddata test. BackupuserDB and restart verifiedonlyprojectservices. READMEarchitecture/contracts/recovery/testing. Mark completion only from freshoutputs.

Ruling: local default uses separate SQLite service databases; production PostgreSQL URLs independentlyconfigured. Logical isolation backed by service-owned tables and no worker directSQL. Avoid introducing extra broker; durable managementjobs are polled viaRPC. Exact operation contract is shared before independent tasks begin.


## Completion evidence — 2026-09-15

-192backendtests passed;20frontendtests passed; typecheck,lint,build passed. After reducing duplicatedevidenceenvelope8focusedworkflow/serviceintegrationtests passed.
-Real3process test scripts/check_knowledge_services.py passed: independentauthenticatedRPCs, Ollamaembeddinggemma, ingestion, foursearchmodes, directnamedKBworkflow, rebuildactivation, provenanceaftercleanup, removalanddownloadrevocation.
-PostgreSQL separateowned-schema check scripts/check_kb_postgres.py passed; testcontainerstoppedafterwards.
-Independentmanagement/search/runtime reviews foundandfixed quotaheadroom, Chroma dimensionchanges, indexingblockingsearch, replacementavailability, archiveprovenance, cleanupbackoff, andwholeworkflowdeadline. Finalscopedreviewapproved.
-CurrentprofileusesSQLite in separate management/searchdatabases; originalsSQLblobs intentionallyatomic withmetadata/job. ConfigurationproductionPostgres documented. Fullsnapshotrebuilds, nointermediatestagecheckpointreuse; faultrecovery restartssafely.
-Backup.data/backups/before-knowledge-services-20260915T184135Z.db. Restartedscripts/dev.py withmanagement8011/search8012/ingestion, gateway8000, workflowworker andfrontend3000. Livehealthand401unauthchecks passed; existingaccount preserved.
-Oldresources importexplicitlyviaresumableImportcopy; noautomaticrewriteofuserworkflows. No realremoteES/Pineconewrites, paidcalls, modeldownloads, orbrowserinteractionQA.
