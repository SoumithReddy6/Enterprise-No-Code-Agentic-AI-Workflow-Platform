import { readFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
const B = '/Users/soumith/Library/Mobile Documents/iCloud~md~obsidian/Documents/Soumith/No Code Agentic AI/frontend/tests/browser/';
const catalog = JSON.parse(readFileSync(B + 'request-nodes.json', 'utf8'));
const rec = JSON.parse(readFileSync(B + 'repeat-run-events.json', 'utf8'));
const model = JSON.parse(readFileSync(B + 'repeat-issues.json', 'utf8')).model;
test('shots', async ({ page }) => {
  let normalized: unknown = rec.imports[0].normalized, submitted: unknown, stage = 1;
  const events = () => rec.events.filter((e: { seq: number }) => stage === 2 || e.seq <= 8);
  await page.route('**/api/**', async (route) => {
    const r = route.request(); const path = new URL(r.url()).pathname;
    if (path === '/api/validate') { const w = r.postDataJSON(); const out = normalized ?? w; normalized = undefined;
      return route.fulfill({ json: { valid: true, errors: [], warnings: [], issues: [], warning_issues: [], workflow: out } }); }
    if (path === '/api/runs' && r.method() === 'POST') { submitted = r.postDataJSON().workflow; return route.fulfill({ json: { id: 'x' } }); }
    if (path === '/api/runs/x/events') return route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' },
      body: events().map((e: unknown) => 'event: node\ndata: ' + JSON.stringify(e) + '\n\n').join('') });
    if (path === '/api/runs/x') return route.fulfill({ json: { id: 'x', status: stage === 1 ? 'running' : 'success', output: 'draft two', error: '', created_at: '', events: events(), workflow: submitted, message: 'go' } });
    const f: Record<string, unknown> = { '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [], '/api/credentials': [],
      '/api/models': [model], '/api/connections': [], '/api/knowledge-bases': [], '/api/runs/page': { items: [], next_cursor: null } };
    return route.fulfill({ json: f[path] ?? {} });
  });
  page.on('dialog', (d) => void d.accept());
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Chat input', exact: true })).toBeVisible();
  await page.locator('input[type=file]').first().setInputFiles({ name: 'w.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(rec.imports[0].file)) });
  await expect(page.getByRole('textbox', { name: 'Workflow name' })).toHaveValue('Repeat');
  await page.locator('.react-flow__controls-fitview').click(); await page.waitForTimeout(600);
  await page.screenshot({ path: 'canvas.png' });
  await page.locator('.react-flow__node[data-id="r"]').click(); await page.waitForTimeout(300);
  await page.screenshot({ path: 'inspector-repeat.png' });
  await page.getByRole('button', { name: 'Run workflow' }).click();
  await expect(page.locator('.react-flow__node[data-id="r"] .workflow-card')).toHaveClass(/running/);
  await page.screenshot({ path: 'running.png' });
  stage = 2;
  await expect(page.locator('.react-flow__node[data-id="r"] .workflow-card')).toHaveClass(/success/);
  await page.getByRole('button', { name: /^Execution log/ }).click();
  await page.locator('.event-row').nth(7).locator('summary').click();
  await page.locator('.event-row').nth(13).locator('summary').click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: 'events.png' });
});
