# A5.1 exact-validation audit — October 6, 2026

Audited HEAD: `28c9f0bd5cf954040529eaa2c775e1ed03464862`. Runtime fix:
`5e9131711bfa23287b5e9aca9c8b13ca35b83d5d`. Both are already pushed.

**The two previous follow-up reproductions are fixed. One new P1 composition defect
remains:** an embedded schema's dialect declaration discards Relay's exact validator.
It can reject valid answers, crash a valid multiple test, or bypass a conditional
numeric bound and permit a downstream write. A smaller range-documentation mismatch
is also recorded below.

## R01 — P1: exact validation is lost when a referenced resource declares its dialect

`backend/app/exact_json.py:119–121` constructs an extended validator with Relay's
integer checker and exact `multipleOf`. The installed jsonschema implementation's
`evolve` method selects a new class with `validator_for(schema, default=cls)`.
When a resolved resource includes a recognized `$schema`, it selects the library's
registered default class, discarding both Relay overrides. This happens even when the
resource declares the same Draft 2020-12 dialect as the root.

The reproduction uses a legitimate bundled resource with its own `$id`, referenced
through a local URN. Configuration validation and `/api/validate` accept it. No remote
schema retrieval or cross-dialect interpretation is needed:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["amount"],
  "properties": {"amount": {"$ref": "urn:relay:audit:money"}},
  "$defs": {
    "Money": {
      "$id": "urn:relay:audit:money",
      "$schema": "https://json-schema.org/draft/2020-12/schema",
      "if": {"type": "integer"},
      "then": {"maximum": 999}
    }
  }
}
```

`1000.0` is an integral JSON number, so the `then` bound must reject it. The fallback
checker treats its Python Decimal representation as non-integer, skips `then`, and
accepts it. Removing only the embedded `$schema` restores the correct rejection.

**Separate-process proof:** a deterministic local Ollama HTTP fixture returned
`{"amount":1000.0}` to a real authenticated API and worker. The schema above accepted
it, a downstream `gt 999` condition took the true branch, and the local receiver got
one POST with `{"amount":1000}`. The run reported success. Without the embedded
declaration, the same answer was repaired once, then refused, with zero writes.
Approval was deliberately disabled for this synthetic test action; no real account,
recipient or external system was contacted.

Two other manifestations confirm that the overrides themselves are lost:

| Referenced resource | Answer | Without embedded `$schema` | With embedded `$schema` |
| --- | --- | --- | --- |
| `type: integer` | `1000.0` | accepted, one model call | wrongly rejected, two calls |
| `type: number, multipleOf: 0.01` | `1e30` | accepted, one model call | `InvalidOperation`; one call, generic node failure |
| `if: integer, then: maximum 999` | `1000.0` | refused after one repair, zero writes | accepted, one write |

These results reproduce directly and through separately deployed processes. The
original new tests correctly exercise the root validator; they do not test this
schema-evolution boundary.

Fix: preserve the exact checker and numeric handlers when the validator evolves or
resolves a resource. If multiple dialects are supported, each selected dialect needs
the corresponding exact variant. Avoid changing the process-wide validator registry
as a shortcut. Add paired controls with and without the same-dialect declaration,
including `$ref`, conditional bounds, large multiples, bounded repair and downstream
write suppression.

Evidence: [schema-resource results](../../evals/results/a51-exact-validation-2026-10-06/schema-resources-results.json)
and [standalone probe](../../evals/results/a51-exact-validation-2026-10-06/probe_schema_resources.py).
The initial availability-only reproduction is retained separately as
`initial-schema-resources-results.json`.

## R02 — P3: the documented upper magnitude differs from the enforced bound

The new documentation and error text say magnitudes stop at `1e1000`. `_decimal`
actually permits adjusted exponents through 1000, so it accepts `9e1000` and
`1.00000000000000001e1000`. `_integer` similarly accepts `2 * 10**1000` because it has
1,001 digits. Nonzero supported values therefore remain below `1e1001`, rather than
at or below `1e1000`.

This does not remove the arithmetic bound or recreate the enormous-exponent failure.
Clarify the documented contract as an exponent limit, or enforce the stated absolute
magnitude, and pin the chosen boundary with coefficient-aware tests. Zero is naturally
handled separately.

## Closure of the previous findings and fresh checks

The original boundary probe passes all eight rows. The original deployed probe passes
all five rows: numeric schema violation now fails before writing, and unsupported
exponents become correctable tool errors with no network call. Exact approved integer
payloads, digest rejection and repeated-approval idempotency remain correct.

| Fresh verification | Result |
| --- | --- |
| Full local backend suite, one process | 1,017 passed, exit 0; JUnit retained |
| Original follow-up boundary probe, copied to a new directory | exit 0 |
| Original follow-up deployed probe, copied likewise | exit 0 |
| Deployed scenario campaign | 20/20 passed, no source changes |
| New resource-composition probe | six controls pass; six defect expectations fail, exit 1 |
| Hosted CI on `28c9f0b` | all five jobs passed |
| `git diff --check` | clean |

[Hosted run 37540530328](https://github.com/SoumithReddy6/Enterprise-No-Code-Agentic-AI-Workflow-Platform/actions/runs/37540530328)
passed backend, native-macos, frontend, browser-layout and postgres-conformance.
The local frontend suite, five local hash seeds, reported eight mutants and full
live-model campaign were not repeated. Frontend checks above come from the verified
hosted run. Model responses in the probes are deterministic fixtures, not claims
about live-model quality.

The documented float precision limit on schema operands at API ingestion is
acknowledged; it was not treated as a new finding in this review.

Application code and dependencies were unchanged. All probe servers were stopped;
temporary databases and synthetic accounts were isolated from the real workspace.
The previous evidence directories were not modified. Fresh results and provenance
are in `evals/results/a51-exact-validation-2026-10-06/`.

Reproduce the new finding:

```bash
.venv/bin/python evals/results/a51-exact-validation-2026-10-06/probe_schema_resources.py
```

It currently exits 1. Keep the original two follow-up findings closed, and resolve
R01 before claiming exact numeric validation composes across supported schemas.
