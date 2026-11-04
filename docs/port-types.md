# Port declarations — Phase 1

This describes the initial rollout. [Phase 2](jira-items.md) adds Jira `items: array<object>` alongside unchanged `text`; all other declarations remain strings. Existing bindings remain silent, while new `items`-to-string bindings warn.

Port types are advisory metadata only. All current registry inputs and outputs remain `string`; values on the wire remain strings. No coercion, parsing, or new rejection is performed at runtime.

The vocabulary is `string`, `number`, `boolean`, `object`, `array`, `array<string>`, and `array<object>`. `valid_type` recognizes exact names. `compatibility` returns:

- `ok` for equal known types and typed-array to untyped-array widening.
- `coerce` for known types to/from string and untyped-array to typed-array narrowing.
- `mismatch` for other pairs, including unknown declarations. Registry audit tests catch unknown names.

`compiler.type_warnings(workflow)` examines declared input bindings independently of `validate_workflow`. Missing nodes/ports and malformed bindings remain the existing validator's responsibility. Advisory text identifies the destination input, source binding, both declared types, and the conversion or structural problem. It explicitly states that Phase 1 performs neither conversion nor rejection.

`POST /api/validate` returns the additive `warnings: string[]` field. `valid` still depends only on `errors`. The editor displays warnings in an amber, dismissible, non-blocking panel separately from red errors. Existing clients can ignore the field. With today's registry all existing workflows return an empty warning list.

Verification covers the full 7-by-7 compatibility matrix, unknown names, every registry port, every `examples/*.json` workflow, every workflow-building expression in the scenario campaign, and an API test with deliberately mismatched metadata that still validates, saves, and completes successfully. The tests do not relabel any production node.

Runtime conversions, real node relabelling, hard type errors, and type-aware editor connections are outside Phase 1.

Verified on 2026-09-22: 485 backend tests passed (including 74 new port-type tests), 26 frontend tests passed, and frontend typecheck, lint, and production build passed. All 14 workflow scenarios passed (15 results because memory has store and recall stages). See [campaign results](../evals/results/port-types-workflows-2026-09-22.json). Existing examples and campaign workflow definitions produced zero type warnings.
