import test from 'node:test';
import assert from 'node:assert/strict';
import { parseSources, documentLink, sourcesForRun } from '../lib/knowledge.ts';
import type { Workflow } from '../lib/workflow.ts';

const source = {
  id: 'chunk123',
  document_id: 'doc123',
  knowledge_base_id: 'kb_123',
  version: 2,
  filename: '<script>.pdf',
  page: 2,
  text: '[evil](javascript:alert(1))',
  score: 1,
  citation: 'S1',
  cited: true,
};
const node = (id: string, type: string) => ({
  id,
  type,
  version: 1 as const,
  label: '',
  position: { x: 0, y: 0 },
  inputs: {},
  config: {},
});
const workflow: Workflow = {
  version: 1,
  name: 'Ask the knowledge base',
  description: '',
  nodes: [node('input', 'chat_input'), node('answer', 'query')],
  edges: [],
};

void test('sources preserve plain strings and links only use bounded identifiers', () => {
  assert.deepEqual(parseSources(JSON.stringify([source])), [source]);
  assert.equal(
    documentLink(source),
    '/api/knowledge-bases/kb_123/documents/doc123/file#page=2',
  );
  assert.equal(
    parseSources(JSON.stringify([{ ...source, document_id: '../foreign' }]))
      .length,
    0,
  );
  assert.equal(
    parseSources(JSON.stringify([{ ...source, citation: 'javascript:evil' }]))
      .length,
    0,
  );
  assert.deepEqual(parseSources('invalid'), []);
});

void test('sources come only from successful answer events in the selected run snapshot', () => {
  const events = [
    {
      status: 'success',
      node_id: 'answer',
      outputs: { sources: JSON.stringify([source]) },
      seq: 1,
      timestamp: '',
    },
  ];
  assert.deepEqual(sourcesForRun(undefined, events), []);
  assert.deepEqual(sourcesForRun({ workflow }, events), [source]);
  assert.deepEqual(
    sourcesForRun({ workflow }, [{ ...events[0], status: 'failed' }]),
    [],
  );
});

void test('unified KB sources retain version and ignore carried URLs', () => {
  const modern = { ...source, url: 'javascript:bad' };
  assert.equal(
    documentLink(parseSources([modern])[0]),
    '/api/knowledge-bases/kb_123/documents/doc123/file#page=2',
  );
  const { citation: _citation, cited: _cited, ...bare } = modern;
  assert.equal(parseSources([bare])[0].citation, 'S1');
  assert.deepEqual(
    parseSources([{ ...modern, knowledge_base_id: '../bad' }]),
    [],
  );
  assert.deepEqual(parseSources([{ ...modern, version: -1 }]), []);
  assert.deepEqual(
    parseSources([{ ...modern, knowledge_base_id: 'x'.repeat(65) }]),
    [],
  );
  // Sources without a knowledge-base identity never render as links.
  const { knowledge_base_id: _kb, version: _v, ...orphan } = modern;
  assert.deepEqual(parseSources([orphan]), []);
});

void test('proxy preserves PDF bytes and authenticated download headers', async () => {
  const { POST } = await import('../app/api/[...path]/route.ts');
  const originalFetch = globalThis.fetch;
  const bytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0xff, 0x80, 0x00]);
  globalThis.fetch = async (_url, init) => {
    assert.deepEqual(
      new Uint8Array(await new Response(init?.body).arrayBuffer()),
      bytes,
    );
    assert.equal(new Headers(init?.headers).get('cookie'), 'session=test');
    return new Response(bytes, {
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Security-Policy': 'sandbox',
        'Content-Disposition': 'inline; filename="test.pdf"',
      },
    });
  };
  try {
    const response = await POST(
      new Request('http://localhost/api/knowledge-bases/base/documents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/pdf', cookie: 'session=test' },
        body: bytes,
      }),
    );
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
    assert.equal(response.headers.get('content-security-policy'), 'sandbox');
    assert.equal(
      response.headers.get('content-disposition'),
      'inline; filename="test.pdf"',
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

void test('proxy rejects over-limit bodies even without content-length', async () => {
  const { POST } = await import('../app/api/[...path]/route.ts');
  const response = await POST(
    new Request('http://localhost/api/knowledge-bases/base/documents', {
      method: 'POST',
      body: new Uint8Array(25 * 1024 * 1024 + 1),
    }),
  );
  assert.equal(response.status, 413);
});

void test('raw KB uploads and proxy preserve replay identity', async () => {
  const { uploadDocument } = await import('../lib/knowledge-hub.ts');
  const { POST } = await import('../app/api/[...path]/route.ts');
  const originalFetch = globalThis.fetch;
  const file = new File(['hello'], 'a b.txt');
  try {
    globalThis.fetch = async (url, init) => {
      assert.equal(
        url,
        '/api/knowledge-bases/kb/documents?filename=a+b.txt&replace_document_id=old',
      );
      assert.equal(
        new Headers(init?.headers).get('idempotency-key'),
        'replay-key',
      );
      assert.equal(init?.body, file);
      return Response.json({ id: 'doc' }, { status: 202 });
    };
    assert.equal(
      (await uploadDocument('kb', file, 'replay-key', 'old')).id,
      'doc',
    );
    globalThis.fetch = async (_url, init) => {
      assert.equal(
        new Headers(init?.headers).get('idempotency-key'),
        'replay-key',
      );
      return Response.json({ id: 'doc' }, { status: 202 });
    };
    assert.equal(
      (
        await POST(
          new Request('http://localhost/api/knowledge-bases/kb/documents', {
            method: 'POST',
            headers: { 'Idempotency-Key': 'replay-key' },
            body: file,
          }),
        )
      ).status,
      202,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
