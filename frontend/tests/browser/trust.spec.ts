import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';
import type { Run, Workflow } from '../../lib/workflow';

const catalog: unknown = JSON.parse(readFileSync(new URL('./example-catalog.json', import.meta.url), 'utf8'));
const examplePath = fileURLToPath(new URL('../../../examples/purchase-routing.json', import.meta.url));
const example = JSON.parse(readFileSync(examplePath, 'utf8')) as Workflow;

// A recorded run of the example, as the API returns it: labels on outputs, a decision on the Condition.
const run: Run = {
  id: 'trust-fixture', name: 'Purchase routing', status: 'success', output: 'Auto-approved',
  error: '', created_at: '2026-10-09T12:00:00Z', finished_at: '2026-10-09T12:00:01Z',
  workflow: example, message: 'order $450', events: [
    { seq: 1, node_id: 'extract', status: 'success', timestamp: '', outputs: {
      text: '{"item": "order", "quantity": null, "unit_price": null, "total": 450}', provider: 'ollama', sources: '[]', grounding: '{}',
      _provenance: { v: 1, ports: { text: 'guessed', provider: 'source', sources: 'source', grounding: 'source' },
        fields: { text: { item: 'quoted', quantity: 'absent', unit_price: 'absent', total: 'quoted' } } } } },
    { seq: 2, node_id: 'check', status: 'success', timestamp: '', outputs: { branch: 'false',
      _provenance: { v: 1, ports: { branch: 'guessed' } } },
      decision: { label: 'guessed', would_review: true, reason: 'guessed' } },
  ],
};

test('the inspector shows where each value came from, and which decision would need a person', async ({ page }) => {
  const unexpected: string[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/validate') {
      await route.fulfill({ json: { valid: true, errors: [], warnings: [], issues: [], warning_issues: [], workflow: request.postDataJSON() } });
      return;
    }
    const fixtures: Record<string, unknown> = {
      '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [],
      '/api/credentials': [], '/api/models': [], '/api/connections': [], '/api/knowledge-bases': [],
      '/api/runs/page': { items: [run], next_cursor: null }, '/api/runs/trust-fixture': run,
    };
    if (request.method() !== 'GET' || !Object.hasOwn(fixtures, path)) {
      unexpected.push(`${request.method()} ${path}`);
      await route.abort();
      return;
    }
    await route.fulfill({ json: fixtures[path] });
  });
  page.on('dialog', (dialog) => void dialog.accept());
  await page.goto('/');
  await page.locator('input[type=file]').first().setInputFiles(examplePath);
  await expect(page.locator('.react-flow__node', { hasText: 'Extract amount' })).toBeVisible();
  await page.getByRole('button', { name: 'History', exact: true }).click();
  await page.getByRole('button', { name: /^Purchase routing Completed/ }).click();

  await page.locator('.react-flow__node', { hasText: 'Extract amount' }).click();
  const trust = page.getByRole('region', { name: 'Trust in the last run' });
  await expect(trust.locator('li', { hasText: 'item' })).toContainText('Found in the input');
  await expect(trust.locator('li', { hasText: 'quantity' })).toContainText('Not stated');

  await page.locator('.react-flow__node', { hasText: 'Over $1,000?' }).click();
  await expect(trust).toContainText('A person would need to decide: the value was not found in the input.');
  await expect(trust.locator('li', { hasText: 'branch' })).toContainText('Not confirmed: a model guess');
  expect(unexpected).toEqual([]);
});
