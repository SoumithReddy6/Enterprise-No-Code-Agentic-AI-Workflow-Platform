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

// The node types a label or note depends on: the run's own workflow.
export type NodeTypes = { nodes: { id: string; type: string }[] };
const typeOf = (workflow: NodeTypes | undefined, id: string | undefined) =>
  workflow?.nodes.find((n) => n.id === id)?.type;

export function incompleteRun(run: RunCompleteness | undefined, workflow?: NodeTypes): IncompleteRun | null {
  if (!run?.truncated) return null;
  const recorded = run.truncation_causes?.length
    ? run.truncation_causes
    : // Runs finished before causes were recorded still carry the joined reason.
      [{ node_id: '', kind: '', source: run.truncation_source || 'confirmed', reason: run.truncation_reason || '' }];
  const causes = recorded
    .map((cause) => ({
      node: cause.node_id,
      // A Repeat delivered one answer, which did not meet its rule; its reason says which stop.
      label: cause.kind === 'loop' && typeOf(workflow, cause.node_id) === 'repeat'
        ? 'Repeat did not meet its stop rule'
        : KIND_LABELS[cause.kind] || 'Work was incomplete',
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
  node_id?: string;
  kind?: string;
  loop_node_id?: string;
  item_index?: number;
  item_result?: unknown;
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

// Card statuses: the latest event of each node. Loop item and summary events are about the
// loop's progress, not its status (a Repeat stays running between iterations).
export function nodeStatuses(events: EventSummary[]): Record<string, string> {
  return Object.fromEntries(
    events.filter((e) => e.node_id && e.kind !== 'loop_item' && e.kind !== 'loop_summary').map((e) => [e.node_id, e.status]),
  );
}

// An execution log title: the node, and for work inside a loop the iteration (Repeat) or
// item (For each) it belongs to, counted from 1.
export function eventTitle(event: EventSummary, workflow?: NodeTypes): string {
  const name = event.node_id || 'Workflow';
  if (!event.loop_node_id || typeof event.item_index !== 'number') return name;
  const unit = typeOf(workflow, event.loop_node_id) === 'repeat' ? 'iteration' : 'item';
  return `${name} · ${unit} ${event.item_index + 1}`;
}

// One note per Repeat iteration row. The stored copy of a loop event is bounded, so a large
// item_result can arrive as a string: a verdict that cannot be read is said to be
// unavailable, never taken as "not met".
function iterationNote(event: EventSummary): string {
  const number = (event.item_index ?? 0) + 1;
  const row = event.item_result && typeof event.item_result === 'object' ? event.item_result as { status?: unknown; met?: unknown } : null;
  if (row?.status === 'interrupted') return `Iteration ${number} was stopped by the run budget before it was checked.`;
  if (row?.status === 'abstained') return `Iteration ${number} stopped: the answerability guard abstained.`;
  if (row?.met === true) return `Iteration ${number} met the stop rule.`;
  if (row?.met === false) return `Iteration ${number} did not meet the stop rule.`;
  return `Iteration ${number} finished. Verdict details are unavailable.`;
}

// Why a Repeat stopped, from its summary output. A summary that does not parse gives none.
function stopNote(summary: unknown): string | null {
  let s: { iterations?: number; max_iterations?: number; stopped_by?: string };
  try {
    s = typeof summary === 'string' ? JSON.parse(summary) : null;
  } catch {
    return null;
  }
  if (!s || typeof s !== 'object') return null;
  const n = s.iterations ?? 0, m = s.max_iterations ?? 0;
  switch (s.stopped_by) {
    case 'rule': return `Stopped by its rule after ${n} of ${m} ${m === 1 ? 'iteration' : 'iterations'}.`;
    case 'limit': return `Reached its limit of ${m} ${m === 1 ? 'iteration' : 'iterations'} without meeting its rule. Returned the answer from iteration ${n}.`;
    case 'budget': return `The run budget stopped iteration ${n + 1} before it was checked. Returned the answer from iteration ${n}.`;
    case 'guard': return 'The answerability guard stopped the loop and returned an abstention.';
    default: return null;
  }
}

// Short notes shown above an event's raw detail in the execution log.
export function eventNotes(event: EventSummary, workflow?: NodeTypes): string[] {
  const notes: string[] = [];
  if (typeOf(workflow, event.node_id) === 'repeat') {
    if (event.kind === 'loop_item') notes.push(iterationNote(event));
    else if (event.status === 'success') {
      const note = stopNote(event.outputs?.summary);
      if (note) notes.push(note);
    }
  }
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

export type EventUsage = {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  partial_calls?: number;
  unreported_calls?: number;
  unanswered_calls?: number;
};

// A call whose usage is missing is not free work: the counts shown are a lower bound,
// and a call that got no response has an unknown cost.
export function usageLabel(usage: EventUsage | undefined): string {
  if (!usage) return '';
  const unanswered = usage.unanswered_calls ?? 0;
  const missing = (usage.partial_calls ?? 0) + (usage.unreported_calls ?? 0);
  if (usage.calls === 0 && unanswered > 0) return 'tokens unknown (no response)';
  const counts = `${usage.prompt_tokens}+${usage.completion_tokens} tok`;
  if (missing === 0 && unanswered === 0) return counts;
  const unreported = (usage.unreported_calls ?? 0) === usage.calls && unanswered === 0;
  return unreported && usage.prompt_tokens + usage.completion_tokens === 0
    ? 'tokens not reported'
    : `${counts} (incomplete)`;
}
