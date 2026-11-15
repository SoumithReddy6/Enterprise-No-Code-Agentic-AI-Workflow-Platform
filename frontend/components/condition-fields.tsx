'use client';
import { ConfigField } from './platform-settings';
import { conditionSettingVisible, withConditionOperator, type ConfigProperty } from '@/lib/workflow';

/**
 * The fields of one Condition rule (operator, field and the operands the operator uses), as
 * a Switch case and a Repeat's stop rule edit it. Changing the operator drops the operands
 * the new operator does not use, so a stale operand cannot make the rule invalid.
 */
export default function ConditionFields({
  idPrefix,
  title,
  predicate,
  rule,
  disabled,
  onChange,
}: {
  /** Keeps React keys unique when several rules are shown. */
  idPrefix: string;
  /** Prefixed to each field's label, e.g. "Case 1" or "Stop rule". */
  title: string;
  predicate: Record<string, ConfigProperty>;
  rule: Record<string, unknown>;
  disabled: boolean;
  onChange: (rule: Record<string, unknown>) => void;
}) {
  return (
    <>
      {Object.entries(predicate)
        .filter(([key]) => conditionSettingVisible(rule, key))
        .map(([key, property]) => (
          <ConfigField
            key={`${idPrefix}:${key}`}
            name={key}
            // The rule help is shown once above the fields, not repeated on each.
            property={{ ...property, title: `${title} ${property.title || key}`, description: undefined }}
            value={rule[key]}
            disabled={disabled}
            onChange={(value) => {
              if (key === 'operator') {
                onChange(withConditionOperator(rule, String(value)));
                return;
              }
              const next = { ...rule };
              if (value === undefined) delete next[key];
              else next[key] = value;
              onChange(next);
            }}
          />
        ))}
    </>
  );
}
