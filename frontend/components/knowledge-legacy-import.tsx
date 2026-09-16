'use client';
import { useEffect, useState } from 'react';
import { api } from '@/lib/workflow';
import type { KnowledgeHubBase } from '@/lib/knowledge-hub';
type LegacyResource = {
  kind: 'pdf' | 'vector';
  id: string;
  name: string;
  document_count: number;
};
export default function KnowledgeLegacyImport({
  disabled,
  revision,
  onImport,
}: {
  disabled: boolean;
  revision: number;
  onImport: (load: () => Promise<KnowledgeHubBase>) => void;
}) {
  const [resources, setResources] = useState<LegacyResource[]>([]);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    void api<LegacyResource[]>('/knowledge-bases/legacy-resources')
      .then((items) => {
        if (active) {
          setResources(items);
          setError('');
        }
      })
      .catch((e) => {
        if (active) setError((e as Error).message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [revision, retry]);
  const resource = resources.find((r) => `${r.kind}:${r.id}` === selected);
  return (
    <details className="knowledge-legacy-import">
      <summary>Import existing knowledge</summary>
      <p className="helper">
        Copy documents from a legacy PDF base or vector resource into a named
        knowledge base. Original workflows and histories remain available.
      </p>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <label>
        Legacy resource
        <select
          disabled={disabled || loading}
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
        >
          <option value="">
            {loading ? 'Loading resources…' : 'Choose a resource'}
          </option>
          {resources.map((r) => (
            <option key={`${r.kind}:${r.id}`} value={`${r.kind}:${r.id}`}>
              {r.kind === 'pdf' ? 'PDF' : 'Vector'} · {r.name} ·{' '}
              {r.document_count} documents
            </option>
          ))}
        </select>
      </label>
      {!loading && !error && !resources.length && (
        <p className="helper">No legacy resources available.</p>
      )}
      <button
        disabled={disabled || loading || !resource}
        onClick={() => {
          if (resource)
            onImport(() =>
              api<KnowledgeHubBase>('/knowledge-bases/import-legacy', 'POST', {
                kind: resource.kind,
                id: resource.id,
              }),
            );
        }}
      >
        Import copy
      </button>
      <button
        disabled={disabled || loading}
        onClick={() => {
          setLoading(true);
          setRetry((v) => v + 1);
        }}
      >
        Refresh resources
      </button>
      <p className="helper">Repeating an import resumes its existing copy.</p>
    </details>
  );
}
