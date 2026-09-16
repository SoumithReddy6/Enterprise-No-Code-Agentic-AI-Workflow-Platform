'use client';
import { useState } from 'react';
import { api, type Credential } from '@/lib/workflow';
import { providerName, type AllowedModel } from '@/lib/models';

type Props = {
  models: AllowedModel[];
  credentials: Credential[];
  onChange: () => Promise<void>;
};
export default function ProviderSettings({
  models,
  credentials,
  onChange,
}: Props) {
  const [provider, setProvider] = useState('ollama');
  const [credential, setCredential] = useState('');
  const [keyName, setKeyName] = useState('');
  const [secret, setSecret] = useState('');
  const [model, setModel] = useState('');
  const [installed, setInstalled] = useState<{ name: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  async function perform(action: () => Promise<void>) {
    setBusy(true);
    setNotice('');
    try {
      await action();
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const keys = credentials.filter((c) => c.provider === provider);
  return (
    <div className="provider-settings">
      <h2>Providers & models</h2>
      <p className="helper">
        Configure this workspace here. Choose enabled models in a node’s
        right-hand settings.
      </p>
      <label>
        Provider
        <select
          disabled={busy}
          value={provider}
          onChange={(e) => {
            setProvider(e.target.value);
            setCredential('');
            setModel('');
            setSecret('');
            setKeyName('');
            setNotice('');
          }}
        >
          {['ollama', 'openai', 'claude'].map((p) => (
            <option key={p} value={p}>
              {providerName(p)}
            </option>
          ))}
        </select>
      </label>
      {provider === 'ollama' ? (
        <>
          <p className="helper">
            Uses Ollama on the backend machine. No API key required.
          </p>
          <button
            className="button"
            disabled={busy}
            onClick={() =>
              void perform(async () => {
                const result = await api<{
                  connected: boolean;
                  models: { name: string }[];
                  error?: string;
                }>('/providers/ollama/models');
                setInstalled(result.models);
                if (!result.connected)
                  throw new Error(result.error || 'Ollama is unavailable.');
                setNotice(
                  result.models.length
                    ? 'Installed models loaded. Enable a chat model below.'
                    : 'No models installed. Pull a chat model in Ollama, then refresh.',
                );
              })
            }
          >
            {busy ? 'Loading…' : 'Refresh installed models'}
          </button>
        </>
      ) : (
        <>
          <label>
            Saved API key
            <select
              disabled={busy}
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
            >
              <option value="">Select a key</option>
              {keys.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          <details
            className="provider-key-form"
            open={keys.length === 0 ? true : undefined}
          >
            <summary>Add API key</summary>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void perform(async () => {
                  const saved = await api<Credential>('/credentials', 'POST', {
                    name: keyName.trim(),
                    secret: secret.trim(),
                    provider,
                  });
                  setSecret('');
                  setKeyName('');
                  setCredential(saved.id);
                  await onChange();
                  setNotice(
                    'API key encrypted and saved. Enable a model below.',
                  );
                });
              }}
            >
              <label>
                Key name
                <input
                  disabled={busy}
                  required
                  maxLength={120}
                  value={keyName}
                  onChange={(e) => setKeyName(e.target.value)}
                  placeholder="My development key"
                />
              </label>
              <label>
                {providerName(provider)} API key
                <input
                  disabled={busy}
                  required
                  type="password"
                  autoComplete="off"
                  maxLength={4000}
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                />
              </label>
              <button
                className="button"
                disabled={busy || !secret.trim() || !keyName.trim()}
              >
                Save API key
              </button>
            </form>
          </details>
          <p className="helper">
            Keys are encrypted on the backend and never included in exported
            workflows.
          </p>
        </>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void perform(async () => {
            await api('/models', 'POST', {
              provider,
              model: model.trim(),
              credential_id: provider === 'ollama' ? '' : credential,
            });
            await onChange();
            setNotice('Model enabled. Select it in the node on the right.');
            setModel('');
          });
        }}
      >
        <label>
          {provider === 'ollama'
            ? 'Installed model or exact model ID'
            : 'Chat model ID'}
          <input
            disabled={busy}
            required
            maxLength={100}
            pattern="\S+"
            list={provider === 'ollama' ? 'installed-ollama-models' : undefined}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={
              provider === 'ollama'
                ? 'qwen2.5:0.5b'
                : 'Enter the model ID from your provider'
            }
          />
        </label>
        <datalist
          id="installed-ollama-models"
          aria-label="Installed Ollama models"
        >
          {installed.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </datalist>
        {provider !== 'ollama' && (
          <p className="helper">
            Use a text chat model available to your API account. Enabling an ID
            does not verify provider access or make a generation request.
          </p>
        )}
        <button
          className="button primary"
          disabled={
            busy || !model.trim() || (provider !== 'ollama' && !credential)
          }
        >
          Enable model
        </button>
      </form>
      {notice && <output className="provider-notice">{notice}</output>}
      <h3>Workspace model catalog</h3>
      {!models.length && (
        <p className="helper">
          No models enabled yet. Start with Ollama above.
        </p>
      )}
      {models.map((m) => (
        <div className="allowed-model" key={m.id}>
          <strong>{m.model}</strong>
          <span>{providerName(m.provider)}</span>
          {m.credential_id && (
            <small>
              {credentials.find((c) => c.id === m.credential_id)?.name ||
                'API key'}
            </small>
          )}
          <button
            className="text-button"
            disabled={busy}
            aria-label={`${m.enabled ? 'Disable' : 'Enable'} ${m.model}`}
            onClick={() =>
              void perform(async () => {
                await api(`/models/${m.id}`, 'PUT', { enabled: !m.enabled });
                await onChange();
                setNotice(
                  m.enabled
                    ? 'Model disabled. Nodes using it must select an enabled model before running.'
                    : 'Model enabled.',
                );
              })
            }
          >
            {m.enabled ? 'Enabled · disable' : 'Disabled · enable'}
          </button>
        </div>
      ))}
    </div>
  );
}
