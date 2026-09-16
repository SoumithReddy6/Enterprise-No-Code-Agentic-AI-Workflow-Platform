import test from 'node:test';
import assert from 'node:assert/strict';
import { selectedModelId, modelConfig } from '../lib/models.ts';
const catalog = [
  {
    id: 'one',
    provider: 'ollama',
    model: 'tiny',
    credential_id: '',
    enabled: true,
  },
  {
    id: 'two',
    provider: 'claude',
    model: 'cloud',
    credential_id: 'key',
    enabled: false,
  },
];
void test('selection only matches enabled models and their bound credential', () => {
  assert.equal(
    selectedModelId(catalog, { provider: 'ollama', model: 'tiny' }),
    'one',
  );
  assert.equal(
    selectedModelId(catalog, {
      provider: 'claude',
      model: 'cloud',
      credential_id: 'key',
    }),
    '',
  );
  assert.equal(
    selectedModelId(catalog, {
      provider: 'ollama',
      model: 'tiny',
      credential_id: 'wrong',
    }),
    '',
  );
});
void test('switching provider replaces the old credential and preserves system prompt', () => {
  assert.deepEqual(
    modelConfig(
      { system: 'Be brief', provider: 'claude', credential_id: 'old' },
      catalog[0],
    ),
    {
      system: 'Be brief',
      provider: 'ollama',
      model: 'tiny',
      credential_id: '',
    },
  );
});

void test('slow old refresh cannot overwrite a newer catalog response', async () => {
  const { latestRequestGuard } = await import('../lib/models.ts');
  const begin = latestRequestGuard();
  const oldRequest = begin();
  const newRequest = begin();
  assert.equal(newRequest(), true);
  assert.equal(oldRequest(), false);
});
