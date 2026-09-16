'use client';
import { useEffect, useState } from 'react';
import { api, type ConfigProperty } from '@/lib/workflow';
const scalar = (value: unknown): string =>
  ['string', 'number', 'boolean'].includes(typeof value) ? String(value) : '';
export type ToolConnection = {
  id: string;
  name: string;
  provider: string;
  endpoint: string;
  username?: string;
  port?: number;
  sender?: string;
  has_secret?: boolean;
};
export type VectorResource = {
  id: string;
  name: string;
  backend: string;
  storage_path: string;
  connection_id?: string;
  embedding_model: string;
  index_method: string;
  index_name?: string;
};

export function ConfigField({
  name,
  property,
  value,
  disabled,
  onChange,
}: {
  name: string;
  property: ConfigProperty;
  value: unknown;
  disabled: boolean;
  onChange: (value: unknown) => void;
}) {
  const schema = property.anyOf?.find((p) => p.type !== 'null') || property;
  const type = schema.type;
  const json = type === 'object' || type === 'array';
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState('');
  const options = schema.enum || property.enum;
  return (
    <label>
      {property.title || name.replaceAll('_', ' ')}
      {options ? (
        <select
          disabled={disabled}
          value={scalar(value ?? property.default ?? '')}
          onChange={(e) => onChange(e.target.value)}
        >
          {options.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      ) : type === 'boolean' ? (
        <input
          type="checkbox"
          disabled={disabled}
          checked={Boolean(value ?? property.default)}
          onChange={(e) => onChange(e.target.checked)}
        />
      ) : json ? (
        <textarea
          disabled={disabled}
          rows={5}
          value={
            draft ??
            JSON.stringify(
              value ?? property.default ?? (type === 'array' ? [] : {}),
              null,
              2,
            )
          }
          onChange={(e) => {
            setDraft(e.target.value);
            try {
              const parsed: unknown = JSON.parse(e.target.value);
              if (
                type === 'object' &&
                (parsed === null ||
                  Array.isArray(parsed) ||
                  typeof parsed !== 'object')
              )
                throw Error('Enter a JSON object');
              if (type === 'array' && !Array.isArray(parsed))
                throw Error('Enter a JSON array');
              onChange(parsed);
              setError('');
            } catch {
              setError('Enter valid JSON before leaving this field.');
            }
          }}
        />
      ) : ['integer', 'number'].includes(type || '') ? (
        <input
          type="number"
          disabled={disabled}
          step={type === 'integer' ? 1 : 'any'}
          min={schema.minimum}
          max={schema.maximum}
          placeholder="Provider default"
          value={scalar(value ?? property.default ?? '')}
          onChange={(e) =>
            onChange(e.target.value === '' ? undefined : Number(e.target.value))
          }
        />
      ) : [
          'system',
          'user_prompt',
          'template',
          'code',
          'body',
          'content',
        ].includes(name) ? (
        <textarea
          disabled={disabled}
          rows={5}
          value={scalar(value ?? property.default ?? '')}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          disabled={disabled}
          value={scalar(value ?? property.default ?? '')}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {property.description && (
        <span className="helper">{property.description}</span>
      )}
      {error && <span className="error-text">{error}</span>}
    </label>
  );
}

export function VectorFiles({
  resource,
  disabled,
}: {
  resource: VectorResource;
  disabled: boolean;
}) {
  const [files, setFiles] = useState<
    { id: string; filename: string; status: string; error?: string }[]
  >([]);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<typeof files>(
          `/vector-resources/${encodeURIComponent(resource.id)}/files`,
        );
        if (!active) return;
        setFiles(result);
        if (
          result.some((f) =>
            ['queued', 'processing', 'indexing'].includes(f.status),
          )
        )
          timer = setTimeout(poll, 2000);
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [resource.id, revision]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await fn();
      setRevision((x) => x + 1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="settings-section">
      <h3>FILES · {resource.storage_path}</h3>
      <p className="helper">
        Files belong only to this resource and storage path. PDF, text,
        Markdown, CSV, JSON, HTML and DOCX are supported.
      </p>
      <label className="button secondary">
        Upload files
        <input
          type="file"
          multiple
          disabled={disabled || busy}
          onChange={(e) => {
            const uploads = Array.from(e.target.files || []);
            e.target.value = '';
            void action(async () => {
              const failures: string[] = [];
              for (const file of uploads) {
                const response = await fetch(
                  `/api/vector-resources/${encodeURIComponent(resource.id)}/files?filename=${encodeURIComponent(file.name)}`,
                  {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/octet-stream' },
                    body: file,
                  },
                );
                if (!response.ok) {
                  const detail = (await response.json().catch(() => ({}))) as {
                    detail?: string;
                  };
                  failures.push(
                    `${file.name}: ${detail.detail || 'Upload failed'}`,
                  );
                }
              }
              setRevision((x) => x + 1);
              if (failures.length) throw Error(failures.join('\n'));
            });
          }}
        />
      </label>
      {error && <p className="error-text">{error}</p>}
      {files.map((f) => (
        <div className="resource-file" key={f.id}>
          <a
            href={`/api/vector-files/${encodeURIComponent(f.id)}/file`}
            target="_blank"
            rel="noreferrer"
          >
            {f.filename}
          </a>
          <small>{f.status}</small>
          {f.error && <p className="error-text">{f.error}</p>}
          <button
            disabled={disabled || busy}
            className="text-button"
            onClick={() =>
              void action(async () => {
                await api(
                  `/vector-files/${encodeURIComponent(f.id)}/retry`,
                  'POST',
                );
              })
            }
          >
            Retry
          </button>
          <button
            disabled={disabled || busy}
            className="text-button"
            onClick={() =>
              void action(async () => {
                await api(
                  `/vector-files/${encodeURIComponent(f.id)}/remove`,
                  'POST',
                );
              })
            }
          >
            Remove
          </button>
        </div>
      ))}
    </section>
  );
}

export default function PlatformSettings({
  connections,
  refresh,
  disabled,
}: {
  connections: ToolConnection[];
  refresh: () => Promise<void>;
  disabled: boolean;
}) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState('');
  const [connection, setConnection] = useState({
    name: '',
    provider: 'http',
    endpoint: '',
    username: '',
    secret: '',
    port: 587,
    sender: '',
  });
  async function perform(fn: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="platform-settings">
      <div className="panel-title">Connections</div>
      <p className="helper">
        Reusable workspace connections keep secrets out of workflow JSON.
      </p>
      {error && <p className="error-text">{error}</p>}
      <section className="inspector-form">
        <h3>TOOL CONNECTIONS</h3>
        <select
          aria-label="Edit connection"
          value={edit}
          onChange={(e) => {
            setEdit(e.target.value);
            const c = connections.find((c) => c.id === e.target.value);
            setConnection(
              c
                ? {
                    name: c.name,
                    provider: c.provider,
                    endpoint: c.endpoint,
                    username: c.username || '',
                    secret: '',
                    port: c.port || 587,
                    sender: c.sender || '',
                  }
                : {
                    name: '',
                    provider: 'http',
                    endpoint: '',
                    username: '',
                    secret: '',
                    port: 587,
                    sender: '',
                  },
            );
          }}
        >
          <option value="">New connection</option>
          {connections.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} · {c.provider}
            </option>
          ))}
        </select>
        {Object.entries(connection).map(([key, value]) => (
          <label key={key}>
            {key === 'secret'
              ? 'API key / password (blank keeps saved key)'
              : key}
            <input
              disabled={disabled || busy}
              type={
                key === 'secret'
                  ? 'password'
                  : key === 'port'
                    ? 'number'
                    : 'text'
              }
              value={value}
              list={key === 'provider' ? 'connection-providers' : undefined}
              onChange={(e) =>
                setConnection({
                  ...connection,
                  [key]:
                    key === 'port' ? Number(e.target.value) : e.target.value,
                })
              }
            />
          </label>
        ))}
        <datalist id="connection-providers">
          {[
            'http',
            'email',
            'jira',
            'confluence',
            'github',
            'elasticsearch',
            'pinecone',
          ].map((p) => (
            <option key={p}>{p}</option>
          ))}
        </datalist>
        <button
          className="button secondary"
          disabled={disabled || busy || !connection.name}
          onClick={() =>
            void perform(async () => {
              await api(
                edit ? `/connections/${edit}` : '/connections',
                edit ? 'PUT' : 'POST',
                connection,
              );
              setConnection({ ...connection, secret: '' });
            })
          }
        >
          Save connection
        </button>
      </section>
    </div>
  );
}
