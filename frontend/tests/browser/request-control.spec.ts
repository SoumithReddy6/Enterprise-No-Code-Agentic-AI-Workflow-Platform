import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import type { Workflow } from '../../lib/workflow';

test('declared control handles and optional inputs render; load/edit/validate preserves contract 2', async ({ page }) => {
  const catalog = JSON.parse(readFileSync(new URL('./example-catalog.json', import.meta.url), 'utf8'));
  catalog.push({ type: 'test_control', name: 'Test control', category: 'Control', description: 'Test-only control',
    inputs: { message: 'string' }, optional_inputs: { reference: 'object' }, outputs: { branch: 'string', text: 'string' },
    branch_routes: ['continue', 'reply'], config_schema: { properties: {} } });
  const submitted: Workflow[] = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/validate') {
      const workflow = route.request().postDataJSON() as Workflow; submitted.push(workflow);
      await route.fulfill({ json: { valid: true, errors: [], warnings: [], workflow } }); return;
    }
    const fixtures: Record<string, unknown> = { '/api/auth/status': { enabled: false }, '/api/nodes': catalog,
      '/api/workflows': [], '/api/credentials': [], '/api/models': [], '/api/connections': [],
      '/api/knowledge-bases': [], '/api/runs/page': { items: [], next_cursor: null } };
    await route.fulfill({ json: fixtures[path] ?? [] });
  });
  page.on('dialog', dialog => void dialog.accept());
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Condition', exact: true })).toBeVisible();
  const workflow: Workflow = { version: 1, contract: 2, name: 'Contract preservation', description: '', nodes: [
    { id: 'input', type: 'chat_input', version: 1, label: 'Original input', position: { x: 300, y: 40 }, inputs: {}, config: {} },
    { id: 'guard', type: 'test_control', version: 1, label: 'Declared control', position: { x: 300, y: 220 }, inputs: { message: 'input.message' }, config: {} },
    { id: 'out', type: 'response', version: 1, label: 'Accepted', position: { x: 100, y: 400 }, inputs: { text: 'guard.text' }, config: {} },
    { id: 'reply', type: 'response', version: 1, label: 'Reply', position: { x: 500, y: 400 }, inputs: { text: 'guard.text' }, config: {} },
  ], edges: [{ id: 'a', source: 'input', target: 'guard' },
    { id: 'b', source: 'guard', target: 'out', sourceHandle: 'continue' },
    { id: 'c', source: 'guard', target: 'reply', sourceHandle: 'reply' }] };
  await page.locator('input[type=file]').setInputFiles({ name: 'guarded.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(workflow)) });
  await expect(page.getByRole('textbox', { name: 'Workflow name' })).toHaveValue('Contract preservation');
  const card = page.locator('[data-id="guard"]');
  await expect(card.locator('[data-handleid="continue"]')).toHaveCount(1);
  await expect(card.locator('[data-handleid="reply"]')).toHaveCount(1);
  await card.click();
  const reference = page.getByLabel(/^reference \(optional\)/);
  await expect(reference).toHaveValue('');
  await reference.selectOption('input.message');
  await reference.selectOption('');
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect.poll(() => submitted.length).toBeGreaterThanOrEqual(2);
  expect(submitted.at(-1)!.contract).toBe(2);
  expect(submitted.at(-1)!.nodes.find(n => n.id === 'guard')!.inputs).toEqual({ message: 'input.message' });
});
