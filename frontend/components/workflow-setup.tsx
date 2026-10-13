'use client';
import { useState } from 'react';
import { createRequestWorkflow, requestTemplates } from '@/lib/request-routing';
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
  const router = draft.nodes.find((n) => n.type === 'request_router')!;
  const check = draft.nodes.find((n) => n.type === 'verify_extraction');
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
        Configure what this workflow accepts. The router admits a request;
        business facts still need their own checks. These are draft examples,
        not certified model accuracy.
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
          value={String(router.config.purpose)}
          onChange={(e) => update(router.id, 'purpose', e.target.value)}
        />
      </label>
      {(
        [
          'accepted_requests',
          'examples',
          'exclusions',
          'clarification_rules',
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
              exclusions: 'Exclusions',
              clarification_rules: 'Clarification rules',
            }[key],
            description:
              'JSON list of descriptions. Missing business facts must also be required by extraction checks.',
          }}
          value={router.config[key]}
          disabled={disabled}
          onChange={(v) => update(router.id, key, v)}
        />
      ))}
      {check && (
        <section aria-label="Extraction acceptance rules">
          <h3>Required facts and checks</h3>
          <p className="helper">
            Quotation proves where a value appeared, not what it means. Where
            this template includes a reference-record read, select its real
            connection and query before running. Empty or unsuitable record
            evidence requires review; a failed read stops execution.
          </p>
          {(
            [
              'required_fields',
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
                  field_rules: 'Field rules',
                  expected_schema: 'Expected JSON schema',
                  cross_checks: 'Cross checks',
                }[key],
              }}
              value={check.config[key]}
              disabled={disabled}
              onChange={(v) => update(check.id, key, v)}
            />
          ))}
        </section>
      )}
      <p className="helper">
        You can save an unfinished draft. Invalid JSON or missing rules must be
        corrected before Validate or Run.
      </p>
      <button
        className="button primary"
        type="submit"
        disabled={disabled || !String(router.config.purpose).trim()}
      >
        Create workflow
      </button>
    </form>
  );
}
