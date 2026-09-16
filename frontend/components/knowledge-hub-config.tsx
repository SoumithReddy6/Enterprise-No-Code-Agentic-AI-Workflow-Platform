'use client';
import { ConfigField, type ToolConnection } from './platform-settings';
import type {
  KBConfig,
  KBCapabilities,
  SearchOptions,
} from '@/lib/knowledge-hub';
export function SearchFields({
  value,
  onChange,
  disabled,
}: {
  value: SearchOptions;
  onChange: (v: SearchOptions) => void;
  disabled: boolean;
}) {
  return (
    <div className="inspector-form knowledge-config-grid">
      <ConfigField
        name="mode"
        property={{
          title: 'Search technique',
          enum: ['similarity', 'keyword', 'hybrid', 'rrf'],
        }}
        value={value.mode}
        disabled={disabled}
        onChange={(v) =>
          onChange({ ...value, mode: v as SearchOptions['mode'] })
        }
      />
      {(
        [
          'top_k',
          'candidate_k',
          'score_threshold',
          'rrf_k',
          'vector_weight',
        ] as const
      ).map((key) => (
        <ConfigField
          key={key}
          name={key}
          property={{
            title: {
              top_k: 'Results to return',
              candidate_k: 'Candidate pool',
              score_threshold: 'Minimum score (optional)',
              rrf_k: 'RRF constant',
              vector_weight: 'Vector weight',
            }[key],
            type: ['score_threshold', 'vector_weight'].includes(key)
              ? 'number'
              : 'integer',
            minimum:
              key === 'vector_weight'
                ? 0
                : key === 'score_threshold'
                  ? undefined
                  : 1,
            maximum:
              key === 'top_k'
                ? 20
                : key === 'candidate_k'
                  ? 100
                  : key === 'vector_weight'
                    ? 1
                    : key === 'rrf_k'
                      ? 1000
                      : undefined,
          }}
          value={value[key]}
          disabled={disabled}
          onChange={(v) => onChange({ ...value, [key]: v ?? null })}
        />
      ))}
      {['filename', 'document_id'].map((key) => (
        <label key={key}>
          Filter by {key}
          <input
            disabled={disabled}
            maxLength={240}
            value={value.filter[key] || ''}
            onChange={(e) => {
              const filter = { ...value.filter };
              if (e.target.value) filter[key] = e.target.value;
              else delete filter[key];
              onChange({ ...value, filter });
            }}
          />
        </label>
      ))}
    </div>
  );
}
export default function KnowledgeConfig({
  value,
  onChange,
  caps,
  connections,
  disabled,
}: {
  value: KBConfig;
  onChange: (v: KBConfig) => void;
  caps: KBCapabilities;
  connections: ToolConnection[];
  disabled: boolean;
}) {
  const set = (key: keyof KBConfig, v: unknown) =>
    onChange({ ...value, [key]: v });
  return (
    <div className="inspector-form knowledge-config-grid">
      <label>
        Storage backend
        <select
          disabled={disabled}
          value={value.backend}
          onChange={(e) =>
            onChange({
              ...value,
              backend: e.target.value,
              index_method:
                caps.backends[e.target.value]?.index_methods?.[0] || '',
              connection_id: '',
            })
          }
        >
          {Object.keys(caps.backends).map((b) => (
            <option key={b}>{b}</option>
          ))}
        </select>
      </label>
      <label>
        Installed embedding model
        <select
          disabled={disabled}
          value={value.embedding_model}
          onChange={(e) =>
            onChange({
              ...value,
              embedding_model: e.target.value,
              embedding_digest: '',
            })
          }
        >
          <option value="">Keyword only (no embeddings)</option>
          {caps.embedding_models.map((m) => {
            const name = typeof m === 'string' ? m : m.name;
            return <option key={name}>{name}</option>;
          })}
        </select>
      </label>
      {(
        ['storage_path', 'chunk_size', 'chunk_overlap', 'index_name'] as const
      ).map((key) => (
        <ConfigField
          key={key}
          name={key}
          property={{
            type: typeof value[key] === 'number' ? 'integer' : 'string',
            title: {
              storage_path: 'Storage path',
              chunk_size: 'Chunk size (characters)',
              chunk_overlap: 'Chunk overlap (characters)',
              index_name: 'Index name',
            }[key],
            minimum: key === 'chunk_size' ? 100 : 0,
            maximum:
              key === 'chunk_size'
                ? 8000
                : key === 'chunk_overlap'
                  ? Math.min(4000, value.chunk_size - 1)
                  : undefined,
          }}
          value={value[key]}
          disabled={disabled}
          onChange={(v) => set(key, v)}
        />
      ))}
      <ConfigField
        name="chunking"
        property={{ title: 'Chunking strategy', enum: ['fixed', 'paragraph'] }}
        value={value.chunking}
        disabled={disabled}
        onChange={(v) => set('chunking', v)}
      />
      <ConfigField
        name="index_method"
        property={{
          title: 'Indexing method',
          enum: caps.backends[value.backend]?.index_methods || [
            value.index_method,
          ],
        }}
        value={value.index_method}
        disabled={disabled}
        onChange={(v) => set('index_method', v)}
      />
      {['elasticsearch', 'pinecone'].includes(value.backend) && (
        <label>
          Shared connection
          <select
            disabled={disabled}
            value={value.connection_id}
            onChange={(e) => set('connection_id', e.target.value)}
          >
            <option value="">Choose connection</option>
            {connections
              .filter((c) => c.provider === value.backend)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
          <span className="helper">Manage credentials in Connections.</span>
        </label>
      )}
      <details>
        <summary>Default retrieval options</summary>
        <SearchFields
          value={value.search_defaults}
          onChange={(v) => set('search_defaults', v)}
          disabled={disabled}
        />
      </details>
    </div>
  );
}
