# KB RPC and public contract (2026-09-15)
Shared immutable interface for independent implementers. Domain calls raise KeyError for missing/foreign, ValueError for invalid/conflict, internal server wrappers map errors. tenant always trusted server identity. IDs strings <=64. Times UNIX seconds. Configuration dict: backend faiss|elasticsearch|pinecone (Chroma retired; rebuild with FAISS), storage_path logical slug, embedding_model string (blank permits legacy keyword-only), embedding_digest string default'', chunking fixed|paragraph, chunk_size1200,chunk_overlap200,index_method backendcapdefault,connection_id'',index_name'', search_defaults RetrievalOptions dict. Private connection resolved by Relay from tenant encrypted connections, sent in payload field connection; never returned in public metadata. Fernet secret_key shared service config, encrypt private connection at rest.

## Management
Management(database_url, root, secret_key). call(action,tenant,payload) sync JSON serializable.
- create {name,description='',config,connection? ,idempotency_key?}->KB
- list {}->[KB]
- get {kb_id}->KB detail with documents, jobs, versions (all metadata only)
- update {kb_id,name?,description?}->KB
- rebuild {kb_id,config,connection?}->KB (newconfig pending; oldactive search remains)
- upload {kb_id,filename,content_b64,idempotency_key,replace_document_id?}->Document (eachfile original stored durably, job registered; duplicate samekey+bytes returns same document; differentpayload samekey reject)
- retry {kb_id,document_id}->KB (new attempt)
- remove_document {kb_id,document_id}->KB (mark removed immediately, pending snapshot updated; oldsource must fail authorization)
- delete {kb_id}-> {status:'deleted'} (tombstone remains untilcleanup)
- cancel {kb_id}->KB (cancel pending jobs, preserve active)
- resolve {kb_id}-> {kb_id,version,segment_id,config,connection,documents:[{id,filename,content_hash}],status} only active searchable version; fail ValueError if no active; document list excludes removed docs, config privateconnection only internal
- verify {kb_id,version,document_ids:[...]}->{valid:true}; ownership + document notremoved + matching documentcontent version; retainedpreviousversions allowed for savedworkflow provenance until document removed/replaced; tests require removed denied
- claim {} -> null or Job; tenant ignored ONLY for trusted worker. Job {id,kb_id,tenant_id,attempt_id,version,config,connection,documents:[{id,filename,content_hash}], lease_seconds:30}; renew owner attempt id.
- artifact {job_id,attempt_id,document_id}-> {content_b64,filename}; validateslivelease
- progress {job_id,attempt_id,stage,completed?,total?}->{ok:true}; renew {job_id,attempt_id}->{ok:true}; fail {job_id,attempt_id,error}->{ok:true}; publish {job_id,attempt_id,segment_id,chunk_count,embedding_digest?,dimensions?}->{ok:true}; fenced, idempotent successfulpublish allowed; activeversion update atomic.
- cleanup_list {} ->[{kb_id,tenant_id,segment_id}] retired/cancelled/failedattempts anddeletedKBsegments, repeatedtombstones; cleanup_result {segment_id,ok,error?}->{ok:true}; metrics visible injobs/detail. neverretire active or livepending.
- build_status {kb_id,segment_id}->{allowed:bool}; for search to defend lateupsert; parent RPC coordinator invokes; service-domain Search also own tombstone.
KB={id,name,description,config,status,active_version:int|null,pending_version:int|null,document_count,chunk_count,created_at,updated_at}; config publicexcludesconnectionsecret. status empty|indexing|ready|degraded|failed|deleted. Document={id,filename,status,content_hash,bytes,error,created_at}, status queued|processing|ready|failed|removed. Jobs={id,attempt_id,version,status,stage,error,cleanup_status,...}. Rebuild version full snapshot of live docs; preserve active whileprocessing. Superseding mutation may invalidate previous pending job, safely start fresh version; do not publish stale snapshots.

## Search
Search(database_url,root,secret_key). async call(action,tenant,payload).
- build {kb_id,version,segment_id,config,connection,documents:[{id,filename,content_hash}],chunks:[{id,document_id,ordinal,page,text}],vectors:[float[]]} ->{segment_id,chunk_count}; vectors=[] valid keywordonly; uniqueattempt id inventory persistedBEFOREupsert, stagingnot searchable until buildready; repeated identicalbuild safe, conflictingpayload rejected. Global ordinal for FAISS. KB document/page metadata persisted for sourcehydration.
- search {kb_id,version,segment_id,document_ids:[ids],query,query_vector:[],options:RetrievalOptions} -> sources array max20; only readysegment exacttenantkbversion; documentids authoritative allowedsubset supplied by management. Sources {id,knowledge_base_id,version,document_id,filename,page,text,score,url:'/api/knowledge-bases/KB/documents/DOC/file'}.
- verify {sources:[<=20]} -> canonical sources; validates chunkidentity/tenant and fields, serviceown metadata; parent additionally management.verify each KBversion/doc beforeuse/download.
- cleanup {kb_id,segment_id,retain_provenance:false}-> {ok:true}; persist tombstone first; rejectbuild afterward; repeats deletevectorsandchunks evenlatewrites, failures remain pending; never expose incomplete builds.
- cleanup_pending {}->{...}; retries stored failures/retiredtombstones, cleanup counters returned. Preserve tombstones to catch delayedwriters.
- stats {kb_id}-> safe metadata only.
Own SQL Base and indexstore. Persist inverted postings for keyword mode rather than retokenize entirecorpus eachquery. Concurrency persegment locking, idempotent build hash. Realvectoradapters fromexisting module, reuse capability limits. Do not access managementSQL.

## Public gateway /api/knowledge-bases
GET '' list; POST '' create{name,description,config}; GET /capabilities {backends,embedding_models,extensions,max_file_bytes}; GET /{id} detail; PUT /{id} metadata; POST /{id}/rebuild {config}; POST /{id}/cancel; POST /{id}/delete.
POST /{id}/documents?filename=...&replace_document_id=... raw filebody + Idempotency-Key header ->202Document. GET /{id}/documents/{doc}/file downloadsoriginal (management new download {kb_id,document_id}->{filename,content_b64} tenantchecked). POST /{id}/documents/{doc}/retry or /remove. POST /{id}/search {query,options?}->{query,knowledge_base_id,version,sources,status:'results'|'no_matches',elapsed_ms}. GET /{id}/documents/{doc}/preview -> parent may provide extracted activechunks fromsearch via new preview action {kb_id,segment_id,document_id,limit:10}; implementSearch preview returns sources (score0) and total count. Usersees clear backend failure503, not no_matches.
Config GUI fields exactabove. Retrieve/Query config knowledge_base_id newlyadded; oldresource_id/storage_path hiddencompat. New sources accepted by frontend lib sourceparser; urlgeneratedonlybounded IDs. List old /knowledge and /vector-resources separatelegacydata until parent migration adapter exposes innewlist via import.

## Internal HTTP
POST /rpc with {action,tenant,payload}, header X-Relay-Service-Key; constant-time token compare. No arbitrary method dispatch. /health unauthenticated statusonly. Ports management8011/search8012; workeroutboundonly. Config KB_MANAGEMENT_URL,KB_SEARCH_URL,KB_SERVICE_KEY,KB_MANAGEMENT_DATABASE_URL,KB_SEARCH_DATABASE_URL,KB_DATA_DIR. Clientuses trust_env=False. Node runtime canonicalJSON envelope storedin retrieve.context {question,passages,sources,knowledge_base_id,version}; query output remains sources JSON; Agent consumes envelope unchanged.


Final cleanup contract: managementcleanup_list includes retain_provenance=true forretiredsuccessfulversions, false forfailed/deleted. Search true archivescanonicalmetadata forverifyonly whiledeleting vectors/postings; false irreversiblypurges. Management stillauthorizesallsourceuse byKBversion+documentrevocation. cleanup_list returns onlydue jobs; next_cleanup_at persists, successfuldelete rechecked300sec, failuresbackoff5..300. Ingestioncalls pertenantcleanup directly; no global systemtenantsearchcleanup scan.
