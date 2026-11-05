import test from 'node:test';
import assert from 'node:assert/strict';
import { usageLabel } from '../lib/run-status.ts';

// TA06: a call with missing usage is not shown as free work.
void test('an event shows incomplete or unknown token counts, never a bare 0+0', () => {
  assert.equal(usageLabel(undefined), '');
  assert.equal(usageLabel({ calls: 1, prompt_tokens: 12, completion_tokens: 7 }), '12+7 tok');
  const partial = usageLabel({ calls: 1, prompt_tokens: 0, completion_tokens: 7, partial_calls: 1 });
  assert.match(partial, /^0\+7 tok/);
  assert.match(partial, /incomplete/);
  const unreported = usageLabel({ calls: 1, prompt_tokens: 0, completion_tokens: 0, unreported_calls: 1 });
  assert.doesNotMatch(unreported, /^0\+0 tok$/);
  assert.match(unreported, /not reported|incomplete/);
  const unanswered = usageLabel({ calls: 0, prompt_tokens: 0, completion_tokens: 0, unanswered_calls: 1 });
  assert.doesNotMatch(unanswered, /0\+0/);
  assert.match(unanswered, /unknown/);
});
