import { admissionDetails } from '@/lib/request-routing';
import type { Run, RunEvent } from '@/lib/workflow';

export default function RequestChecks({
  run,
  events,
}: {
  run?: Run;
  events: RunEvent[];
}) {
  if (!run) return null;
  const details = run.workflow.nodes.flatMap((node) => {
    const event = events
      .filter((e) => e.node_id === node.id && e.status === 'success')
      .at(-1);
    const detail = event ? admissionDetails(event, node.type) : null;
    return detail ? [{ node, detail, event }] : [];
  });
  if (!details.length) return null;
  return (
    <section className="request-checks" aria-label="Request checks">
      <h3>Request checks</h3>
      {details.map(({ node, detail, event }) => (
        <section key={node.id}>
          <strong>
            {node.label || node.id} · {detail.title}
          </strong>
          <p>{detail.explanation}</p>
          {['verify_extraction', 'request_reader'].includes(node.type) && (
            <>
              <details>
                <summary>Original input for these checks</summary>
                <pre>{run.message}</pre>
              </details>
              {(() => {
                const checkpoint = run.checkpoints?.[node.id];
                const value = checkpoint?.text ?? event?.outputs?.text;
                const preview =
                  !checkpoint && event?.preview_of?.includes('outputs.text');
                return typeof value === 'string' ? (
                  <details>
                    <summary>
                      {preview
                        ? 'Stored preview of checked value'
                        : 'Checked value'}
                    </summary>
                    <pre>{value}</pre>
                  </details>
                ) : null;
              })()}
            </>
          )}
          {detail.missingFields.length > 0 && (
            <p>Missing fields: {detail.missingFields.join(', ')}</p>
          )}
          {!!detail.fields.length && (
            <ul className="field-checks">
              {detail.fields.map((field, i) => (
                <li key={i}>
                  <span className="mono">{field.field || '(whole value)'}</span>{' '}
                  · <strong>{field.status}</strong>:{' '}
                  {field.reason.replaceAll('_', ' ')}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </section>
  );
}
