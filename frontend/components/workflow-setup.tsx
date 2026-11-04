'use client';
import { useState } from 'react';
import {
  ACCEPT_QUOTED_RISK,
  createRequestWorkflow,
  requestTemplates,
} from '@/lib/request-routing';
import type { Workflow } from '@/lib/workflow';
import { ConfigField } from './platform-settings';

export default function WorkflowSetup({
  onCreate,
  disabled,
}: {
  onCreate: (workflow: Workflow) => void;
  disabled: boolean;
}) {
  const [template, setTemplate] = useState('qa');
  const [draft, setDraft] = useState(() => createRequestWorkflow('qa'));
  const reader = draft.nodes.find((n) => n.type === 'request_reader')!;
  const update = (id: string, key: string, value: unknown) =>
    setDraft((previous) => ({
      ...previous,
      nodes: previous.nodes.map((n) =>
        n.id === id ? { ...n, config: { ...n.config, [key]: value } } : n,
      ),
    }));
  return (
    <form
      className="inspector-form workflow-setup"
      onSubmit={(e) => {
        e.preventDefault();
        onCreate(draft);
      }}
    >
      <p className="helper">
        Configure what this workflow accepts. The Request reader asks one model
        to read the request, then checks every value in code; code chooses the
        branch. These are draft examples, not certified model accuracy.
      </p>
      <label>
        Workflow template
        <select
          aria-label="Workflow template"
          disabled={disabled}
          value={template}
          onChange={(e) => {
            setTemplate(e.target.value);
            setDraft(createRequestWorkflow(e.target.value));
          }}
        >
          {requestTemplates.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Purpose
        <textarea
          aria-label="Purpose"
          rows={4}
          required
          maxLength={2000}
          disabled={disabled}
          value={String(reader.config.purpose)}
          onChange={(e) => update(reader.id, 'purpose', e.target.value)}
        />
      </label>
      {(
        [
          'accepted_requests',
          'examples',
          'excluded_requests',
          'prepared_replies',
        ] as const
      ).map((key) => (
        <ConfigField
          key={template + key}
          name={key}
          property={{
            type: 'array',
            title: {
              accepted_requests: 'Accepted requests',
              examples: 'Valid request examples',
              excluded_requests: 'Excluded requests',
              prepared_replies: 'Prepared replies',
            }[key],
            description:
              key === 'prepared_replies'
                ? 'JSON list of {"message", "reply"}. An exact message gets its reply with no model call.'
                : 'JSON list of descriptions. Required facts are set below, not here.',
          }}
          value={reader.config[key]}
          disabled={disabled}
          onChange={(v) => update(reader.id, key, v)}
        />
      ))}
      {
        <section aria-label="Extraction acceptance rules">
          <h3>Required facts and checks</h3>
          <p className="helper">{ACCEPT_QUOTED_RISK}</p>
          <p className="helper">
            Quotation proves where a value appeared, not what it means. Where
            this template includes a reference-record read, select its real
            connection and query before running. Empty or unsuitable record
            evidence requires review; a failed read stops execution.
          </p>
          {(
            [
              'required_fields',
              'required_any',
              'field_rules',
              'expected_schema',
              'cross_checks',
            ] as const
          ).map((key) => (
            <ConfigField
              key={template + key}
              name={key}
              property={{
                type: key === 'expected_schema' ? 'object' : 'array',
                title: {
                  required_fields: 'Required fields',
                  required_any: 'Required, one group of',
                  field_rules: 'Field rules',
                  expected_schema: 'Expected JSON schema',
                  cross_checks: 'Cross checks',
                }[key],
              }}
              value={reader.config[key]}
              disabled={disabled}
              onChange={(v) => update(reader.id, key, v)}
            />
          ))}
        </section>
      }
      <p className="helper">
        You can save an unfinished draft. Invalid JSON or missing rules must be
        corrected before Validate or Run.
      </p>
      <button
        className="button primary"
        type="submit"
        disabled={disabled || !String(reader.config.purpose).trim()}
      >
        Create workflow
      </button>
    </form>
  );
}
