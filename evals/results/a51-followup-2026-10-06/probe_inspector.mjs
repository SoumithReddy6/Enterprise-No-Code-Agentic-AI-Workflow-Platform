// Run while the isolated browser Vite harness serves port 3318. No real API is contacted.
import { chromium } from '../../../frontend/node_modules/@playwright/test/index.mjs';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const condition = JSON.parse(readFileSync(new URL('../../../frontend/tests/browser/condition-node.json', import.meta.url)));
const validated = [];
const unexpected = [];
const browser = await chromium.launch({ headless: true });
let report;
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/validate') {
      const workflow = request.postDataJSON();
      validated.push(workflow);
      await route.fulfill({ json: { valid: true, errors: [], warnings: [], workflow } });
      return;
    }
    const fixtures = {
      '/api/auth/status': { enabled: false }, '/api/nodes': [condition], '/api/workflows': [],
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
  await page.goto('http://127.0.0.1:3318/');
  await page.locator('.palette-node', { hasText: 'Condition' }).click();
  await page.getByLabel(/^Operator/).selectOption('in');
  const options = page.getByLabel(/^Options/);
  await options.fill('["high"]');
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await page.waitForFunction(() => document.body.textContent.includes('Workflow is valid and ready to run.'));
  const before = validated.length;
  await options.fill('["urgent",');
  await page.getByText('Enter valid JSON before leaving this field.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  // A response has completed once this validation request is observed and the toast updates.
  await page.waitForTimeout(500);
  report = {
    visible_options: await options.inputValue(),
    visible_error: await page.getByText('Enter valid JSON before leaving this field.', { exact: true }).isVisible(),
    validation_requests_with_invalid_draft: validated.length - before,
    submitted_options: validated.at(-1).nodes.find(n => n.type === 'condition').config.options,
    unexpected,
    passed: validated.length === before,
    scope: 'Actual inspector with mocked API; proves stale predicate submission, not a remote production write.',
  };
  await page.screenshot({ path: fileURLToPath(new URL('invalid-options.png', import.meta.url)) });
} finally {
  await browser.close();
}
writeFileSync(new URL('inspector-results.json', import.meta.url), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
process.exitCode = report.passed ? 0 : 1;
