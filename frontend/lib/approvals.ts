export type RunApproval = {
  id: string;
  node_id: string;
  status: string;
  payload_json: string;
  expires_at: number;
};

// Hash the exact UTF-8 preview, never a reserialized JavaScript object.
export async function approvalDecision(approval: RunApproval) {
  const bytes = new TextEncoder().encode(approval.payload_json);
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  const digest = Array.from(new Uint8Array(hash), (n) => n.toString(16).padStart(2, '0')).join('');
  return { approval_id: approval.id, digest };
}
