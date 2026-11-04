import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import type { Workflow } from '../../lib/workflow';

// Generated from the real registry and checked by the backend router tests.
const catalog: unknown = JSON.parse(readFileSync(new URL('./request-router-node.json', import.meta.url), 'utf8'));

test('the request router selects an enabled model and its bound credential', async ({ page }) => {
  const validated: Workflow[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (path === '/api/validate') {
      const workflow = request.postDataJSON() as Workflow; validated.push(workflow);
      await route.fulfill({ json: { valid: true, errors: [], warnings: [], workflow } }); return;
    }
    const fixtures: Record<string, unknown> = {
      '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [],
      '/api/credentials': [{ id: 'cloud-key', name: 'Cloud key', provider: 'openai' }],
      '/api/models': [
        { id: 'local', provider: 'ollama', model: 'fixture', credential_id: '', enabled: true },
        { id: 'cloud', provider: 'openai', model: 'cloud', credential_id: 'cloud-key', enabled: true },
      ], '/api/connections': [], '/api/knowledge-bases': [], '/api/runs/page': { items: [], next_cursor: null },
    };
    await route.fulfill({ json: fixtures[path] ?? [] });
  });
  page.on('dialog', dialog => void dialog.accept());
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Chat input', exact: true })).toBeVisible();
  // The full admission palette/starter lands in Task 7; imported router nodes already
  // need the same authorized model picker as other model-backed nodes.
  let index = 0;
  const node = (id: string, type: string, inputs: Record<string, string>, config: Record<string, unknown> = {}) =>
    ({ id, type, version: 1, label: id, position: { x: 350, y: 80 + 140 * index++ }, inputs, config });
  const workflow = { version: 1, contract: 2, name: 'Router settings', description: '', nodes: [
    node('input', 'chat_input', {}), node('cache', 'greeting_cache', { message: 'input.message' }),
    node('router', 'request_router', { message: 'input.message' }, { purpose: 'Orders', provider: 'ollama', model: 'fixture', credential_id: '', max_tokens: 2048 }),
    node('accepted', 'response', { text: 'input.message' }), node('declined', 'response', { text: 'router.reply' }),
    node('greeting', 'response', { text: 'cache.reply' }),
  ], edges: [{ id: 'a', source: 'input', target: 'cache' }, { id: 'b', source: 'cache', target: 'router', sourceHandle: 'continue' },
    { id: 'c', source: 'cache', target: 'greeting', sourceHandle: 'reply' }, { id: 'd', source: 'router', target: 'accepted', sourceHandle: 'continue' },
    { id: 'e', source: 'router', target: 'declined', sourceHandle: 'reply' }] };
  await page.locator('input[type=file]').setInputFiles({ name: 'router.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(workflow)) });
  await expect(page.getByRole('textbox', { name: 'Workflow name' })).toHaveValue('Router settings');
  await page.locator('[data-id="router"]').click();
  const model = page.getByRole('combobox', { name: /^Model/ });
  await expect(model).toHaveValue('local');
  await model.selectOption('cloud');
  await page.getByLabel(/^Purpose/).fill('Handle purchase requests');
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect.poll(() => validated.length).toBeGreaterThan(0);
  const settings = validated.at(-1)!.nodes.find(n => n.type === 'request_router')!.config;
  expect(settings).toMatchObject({ purpose: 'Handle purchase requests', provider: 'openai', model: 'cloud', credential_id: 'cloud-key' });
});
