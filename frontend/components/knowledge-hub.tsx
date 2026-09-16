'use client';
import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/workflow';
import { parseSources, type KnowledgeSource } from '@/lib/knowledge';
import {
  defaultKBConfig,
  defaultSearch,
  kbPath,
  uploadDocument,
  type KBConfig,
  type KBCapabilities,
  type KnowledgeHubBase,
} from '@/lib/knowledge-hub';
import type { ToolConnection } from './platform-settings';
import KnowledgeConfig, { SearchFields } from './knowledge-hub-config';
import SourcePanel from './source-panel';
type Upload = { file: File; key: string; replaceId?: string; error?: string };
export default function KnowledgeHub({
  connections,
  refresh,
  disabled,
}: {
  connections: ToolConnection[];
  refresh: () => Promise<void>;
  disabled: boolean;
}) {
  const [bases, setBases] = useState<KnowledgeHubBase[]>([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<KnowledgeHubBase | null>(null);
  const [caps, setCaps] = useState<KBCapabilities>({
    backends: {},
    embedding_models: [],
  });
  const [tab, setTab] = useState('documents');
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [config, setConfig] = useState<KBConfig>(defaultKBConfig);
  const [options, setOptions] = useState(defaultSearch);
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<{
    sources: KnowledgeSource[];
    label: string;
  } | null>(null);
  const [preview, setPreview] = useState<KnowledgeSource[]>([]);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [capabilityError, setCapabilityError] = useState('');
  const actionPending = useRef(false);
  const [revision, setRevision] = useState(0);
  const selection = useRef(selected);
  useEffect(() => {
    selection.current = selected;
  }, [selected]);
  const locked = busy || disabled;
  useEffect(() => {
    let active = true;
    void api<KBCapabilities>('/knowledge-bases/capabilities')
      .then((value) => {
        if (active) {
          setCaps(value);
          setCapabilityError('');
        }
      })
      .catch((e) => {
        if (active) setCapabilityError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const list = await api<KnowledgeHubBase[]>('/knowledge-bases');
        const next = selected
          ? await api<KnowledgeHubBase>(kbPath(selected))
          : null;
        if (!active) return;
        setBases(list);
        setDetail(next);
        setLoadError('');
      } catch (e) {
        if (active) setLoadError((e as Error).message);
      } finally {
        if (active) timer = setTimeout(poll, 2500);
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [selected, revision]);
  async function act(fn: () => Promise<void>) {
    if (actionPending.current || disabled) return;
    actionPending.current = true;
    setBusy(true);
    setError('');
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRevision((v) => v + 1);
      actionPending.current = false;
      setBusy(false);
    }
  }
  function choose(id: string) {
    selection.current = id;
    setLoadError('');
    setSelected(id);
    setDetail(null);
    setResult(null);
    setPreview([]);
    setUploads([]);
    setCreating(false);
    setTab('documents');
    setError('');
  }
  async function send(items: Upload[]) {
    const failed: Upload[] = [];
    for (const item of items) {
      try {
        if (item.file.size > (caps.max_file_bytes || 25 * 1024 * 1024))
          throw Error('Exceeds the 25 MB file limit');
        await uploadDocument(selected, item.file, item.key, item.replaceId);
      } catch (e) {
        failed.push({ ...item, error: (e as Error).message });
      }
    }
    const attempted = new Set(items.map((item) => item.key));
    setUploads((previous) => [
      ...previous.filter((item) => !attempted.has(item.key)),
      ...failed,
    ]);
    if (failed.length)
      throw Error(
        `${failed.length} upload(s) failed. Retry below to reuse the same upload identity.`,
      );
  }
  function uploadInput(replaceId?: string) {
    return (
      <input
        aria-label={replaceId ? 'Replace document' : 'Upload documents'}
        type="file"
        multiple={!replaceId}
        accept=".pdf,.txt,.md,.markdown,.csv,.json,.html,.htm,.docx"
        disabled={locked}
        onChange={(e) => {
          const items = Array.from(e.target.files || []).map((file) => ({
            file,
            key: crypto.randomUUID(),
            replaceId,
          }));
          e.target.value = '';
          void act(() => send(items));
        }}
      />
    );
  }
  const current = detail?.id === selected ? detail : null;
  return (
    <div className="knowledge-settings knowledge-hub">
      <header className="knowledge-heading">
        <span className="mini-kicker">WORKSPACE KNOWLEDGE</span>
        <h1>Knowledge bases</h1>
        <p>Turn your documents into answers.</p>
      </header>
      <div className="knowledge-selector">
        <label>
          Knowledge base
          <select
            disabled={locked}
            value={selected}
            onChange={(e) => choose(e.target.value)}
          >
            <option value="">Choose a base</option>
            {bases.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name} · {b.status}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={locked}
          className="button primary"
          onClick={() => {
            setCreating(true);
            setName('');
            setDescription('');
            setConfig(defaultKBConfig);
          }}
        >
          New knowledge base
        </button>
      </div>
      {capabilityError && (
        <p role="alert" className="error-text">
          Configuration options unavailable: {capabilityError}
        </p>
      )}
      {loadError && (
        <p role="alert" className="error-text">
          {loadError}
        </p>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {!selected && !creating && !loadError && (
        <div className="knowledge-empty">
          <strong>Your documents, ready for your agents</strong>
          <p>
            Create a knowledge base or choose an existing one above. Then upload
            documents, test retrieval, and select it in a Retrieve node.
          </p>
        </div>
      )}
      {selected && !current && !creating && !loadError && (
        <output>Loading knowledge base…</output>
      )}
      {busy && (
        <output className="knowledge-busy">Working… Please wait.</output>
      )}
      {creating && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void act(async () => {
              const base = await api<KnowledgeHubBase>(
                '/knowledge-bases',
                'POST',
                { name: name.trim(), description, config },
              );
              choose(base.id);
            });
          }}
        >
          <label>
            Name
            <input
              required
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={locked}
            />
          </label>
          <label>
            Description
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={locked}
            />
          </label>
          <KnowledgeConfig
            value={config}
            onChange={setConfig}
            caps={caps}
            connections={connections}
            disabled={locked}
          />
          <button className="button primary" disabled={locked || !name.trim()}>
            Create knowledge base
          </button>
          <button
            type="button"
            disabled={locked}
            onClick={() => setCreating(false)}
          >
            Close
          </button>
        </form>
      )}
      {current && !creating && (
        <>
          <h2 className="knowledge-name">{current.name}</h2>
          <p>{current.description}</p>
          <p className="helper" aria-live="polite">
            {current.status} · active version {current.active_version ?? 'none'}
            {current.pending_version != null
              ? ` · building version ${current.pending_version}`
              : ''}{' '}
            · {current.document_count} documents · {current.chunk_count} chunks
          </p>
          <div
            className="knowledge-actions"
            role="tablist"
            tabIndex={-1}
            aria-label="Knowledge base sections"
            onKeyDown={(event) => {
              const buttons = Array.from(
                event.currentTarget.querySelectorAll<HTMLButtonElement>(
                  '[role="tab"]',
                ),
              );
              const index = buttons.indexOf(event.target as HTMLButtonElement);
              const next =
                event.key === 'ArrowRight'
                  ? (index + 1) % buttons.length
                  : event.key === 'ArrowLeft'
                    ? (index + buttons.length - 1) % buttons.length
                    : event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? buttons.length - 1
                        : -1;
              if (next >= 0) {
                event.preventDefault();
                buttons[next]?.focus();
                buttons[next]?.click();
              }
            }}
          >
            {['documents', 'settings', 'search', 'activity'].map((t) => (
              <button
                role="tab"
                id={`knowledge-tab-${t}`}
                aria-controls="knowledge-tab-panel"
                tabIndex={tab === t ? 0 : -1}
                aria-selected={tab === t}
                key={t}
                disabled={locked}
                onClick={() => {
                  setTab(t);
                  if (t === 'settings') {
                    setName(current.name);
                    setDescription(current.description);
                    setConfig({
                      ...defaultKBConfig,
                      ...current.config,
                      search_defaults: {
                        ...defaultSearch,
                        ...current.config.search_defaults,
                      },
                    });
                  }
                  if (t === 'search')
                    setOptions({
                      ...defaultSearch,
                      ...current.config.search_defaults,
                    });
                }}
              >
                {
                  {
                    documents: 'Documents',
                    settings: 'Settings',
                    search: 'Test search',
                    activity: 'Activity',
                  }[t]
                }
              </button>
            ))}
          </div>
          <div
            id="knowledge-tab-panel"
            className="knowledge-tab-panel"
            role="tabpanel"
            aria-labelledby={`knowledge-tab-${tab}`}
          >
            {tab === 'documents' && (
              <section>
                <label className="knowledge-upload">
                  <strong>Add documents</strong>
                  <span>
                    Choose one or more files to add to this knowledge base.
                  </span>
                  {uploadInput()}
                </label>
                <p className="helper">
                  PDF, TXT, MD, CSV, JSON, HTML, DOCX · 25 MB per file · 200
                  pages · 20 active documents per base.
                </p>
                {uploads.map((u) => (
                  <p className="error-text" key={u.key}>
                    {u.file.name}: {u.error}
                  </p>
                ))}
                {uploads.length > 0 && (
                  <button
                    disabled={locked}
                    onClick={() => void act(() => send(uploads))}
                  >
                    Retry failed uploads
                  </button>
                )}
                {!current.documents?.length && (
                  <div className="knowledge-empty">
                    <strong>No documents yet</strong>
                    <p>
                      Upload your first document to start building this
                      knowledge base.
                    </p>
                  </div>
                )}
                {current.documents?.map((d) => (
                  <article className="knowledge-document" key={d.id}>
                    <strong>{d.filename}</strong>
                    <span className="knowledge-status">
                      {d.status} ·{' '}
                      {d.bytes < 1024
                        ? `${d.bytes} B`
                        : d.bytes < 1048576
                          ? `${(d.bytes / 1024).toFixed(1)} KB`
                          : `${(d.bytes / 1048576).toFixed(1)} MB`}
                    </span>
                    {d.error && <p className="error-text">{d.error}</p>}
                    {d.status !== 'removed' && (
                      <>
                        <div className="knowledge-actions">
                          <a
                            href={`/api${kbPath(selected)}/documents/${encodeURIComponent(d.id)}/file`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            Original
                          </a>
                          <button
                            disabled={locked}
                            onClick={() =>
                              void act(async () => {
                                const id = selected;
                                const p = await api<{ sources: unknown }>(
                                  `${kbPath(id)}/documents/${encodeURIComponent(d.id)}/preview`,
                                );
                                if (selection.current === id)
                                  setPreview(parseSources(p.sources));
                              })
                            }
                          >
                            Preview
                          </button>
                          {d.status === 'failed' && (
                            <button
                              disabled={locked}
                              onClick={() =>
                                void act(async () => {
                                  await api(
                                    `${kbPath(selected)}/documents/${encodeURIComponent(d.id)}/retry`,
                                    'POST',
                                  );
                                })
                              }
                            >
                              Retry
                            </button>
                          )}
                          <button
                            disabled={locked}
                            onClick={() =>
                              void act(async () => {
                                await api(
                                  `${kbPath(selected)}/documents/${encodeURIComponent(d.id)}/remove`,
                                  'POST',
                                );
                                setPreview([]);
                                setResult(null);
                              })
                            }
                          >
                            Remove
                          </button>
                        </div>
                        <details className="knowledge-replace">
                          <summary>Replace document</summary>
                          <label>
                            Choose a replacement file{uploadInput(d.id)}
                          </label>
                        </details>
                      </>
                    )}
                  </article>
                ))}
                <SourcePanel sources={preview} />
              </section>
            )}
            {tab === 'settings' && (
              <section>
                <label>
                  Name
                  <input
                    value={name}
                    disabled={locked}
                    onChange={(e) => setName(e.target.value)}
                  />
                </label>
                <label>
                  Description
                  <textarea
                    value={description}
                    disabled={locked}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </label>
                <button
                  disabled={locked || !name.trim()}
                  onClick={() =>
                    void act(async () => {
                      await api(kbPath(selected), 'PUT', {
                        name: name.trim(),
                        description,
                      });
                    })
                  }
                >
                  Save name and description
                </button>
                <KnowledgeConfig
                  value={config}
                  onChange={setConfig}
                  caps={caps}
                  connections={connections}
                  disabled={locked}
                />
                <p className="helper">
                  Rebuild applies these settings to all active documents. The
                  current version stays searchable until the new version is
                  ready.
                </p>
                <button
                  disabled={locked}
                  onClick={() =>
                    void act(async () => {
                      await api(`${kbPath(selected)}/rebuild`, 'POST', {
                        config,
                      });
                    })
                  }
                >
                  Rebuild with these settings
                </button>
                <button
                  disabled={locked || current.pending_version == null}
                  onClick={() =>
                    void act(async () => {
                      await api(`${kbPath(selected)}/cancel`, 'POST');
                    })
                  }
                >
                  Cancel pending build
                </button>
                <details>
                  <summary>Delete knowledge base</summary>
                  <p>
                    Deletion stops search and source access. Stored index
                    cleanup runs in the background.
                  </p>
                  <button
                    disabled={locked}
                    onClick={() =>
                      void act(async () => {
                        await api(`${kbPath(selected)}/delete`, 'POST');
                        choose('');
                      })
                    }
                  >
                    Delete this knowledge base
                  </button>
                </details>
              </section>
            )}
            {tab === 'search' && (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  setResult(null);
                  void act(async () => {
                    const id = selected;
                    const r = await api<{
                      sources: unknown;
                      status: string;
                      version: number;
                      elapsed_ms: number;
                    }>(`${kbPath(id)}/search`, 'POST', { query, options });
                    if (selection.current === id)
                      setResult({
                        sources: parseSources(r.sources),
                        label:
                          r.status === 'no_matches'
                            ? 'No matching passages.'
                            : `Version ${r.version} · ${r.elapsed_ms} ms`,
                      });
                  });
                }}
              >
                <label>
                  Search query
                  <textarea
                    required
                    value={query}
                    disabled={locked}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
                <SearchFields
                  value={options}
                  onChange={setOptions}
                  disabled={locked}
                />
                <button
                  disabled={
                    locked || !query.trim() || current.active_version == null
                  }
                >
                  Search knowledge base
                </button>
                {result && (
                  <>
                    <p aria-live="polite">{result.label}</p>
                    <SourcePanel sources={result.sources} />
                  </>
                )}
              </form>
            )}
            {tab === 'activity' && (
              <section aria-live="polite">
                {!current.jobs?.length && <p>No ingestion activity yet.</p>}
                {current.jobs?.map((j) => (
                  <article className="knowledge-document" key={j.id}>
                    <strong>
                      Version {j.version} · {j.status}
                    </strong>
                    <p>
                      {j.stage}
                      {j.total ? ` · ${j.completed ?? 0}/${j.total}` : ''}
                    </p>
                    {j.total ? (
                      <progress value={j.completed ?? 0} max={j.total} />
                    ) : null}
                    {j.error && <p className="error-text">{j.error}</p>}
                    <p className="helper">
                      Cleanup: {j.cleanup_status || 'not scheduled'}
                    </p>
                  </article>
                ))}
                <details>
                  <summary>Version history</summary>
                  <pre>{JSON.stringify(current.versions || [], null, 2)}</pre>
                </details>
              </section>
            )}
          </div>
        </>
      )}
    </div>
  );
}
