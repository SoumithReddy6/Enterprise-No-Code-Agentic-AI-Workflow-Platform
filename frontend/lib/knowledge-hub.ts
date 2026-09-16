export type SearchOptions = {
  mode: 'similarity' | 'keyword' | 'hybrid' | 'rrf';
  top_k: number;
  candidate_k: number;
  score_threshold: number | null;
  rrf_k: number;
  vector_weight: number;
  filter: Record<string, string>;
};
export type KBConfig = {
  backend: string;
  storage_path: string;
  embedding_model: string;
  embedding_digest?: string;
  chunking: string;
  chunk_size: number;
  chunk_overlap: number;
  index_method: string;
  connection_id: string;
  index_name: string;
  search_defaults: SearchOptions;
};
export type KBDocument = {
  id: string;
  filename: string;
  status: string;
  bytes: number;
  error?: string;
};
export type KnowledgeHubBase = {
  id: string;
  name: string;
  description: string;
  config: KBConfig;
  status: string;
  active_version: number | null;
  pending_version: number | null;
  document_count: number;
  chunk_count: number;
  documents?: KBDocument[];
  jobs?: {
    id: string;
    version: number;
    status: string;
    stage: string;
    error?: string;
    cleanup_status?: string;
    completed?: number;
    total?: number;
  }[];
  versions?: unknown[];
};
export type KBCapabilities = {
  backends: Record<string, { index_methods?: string[] }>;
  embedding_models: (string | { name: string })[];
  extensions?: string[];
  max_file_bytes?: number;
};
export const defaultSearch: SearchOptions = {
  mode: 'keyword',
  top_k: 4,
  candidate_k: 20,
  score_threshold: null,
  rrf_k: 60,
  vector_weight: 0.5,
  filter: {},
};
export const defaultKBConfig: KBConfig = {
  backend: 'faiss',
  storage_path: 'default',
  embedding_model: '',
  chunking: 'fixed',
  chunk_size: 1200,
  chunk_overlap: 200,
  index_method: 'flat',
  connection_id: '',
  index_name: '',
  search_defaults: defaultSearch,
};
export function kbPath(id: string) {
  return `/knowledge-bases/${encodeURIComponent(id)}`;
}
export async function uploadDocument(
  kbId: string,
  file: File,
  key: string,
  replaceId?: string,
) {
  const query = new URLSearchParams({ filename: file.name });
  if (replaceId) query.set('replace_document_id', replaceId);
  const response = await fetch(`/api${kbPath(kbId)}/documents?${query}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/octet-stream',
      'Idempotency-Key': key,
    },
    body: file,
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    throw Error(
      typeof error.detail === 'string'
        ? error.detail
        : `Upload failed (${response.status})`,
    );
  }
  return response.json() as Promise<KBDocument>;
}
