export type RunApproval = {
  id: string;
  node_id: string;
  status: string;
  payload_json: string;
  expires_at: number;
  kind?: string;
};

// Hash the exact UTF-8 preview, never a reserialized JavaScript object.
export async function approvalDecision(
  approval: RunApproval,
  choice?: boolean,
  action: 'approve' | 'reject' = 'approve',
) {
  const kind = approval.kind ?? 'write';
  if (!['write', 'decision', 'verification'].includes(kind))
    throw new Error('Unknown review kind. Reload before deciding.');
  if (
    action === 'approve' &&
    kind === 'decision' &&
    typeof choice !== 'boolean'
  )
    throw new Error(
      'Decision review requires an explicit true or false choice.',
    );
  if (kind !== 'decision' && choice !== undefined)
    throw new Error('Only a decision review accepts a branch choice.');
  const bytes = new TextEncoder().encode(approval.payload_json);
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  const digest = Array.from(new Uint8Array(hash), (n) =>
    n.toString(16).padStart(2, '0'),
  ).join('');
  return {
    approval_id: approval.id,
    digest,
    ...(kind === 'decision' && action === 'approve' ? { choice } : {}),
  };
}
