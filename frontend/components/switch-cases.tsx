'use client';
import { ConfigField } from './platform-settings';
import {
  addCase, caseEdges, caseEntries, isSwitchCase, MAX_CASES, moveCase, removeEntry, replaceEntry,
  resetCases, unreachableCases, updateCase,
} from '@/lib/switch';
import { conditionSettingVisible, withConditionOperator, type ConfigProperty } from '@/lib/workflow';

type Edge = { id: string; source: string; sourceHandle?: string | null };

const preview = (value: unknown) => {
  const text = JSON.stringify(value) ?? String(value);
  return text.length > 200 ? `${text.slice(0, 200)}…` : text;
};

/** The ordered case list of a Switch. Ids are shown read-only: they are the route handles. */
export default function SwitchCases({
  nodeId,
  config,
  predicate,
  edges,
  disabled,
  onChange,
  onRemove,
}: {
  nodeId: string;
  config: Record<string, unknown>;
  predicate: Record<string, ConfigProperty>;
  edges: Edge[];
  disabled: boolean;
  onChange: (config: Record<string, unknown>) => void;
  onRemove: (caseId: string) => void;
}) {
  if (!Array.isArray(config.cases))
    return (
      <section className="settings-section switch-cases" aria-label="Switch cases">
        <h3>CASES</h3>
        <output className="warning-text">
          The saved cases are not a list, so they cannot be shown as cases: {preview(config.cases)}
        </output>
        <button className="button" disabled={disabled} onClick={() => onChange(resetCases(config))}>
          Start a new case list
        </button>
      </section>
    );
  const entries = caseEntries(config);
  const unreachable = unreachableCases(config);
  return (
    <section className="settings-section switch-cases" aria-label="Switch cases">
      <h3>CASES</h3>
      <p className="helper">
        Checked in order. The first matching case wins; if none matches, the run takes Default.
        Each case is a Condition rule: operator, an optional field such as order.amount, and the
        value to compare. A case name grants no permission.
      </p>
      {entries.map((item, index) => {
        if (!isSwitchCase(item))
          return (
            // A malformed entry stays in the draft until the author repairs or removes it.
            <fieldset key={`invalid-${index}`} className="switch-case" aria-label={`Case ${index + 1}`}>
              <legend>Case {index + 1}</legend>
              <output className="warning-text">
                Case {index + 1} is not valid: it needs an id, a label and a rule. Saved data: {preview(item)}
              </output>
              <div className="button-row">
                <button className="text-button" disabled={disabled} onClick={() => onChange(replaceEntry(config, index))}>
                  Replace with an empty case
                </button>
                <button className="text-button danger" disabled={disabled || entries.length <= 1}
                  onClick={() => onChange(removeEntry(config, index))}>
                  Remove
                </button>
              </div>
            </fieldset>
          );
        const connected = caseEdges(edges, nodeId, item.id).length > 0;
        const when = item.when ?? {};
        return (
          <fieldset key={item.id} className="switch-case" aria-label={`Case ${index + 1}`}>
            <legend>Case {index + 1}</legend>
            <label>
              Label
              <input
                aria-label={`Case ${index + 1} label`}
                value={item.label}
                maxLength={80}
                disabled={disabled}
                onChange={(e) => onChange(updateCase(config, item.id, { label: e.target.value }))}
              />
            </label>
            <p className="helper mono" aria-label={`Case ${index + 1} route id`}>Route id: {item.id}</p>
            {Object.entries(predicate)
              .filter(([key]) => conditionSettingVisible(when, key))
              .map(([key, property]) => (
                <ConfigField
                  key={`${item.id}:${key}`}
                  name={key}
                  // The rule help is shown once above the list, not repeated in every case.
                  property={{ ...property, title: `Case ${index + 1} ${property.title || key}`, description: undefined }}
                  value={when[key]}
                  disabled={disabled}
                  onChange={(value) => {
                    if (key === 'operator') {
                      onChange(updateCase(config, item.id, { when: withConditionOperator(when, String(value)) }));
                      return;
                    }
                    const next = { ...when };
                    if (value === undefined) delete next[key];
                    else next[key] = value;
                    onChange(updateCase(config, item.id, { when: next }));
                  }}
                />
              ))}
            {unreachable.has(index) && (
              <output className="warning-text">
                This case can never match: case {unreachable.get(index)! + 1} has the same rule.
              </output>
            )}
            <div className="button-row">
              <button className="text-button" disabled={disabled || index === 0}
                onClick={() => onChange(moveCase(config, item.id, -1))}>Move up</button>
              <button className="text-button" disabled={disabled || index === entries.length - 1}
                onClick={() => onChange(moveCase(config, item.id, 1))}>Move down</button>
              <button className="text-button danger" disabled={disabled || entries.length <= 1}
                onClick={() => onRemove(item.id)}>
                {connected ? 'Remove case and its connection' : 'Remove case'}
              </button>
            </div>
          </fieldset>
        );
      })}
      <button className="button" disabled={disabled || entries.length >= MAX_CASES} onClick={() => onChange(addCase(config))}>
        Add case
      </button>
      {entries.length >= MAX_CASES && <p className="helper">A Switch has at most {MAX_CASES} cases plus Default.</p>}
    </section>
  );
}
