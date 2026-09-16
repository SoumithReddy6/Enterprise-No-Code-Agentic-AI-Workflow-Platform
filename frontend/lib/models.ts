export type AllowedModel = {
  id: string;
  provider: string;
  model: string;
  credential_id: string;
  enabled: boolean;
};
export const providerName = (provider: string) =>
  ({ ollama: 'Ollama · local', openai: 'OpenAI', claude: 'Claude' })[
    provider
  ] || provider;
export function selectedModelId(
  models: AllowedModel[],
  config: Record<string, unknown>,
) {
  return (
    models.find(
      (m) =>
        m.enabled &&
        m.provider === config.provider &&
        m.model === config.model &&
        m.credential_id === (config.credential_id || ''),
    )?.id || ''
  );
}
export function modelConfig(
  config: Record<string, unknown>,
  model: AllowedModel,
) {
  return {
    ...config,
    provider: model.provider,
    model: model.model,
    credential_id: model.credential_id,
  };
}

// A later reload must win even when an earlier request finishes last.
export function latestRequestGuard() {
  let sequence = 0;
  return () => {
    const request = ++sequence;
    return () => request === sequence;
  };
}
