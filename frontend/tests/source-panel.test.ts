import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';
import { createElement, type ComponentType } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { parseSources, documentLink, type KnowledgeSource } from '../lib/knowledge.ts';

void test('SourcePanel renders S1 through S120 with safe document links', () => {
  // Exercise the real TSX component with the existing strip-only Node test runner.
  const source = readFileSync(new URL('../components/source-panel.tsx', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const require = createRequire(import.meta.url);
  const exports: { default?: ComponentType<{ sources: KnowledgeSource[] }> } = {};
  runInNewContext(compiled.outputText, {
    exports,
    require: (id: string): unknown => id === '@/lib/knowledge' ? { documentLink } : require(id),
  });
  assert.ok(exports.default);
  const sources = parseSources(JSON.stringify(Array.from({ length: 120 }, (_, i) => ({
    id: `chunk${i}`, document_id: 'document', knowledge_base_id: 'kb', version: 1,
    filename: 'facts.pdf', page: 1, text: '<script>unsafe</script>', score: 1,
    citation: `S${i + 1}`, cited: true,
  }))));
  const html = renderToStaticMarkup(createElement(exports.default, { sources }));
  assert.equal((html.match(/<article /g) ?? []).length, 120);
  for (let i = 1; i <= 120; i++) assert.ok(html.includes(`[S${i}]`));
  assert.ok(html.includes('/api/knowledge-bases/kb/documents/document/file#page=1'));
  assert.ok(!html.includes('<script>'));
});
