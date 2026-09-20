import test from 'node:test';
import assert from 'node:assert/strict';
import { seed, sameExecutionGraph, api, ApiError } from '../lib/workflow.ts';

void test('save conflict retains the server version and submitted edits', async (t) => {
  const local = structuredClone(seed);
  local.name = 'Local edits';
  const current = { id: 'one', workflow: seed, updated_at: 'new-version' };
  t.mock.method(globalThis, 'fetch', async (_url: unknown, options: RequestInit) => {
    assert.equal(typeof options.body, 'string');
    assert.equal(JSON.parse(options.body as string).updated_at, 'old-version');
    return new Response(JSON.stringify({ detail: 'This workflow changed elsewhere.', current }), { status: 409 });
  });
  await assert.rejects(api('/workflows/one', 'PUT', { ...local, updated_at: 'old-version' }), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 409);
    assert.deepEqual(error.data, { detail: 'This workflow changed elsewhere.', current });
    return true;
  });
  assert.equal(local.name, 'Local edits');
});

void test('KB name conflicts expose the service message and original name', async (t) => {
  const detail = { message: 'A knowledge base with this name already exists.', name: ' First ' };
  t.mock.method(globalThis, 'fetch', async () => new Response(JSON.stringify({ detail }), { status: 409 }));
  await assert.rejects(api('/knowledge-bases', 'POST', { name: detail.name }), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.message, detail.message);
    assert.deepEqual(error.data, { detail });
    return true;
  });
});

void test('server normalization and layout changes preserve execution highlighting', () => {
  const normalized = structuredClone(seed);
  normalized.edges.forEach((e) => (e.sourceHandle = null));
  normalized.nodes[0].position = { x: 999, y: 999 };
  normalized.name = 'Renamed';
  assert.equal(sameExecutionGraph(seed, normalized), true);
});

void test('changing a node configuration invalidates old execution highlighting', () => {
  const changed = structuredClone(seed);
  changed.nodes[1].config.template = 'Different prompt: {message}';
  assert.equal(sameExecutionGraph(seed, changed), false);
});

import { importableWorkflow, saveCompletion } from '../lib/workflow.ts';

void test('legacy VectorDB imports explain how to migrate', () => {
  const old = structuredClone(seed);
  old.nodes[1].type = 'vector_faiss';
  assert.throws(() => importableWorkflow(old, ['chat_input','agent','response']), /retired VectorDB nodes/);
});

void test('save completion keeps newer edits dirty and does not change another document', () => {
  const newer = structuredClone(seed);
  newer.name = 'Edited while saving';
  assert.deepEqual(saveCompletion(1, 1, seed, newer), {
    sameDocument: true,
    dirty: true,
  });
  assert.deepEqual(saveCompletion(1, 2, seed, seed), {
    sameDocument: false,
    dirty: false,
  });
  assert.deepEqual(saveCompletion(1, 1, seed, seed), {
    sameDocument: true,
    dirty: false,
  });
});

void test('import rejects unsupported node types even when validation endpoint returned a document', () => {
  const unsafe = structuredClone(seed);
  unsafe.nodes[0].type = '__proto__';
  assert.throws(
    () =>
      importableWorkflow(unsafe, [
        'chat_input',
        'prompt',
        'llm',
        'agent',
        'response',
      ]),
    /Unsupported node type/,
  );
});

void test('import preserves a draft with known nodes for repair in the editor', () => {
  const draft = structuredClone(seed);
  draft.edges = [];
  assert.equal(
    importableWorkflow(draft, [
      'chat_input',
      'prompt',
      'llm',
      'agent',
      'response',
    ]),
    draft,
  );
});

void test('attachment kinds and target handles affect execution identity', () => {
  const changed = structuredClone(seed);
  changed.edges[0] = {
    ...changed.edges[0],
    kind: 'tool',
    targetHandle: 'tools',
  };
  assert.equal(sameExecutionGraph(seed, changed), false);
});
void test('new starter is a vertical input agent response flow', () => {
  assert.deepEqual(
    seed.nodes.map((n) => n.type),
    ['chat_input', 'agent', 'response'],
  );
  assert.equal(seed.nodes[1].inputs.input, 'input.message');
  assert.ok(seed.nodes[0].position.y < seed.nodes[1].position.y);
});

import { connectionKind, paletteCategory } from '../lib/workflow.ts';
void test('typed attachments reject incompatible endpoints and preserve normal flow', () => {
  assert.equal(connectionKind('tool_http', 'agent', null, 'tools'), 'tool');
  assert.equal(connectionKind('agent', 'agent', 'agents', null), 'agent');
  assert.equal(connectionKind('retrieve', 'agent', null, 'tools'), 'tool');
  assert.equal(connectionKind('chat_input', 'agent', null, 'tools'), null);
  assert.equal(connectionKind('agent', 'response', null, null), 'flow');
  assert.equal(paletteCategory('llm'), null);
  assert.equal(paletteCategory('retrieve'), 'Retrieval');
});

void test('retrieval flow binds evidence envelope to Agent', async () => {
  const { flowBinding, paletteCategory, seed } =
    await import('../lib/workflow.ts');
  const from = { ...seed.nodes[0], type: 'retrieve' };
  const target = { ...seed.nodes[1], inputs: {} };
  const definition = {
    type: 'retrieve',
    name: '',
    category: '',
    description: '',
    inputs: {},
    outputs: { query: 'string', context: 'string', sources: 'string' },
    config_schema: { properties: {} },
  };
  assert.deepEqual(
    flowBinding(from, target, definition, {
      ...definition,
      inputs: { input: 'string' },
    }),
    { port: 'input', output: 'context' },
  );
  assert.equal(paletteCategory('prompt'), null);
  assert.deepEqual(
    flowBinding(
      { ...from, type: 'chat_input' },
      target,
      { ...definition, outputs: { message: 'string' } },
      { ...definition, inputs: { input: 'string' } },
    ),
    { port: 'input', output: 'message' },
  );
});
