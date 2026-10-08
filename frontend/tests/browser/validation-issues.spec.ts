import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import type { Issue, Workflow } from '../../lib/workflow';

// Definitions and the example come from the repository; a backend test keeps the catalog equal
// to what /api/nodes serves.
const catalog: unknown = JSON.parse(readFileSync(new URL('./example-catalog.json', import.meta.url), 'utf8'));
const example = fileURLToPath(new URL('../../../examples/purchase-routing.json', import.meta.url));

async function editor(page: Page, issues: Issue[], unexpected: string[]) {
  const validated: Workflow[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/validate') {
      const workflow = request.postDataJSON() as Workflow;
      validated.push(workflow);
      // The first request is the import's normalization; later ones are Validate clicks.
      const reported = validated.length > 1 ? issues : [];
      await route.fulfill({ json: { valid: !reported.length, errors: reported.map((i) => (i.node_id ? `${i.node_id}: ` : '') + i.message), warnings: [], issues: reported, warning_issues: [], workflow } });
      return;
    }
    const fixtures: Record<string, unknown> = {
      '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [],
      '/api/credentials': [], '/api/models': [], '/api/connections': [], '/api/knowledge-bases': [],
      '/api/runs/page': { items: [], next_cursor: null },
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
  await page.locator('input[type=file]').first().setInputFiles(example);
  await expect(page.locator('.react-flow__node', { hasText: 'Approval message' })).toBeVisible();
  return validated;
}

test('a validation error names its node, highlights it and opens it', async ({ page }) => {
  const unexpected: string[] = [];
  const message = 'invalid binding message = ; use a declared output from a guaranteed upstream node.';
  await editor(page, [{ node_id: 'auto_note', message }, { node_id: null, message: 'Add a response output node.' }], unexpected);
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  const banner = page.getByRole('alert').filter({ hasText: 'Workflow needs attention' });
  await expect(banner).toContainText(`Approval message: ${message}`);
  await expect(banner).not.toContainText('auto_note');
  await expect(banner).toContainText('Add a response output node.');
  const card = page.locator('.workflow-card', { hasText: 'Approval message' });
  await expect(card).toHaveClass(/invalid/);
  await expect(page.locator('.workflow-card.invalid')).toHaveCount(1);
  await banner.getByRole('button', { name: /^Approval message:/ }).click();
  const panel = page.locator('.node-issues');
  await expect(panel).toContainText('This node needs attention');
  await expect(panel).toContainText(message);
  await expect(page.locator('.react-flow__node.selected', { hasText: 'Approval message' })).toBeVisible();
  expect(unexpected).toEqual([]);
});

test('the binding dropdown shows the real binding and offers only what validation accepts', async ({ page }) => {
  const unexpected: string[] = [];
  await editor(page, [], unexpected);
  await page.locator('.react-flow__node', { hasText: 'Approval message' }).click();
  const select = page.locator('label', { hasText: /^message/ }).locator('select');
  await expect(select).toHaveValue('extract.text');
  const options = await select.locator('option').evaluateAll((all) => all.map((o) => [(o as HTMLOptionElement).value, o.textContent]));
  expect(options).toContainEqual(['extract.text', 'Extract amount → text']);
  expect(options.map(([value]) => value)).not.toContain('review_note.text');
  expect(options.map(([value]) => value)).toContain('check.branch');
  expect(unexpected).toEqual([]);
});
