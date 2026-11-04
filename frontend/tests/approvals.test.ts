import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { approvalDecision } from '../lib/approvals.ts';

void test('approval binds to exact displayed bytes including Unicode and large numbers', async () => {
  const payload_json = '{"body": "Hello 🌍", "number": 9007199254740993}';
  const approval = { id: 'one', node_id: 'email', status: 'pending', expires_at: 1, payload_json };
  assert.deepEqual(await approvalDecision(approval), {
    approval_id: 'one', digest: createHash('sha256').update(payload_json).digest('hex'),
  });
  assert.notEqual((await approvalDecision(approval)).digest,
    (await approvalDecision({ ...approval, payload_json: payload_json + ' ' })).digest);
});

import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { createElement, type ComponentType } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { RunApproval } from '../lib/approvals.ts';

void test('review renders complete outgoing payload as escaped text and disables decisions while busy', () => {
  const source = readFileSync(new URL('../components/approval-review.tsx', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const exports: { default?: ComponentType<{ approval: RunApproval; disabled: boolean; onDecision: () => void }> } = {};
  runInNewContext(compiled.outputText, { exports, require: createRequire(import.meta.url) });
  assert.ok(exports.default);
  const approval = { id: 'id', node_id: 'email', status: 'pending', expires_at: 1, payload_json: '{"recipient":"person@example.com","body":"<script>danger</script>"}' };
  const html = renderToStaticMarkup(createElement(exports.default, { approval, disabled: true, onDecision: () => {} }));
  assert.ok(html.includes('person@example.com') && html.includes('&lt;script&gt;danger&lt;/script&gt;'));
  assert.ok(!html.includes('<script>'));
  assert.equal((html.match(/disabled=""/g) ?? []).length, 2);
  assert.ok(html.includes('Approve and continue') && html.includes('Reject action'));
});
