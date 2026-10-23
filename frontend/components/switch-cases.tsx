'use client';
import { ConfigField } from './platform-settings';
import {
  addCase, caseEdges, MAX_CASES, moveCase, switchCases, unreachableCases, updateCase,
} from '@/lib/switch';
import { conditionSettingVisible, withConditionOperator, type ConfigProperty } from '@/lib/workflow';

type Edge = { id: string; source: string; sourceHandle?: string | null };

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
  const cases = switchCases(config);
  const unreachable = unreachableCases(config);
  return (
    <section className="settings-section switch-cases" aria-label="Switch cases">
      <h3>CASES</h3>
      <p className="helper">
        Checked in order. The first matching case wins; if none matches, the run takes Default.
        Each case is a Condition rule: operator, an optional field such as order.amount, and the
        value to compare. A case name grants no permission.
      </p>
      {cases.map((item, index) => {
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
              <button className="text-button" disabled={disabled || index === cases.length - 1}
                onClick={() => onChange(moveCase(config, item.id, 1))}>Move down</button>
              <button className="text-button danger" disabled={disabled || cases.length <= 1}
                onClick={() => onRemove(item.id)}>
                {connected ? 'Remove case and its connection' : 'Remove case'}
              </button>
            </div>
          </fieldset>
        );
      })}
      <button className="button" disabled={disabled || cases.length >= MAX_CASES} onClick={() => onChange(addCase(config))}>
        Add case
      </button>
      {cases.length >= MAX_CASES && <p className="helper">A Switch has at most {MAX_CASES} cases plus Default.</p>}
    </section>
  );
}
