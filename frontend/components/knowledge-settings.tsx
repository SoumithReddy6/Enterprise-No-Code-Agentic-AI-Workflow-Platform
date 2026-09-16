'use client';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/workflow';
import type { KnowledgeBase, KnowledgeDocument } from '@/lib/knowledge';
export default function KnowledgeSettings({
  bases,
  refresh,
  onStart,
  disabled,
}: {
  bases: KnowledgeBase[];
  refresh: () => Promise<void>;
  onStart: (id: string) => void;
  disabled: boolean;
}) {
  const [selected, setSelected] = useState('');
  const baseId = bases.some((b) => b.id === selected)
    ? selected
    : bases[0]?.id || '';
  const [name, setName] = useState('');
  const [documentResult, setDocumentResult] = useState<{
    baseId: string;
    documents: KnowledgeDocument[];
  }>({ baseId: '', documents: [] });
  const documents =
    documentResult.baseId === baseId ? documentResult.documents : [];
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (!baseId) return;
      try {
        const result = await api<KnowledgeDocument[]>(
          `/knowledge/${encodeURIComponent(baseId)}/documents`,
        );
        if (cancelled) return;
        setDocumentResult({ baseId, documents: result });
        if (result.some((d) => ['queued', 'processing'].includes(d.status)))
          timer = setTimeout(poll, 2000);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    }
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [baseId, revision]);
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await action();
      setRevision((r) => r + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="knowledge-settings">
      <div className="panel-title">Knowledge</div>
      <p className="helper">
        Upload PDFs and ask questions with page references.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void perform(async () => {
            const base = await api<KnowledgeBase>('/knowledge', 'POST', {
              name: name.trim(),
            });
            await refresh();
            setSelected(base.id);
            setName('');
          });
        }}
      >
        <label>
          New knowledge base
          <input
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Research papers"
          />
        </label>
        <button className="secondary-button" disabled={busy || !name.trim()}>
          Create base
        </button>
      </form>
      <label>
        Knowledge base
        <select
          value={baseId}
          disabled={busy}
          onChange={(e) => {
            setSelected(e.target.value);
            setError('');
          }}
        >
          <option value="" disabled>
            Choose a base
          </option>
          {bases.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
      </label>
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          e.target.value = '';
          void perform(async () => {
            for (const file of files) {
              if (file.size > 25 * 1024 * 1024)
                throw new Error(`${file.name}: exceeds the 25 MB limit.`);
              const response = await fetch(
                `/api/knowledge/${encodeURIComponent(baseId)}/documents?filename=${encodeURIComponent(file.name)}`,
                {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/pdf' },
                  body: file,
                },
              );
              if (!response.ok) {
                const data = (await response.json().catch(() => null)) as {
                  detail?: unknown;
                } | null;
                throw new Error(
                  typeof data?.detail === 'string'
                    ? data.detail
                    : `Could not upload ${file.name}.`,
                );
              }
              setRevision((r) => r + 1);
            }
          });
        }}
      />
      <button
        className="secondary-button"
        disabled={busy || !baseId}
        onClick={() => input.current?.click()}
      >
        {busy ? 'Working…' : 'Upload PDFs'}
      </button>
      <p className="helper">
        25 MB · 200 pages per PDF · 20 active PDFs per base · 50,000 chunks per
        workspace. Printed English scans use local OCR. Handwriting, charts and
        complex tables may not extract reliably. Search matches keywords.
      </p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div aria-live="polite">
        {documents.map((d) => (
          <article key={d.id} className="knowledge-document">
            <strong>{d.filename}</strong>
            <span className="knowledge-status">
              {d.status}
              {d.pages ? ` · ${d.pages} pages` : ''}
            </span>
            {d.error && <p className="error-text">{d.error}</p>}
            <div className="knowledge-actions">
              {d.status === 'failed' && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      await api(
                        `/documents/${encodeURIComponent(d.id)}/retry`,
                        'POST',
                      );
                    })
                  }
                >
                  Retry
                </button>
              )}
              {d.status !== 'removed' && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      await api(
                        `/documents/${encodeURIComponent(d.id)}/remove`,
                        'POST',
                      );
                    })
                  }
                >
                  Remove
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      <p className="helper">
        Removing a PDF stops retrieval, downloads and resumes that need it.
        Existing run history may retain recorded excerpts.
      </p>
      <button
        className="primary-button"
        disabled={disabled || !baseId || busy}
        onClick={() => onStart(baseId)}
      >
        Create PDF question workflow
      </button>
      <p className="helper">
        Choose an enabled answer model on the right before running.
      </p>
    </div>
  );
}
