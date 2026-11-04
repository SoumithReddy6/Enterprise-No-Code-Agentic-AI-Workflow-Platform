// How an incomplete run, and the events behind it, are explained in the run view.
//
// The backend records one truncation cause per node: what kind of work stopped short
// (a loop, a generated answer, a capped tool result, or a node refused by the action budget), how
// certain that is (confirmed, or unverifiable because an older Relay version wrote the
// checkpoint), and why. These helpers turn that into what a person reads, so the reason
// is visible without opening the execution log.

export type TruncationKind = 'loop' | 'agent' | 'tool' | 'budget';
export type TruncationSource = 'confirmed' | 'legacy_checkpoint_unverified';
export type TruncationCause = { node_id: string; kind: TruncationKind; source: TruncationSource; reason: string };

export type RunCompleteness = {
  truncated?: boolean;
  truncation_reason?: string;
  truncation_source?: string;
  truncation_causes?: TruncationCause[];
};

export type CauseLine = { node: string; label: string; unverified: boolean; reason: string };
export type IncompleteRun = { title: string; certainty: string; unverified: boolean; causes: CauseLine[] };

const KIND_LABELS: Record<string, string> = {
  loop: 'Loop did not deliver every item',
  agent: 'Generated answer is incomplete',
  tool: 'Tool returned capped results',
  budget: 'Skipped by the action budget',
};
const UNVERIFIED = 'legacy_checkpoint_unverified';

export function incompleteRun(run: RunCompleteness | undefined): IncompleteRun | null {
  if (!run?.truncated) return null;
  const recorded = run.truncation_causes?.length
    ? run.truncation_causes
    : // Runs finished before causes were recorded still carry the joined reason.
      [{ node_id: '', kind: '', source: run.truncation_source || 'confirmed', reason: run.truncation_reason || '' }];
  const causes = recorded
    .map((cause) => ({
      node: cause.node_id,
      label: KIND_LABELS[cause.kind] || 'Work was incomplete',
      unverified: cause.source === UNVERIFIED,
      reason: cause.reason || 'The run did not deliver all of its work.',
    }))
    // Confirmed causes first: they are what certainly happened.
    .sort((a, b) => Number(a.unverified) - Number(b.unverified));
  const unverified = run.truncation_source === UNVERIFIED;
  return {
    title: 'Incomplete run',
    certainty: unverified
      ? 'Unverified: this run was checkpointed by an older Relay version, so its completeness cannot be proven.'
      : 'Confirmed: some work was skipped or cut short.',
    unverified,
    causes,
  };
}

export type EventSummary = {
  status: string;
  error?: string;
  reason?: string;
  recovered?: boolean;
  budget_exhausted?: boolean;
  items_truncated?: boolean;
  items_warnings?: string[];
  outputs?: Record<string, unknown>;
  preview_of?: string[];
  preview_omitted?: number;
  omitted_fields?: string[];
};

// Short notes shown above an event's raw detail in the execution log.
export function eventNotes(event: EventSummary): string[] {
  const notes: string[] = [];
  if (event.status === 'not_run') notes.push(`Skipped: ${event.reason || 'the action budget was exhausted'}`);
  else if (event.budget_exhausted)
    notes.push(event.recovered ? 'Recovered: refused by the action budget, so its work is missing from this run.' : 'Refused by the action budget.');
  const truncation = event.outputs?._truncation as { truncation_reason?: string } | undefined;
  if (truncation?.truncation_reason) notes.push(`Incomplete result: ${truncation.truncation_reason}`);
  else if (event.items_truncated) notes.push('Jira items are incomplete (result limit or additional pages).');
  if (event.items_warnings?.length) notes.push(event.items_warnings.join(' '));
  const results = event.outputs?.results as { stored_in?: string; count?: number; unavailable?: string } | undefined;
  if (results && !Array.isArray(results) && results.stored_in) {
    const count = results.count ?? 0;
    notes.push(results.unavailable ? results.unavailable : `${count} ${count === 1 ? 'result is' : 'results are'} stored per item in the run data.`);
  }
  const shortened = (event.preview_of?.length || 0) + (event.preview_omitted || 0);
  if (shortened) notes.push(`${shortened} ${shortened === 1 ? 'field in this log entry was' : 'fields in this log entry were'} shortened for storage.`);
  if (event.omitted_fields?.length) notes.push(`Omitted from this log entry to keep it small: ${event.omitted_fields.join(', ')}.`);
  return notes;
}
