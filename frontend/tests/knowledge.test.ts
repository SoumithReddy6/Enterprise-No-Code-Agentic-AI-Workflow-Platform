import test from 'node:test';
import assert from 'node:assert/strict';
import {
  pdfWorkflow,
  parseSources,
  documentLink,
  sourcesForRun,
} from '../lib/knowledge.ts';

void test('PDF starter binds every retrieval and answer port', () => {
  const workflow = pdfWorkflow('base123', {
    provider: 'ollama',
    model: 'test',
  });
  assert.deepEqual(
    workflow.nodes.map((n) => n.type),
    ['chat_input', 'retrieval', 'grounded_answer', 'response'],
  );
  assert.deepEqual(workflow.nodes[1].config, {
    knowledge_base_id: 'base123',
    limit: 4,
  });
  assert.deepEqual(workflow.nodes[2].inputs, {
    query: 'retrieve.query',
    context: 'retrieve.context',
    sources: 'retrieve.sources',
  });
  assert.equal(workflow.nodes[3].inputs.text, 'answer.text');
});
const source = {
  id: 'chunk123',
  document_id: 'doc123',
  filename: '<script>.pdf',
  page: 2,
  text: '[evil](javascript:alert(1))',
  score: 1,
  citation: 'S1',
  cited: true,
};
void test('sources preserve plain strings and links only use bounded identifiers', () => {
  assert.deepEqual(parseSources(JSON.stringify([source])), [source]);
  assert.equal(documentLink(source), '/api/documents/doc123/file#page=2');
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
  const workflow = pdfWorkflow('base123', {});
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
      new Request('http://localhost/api/knowledge/base/documents', {
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
    new Request('http://localhost/api/knowledge/base/documents', {
      method: 'POST',
      body: new Uint8Array(25 * 1024 * 1024 + 1),
    }),
  );
  assert.equal(response.status, 413);
});

void test('vector sources use tenant vector-file routes and query events', () => {
  const vector = { ...source, resource_id: 'resource123' };
  assert.equal(documentLink(vector), '/api/vector-files/doc123/file#page=2');
  const workflow = pdfWorkflow('base123', {});
  workflow.nodes[2].type = 'query';
  assert.equal(
    sourcesForRun({ workflow }, [
      {
        status: 'success',
        node_id: 'answer',
        outputs: { sources: [vector] },
        seq: 1,
        timestamp: '',
      },
    ]).length,
    1,
  );
});

void test('unified KB sources retain version and build bounded original links', () => {
  const modern = {
    ...source,
    knowledge_base_id: 'kb_123',
    version: 2,
    url: 'javascript:bad',
  };
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
