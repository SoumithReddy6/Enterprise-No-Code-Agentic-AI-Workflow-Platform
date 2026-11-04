import { useEffect, useState } from 'react';
import type { RunApproval } from '@/lib/approvals';
import { reviewState } from '../lib/request-routing.ts';

export default function ApprovalReview({
  approval,
  disabled,
  onDecision,
}: {
  approval: RunApproval;
  disabled: boolean;
  onDecision: (
    approval: RunApproval,
    action: 'approve' | 'reject',
    choice?: boolean,
  ) => void;
}) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  const kind = approval.kind ?? 'write';
  const state = reviewState(approval, now);
  const blocked = disabled || !state.canDecide;
  const title =
    kind === 'verification'
      ? 'Review extracted interpretation'
      : kind === 'decision'
        ? 'Review business decision'
        : 'Review outgoing action';
  // Only use outer strings for the comparison. Parsing/re-encoding the embedded
  // numeric value would round it; the exact payload below remains the digest input.
  let payload: Record<string, unknown> | null = null;
  try {
    const parsed: unknown = JSON.parse(approval.payload_json);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed))
      payload = parsed as Record<string, unknown>;
  } catch {
    /* The disabled state explains malformed payloads. */
  }
  return (
    <section className="approval-review" aria-label={title}>
      <h3>
        {title}: {approval.node_id}
      </h3>
      <p>
        {kind === 'verification'
          ? 'Compare the original input with the extracted value and field checks below. Confirming accepts this interpretation; it does not make the automatic checks pass.'
          : kind === 'decision'
            ? 'Review the original input, resolved values, policy and proposed choice. Select the branch yourself; rejection ends the run.'
            : 'Review the complete outgoing request before allowing this action.'}
      </p>
      <output>{state.message}</output>
      <p>Expires {new Date(approval.expires_at * 1000).toLocaleString()}</p>
      {kind !== 'write' && typeof payload?.original === 'string' && (
        <section aria-label="Original input for review">
          <strong>Original input</strong>
          <pre>{payload.original}</pre>
        </section>
      )}
      {kind === 'verification' && typeof payload?.value === 'string' && (
        <section aria-label="Extracted value for review">
          <strong>Extracted value</strong>
          <pre>{payload.value}</pre>
        </section>
      )}
      <p className="helper">
        Complete payload, exactly as stored and used for the confirmation
        digest:
      </p>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {approval.payload_json}
      </pre>
      <div className="review-actions">
        {kind === 'decision' ? (
          <>
            <button
              className="button secondary"
              disabled={blocked}
              onClick={() => onDecision(approval, 'approve', true)}
            >
              Choose true and continue
            </button>
            <button
              className="button secondary"
              disabled={blocked}
              onClick={() => onDecision(approval, 'approve', false)}
            >
              Choose false and continue
            </button>
          </>
        ) : (
          <button
            className="button primary"
            disabled={blocked}
            onClick={() => onDecision(approval, 'approve')}
          >
            {kind === 'verification'
              ? 'Confirm interpretation'
              : 'Approve and continue'}
          </button>
        )}
        <button
          className="button secondary"
          disabled={blocked}
          onClick={() => onDecision(approval, 'reject')}
        >
          {kind === 'verification'
            ? 'Reject interpretation'
            : kind === 'decision'
              ? 'Reject review'
              : 'Reject action'}
        </button>
      </div>
    </section>
  );
}
