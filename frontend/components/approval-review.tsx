import type { RunApproval } from '@/lib/approvals';

export default function ApprovalReview({ approval, disabled, onDecision }: {
  approval: RunApproval;
  disabled: boolean;
  onDecision: (approval: RunApproval, action: 'approve' | 'reject') => void;
}) {
  return (
    <section aria-label="Review outgoing action">
      <h3>Approval required: {approval.node_id}</h3>
      <p>Review the complete outgoing request before allowing this action.</p>
      <p>Expires {new Date(approval.expires_at * 1000).toLocaleString()}</p>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{approval.payload_json}</pre>
      <button disabled={disabled} onClick={() => onDecision(approval, 'approve')}>Approve and continue</button>
      <button disabled={disabled} onClick={() => onDecision(approval, 'reject')}>Reject action</button>
    </section>
  );
}
