# Jira structured output — Phase 2

Jira nodes now declare `text: string` and `items: array<object>`. The existing `text` output is the same response string, byte for byte. Agent tool observations still use that original text. No other node is relabelled, no automatic input conversion is introduced, and no loop is added.

| Operation | `items` |
| --- | --- |
| `search` | Issues from the returned page |
| `get_issue` | One issue |
| `create_issue`, `comment` | Empty array |

Each item has exactly six fields: `key`, `summary`, `status`, `assignee`, `updated`, and `url`. Status and assignee contain their display names; missing or invalid optional values are `null`. URLs are built from the configured Jira endpoint and escaped issue key. Raw Jira fields are never copied into items. Invalid entries are skipped with a diagnostic; invalid JSON or a missing issues array yields an empty array and diagnostic.

Items stop at 100. The UTF-8 size of unchanged text plus serialized nonempty items is limited to 64,000 bytes; entries are omitted whole when the remaining budget is insufficient. At the legacy text limit an empty array adds only its two framing bytes. Fixed event metadata is outside this content budget. The existing transport response limit remains unchanged. This can produce fewer than 100 items, including zero, for large responses. No additional pages are fetched, preserving existing requests and text behavior. Provider pagination indicators also mark the collection incomplete.

Successful node events expose `items_truncated: boolean` and `items_warnings: string[]`. These are diagnostics, not extra bindable ports. Execution logs display them alongside the outputs. Original events remain available after resume; cached output events retain the checkpointed array. The UI discovers the `items` output from the node catalog automatically. Binding it to a string input produces an amber advisory warning but does not block validation, save, or execution. Individual string-only consumers may still fail on arrays; Phase 2 does not introduce a conversion layer. The prompt node's existing formatting can render a list, which is used in the nonblocking integration test.

The regression suite verifies normalization, malformed responses, limits, pagination, legacy text, unchanged model observations, validation/save/run, and recovery after a downstream failure. Recovery reads the Jira checkpoint from durable storage, passes an actual list downstream, and does not call Jira again. Jira network responses are mocked; no credentials or live writes are needed for these tests.

Verified on 2026-09-23: 506 backend tests (including 21 Jira-specific cases), 26 frontend tests, frontend typecheck, lint, and production build passed. Existing example and scenario-campaign definitions remain warning-free. Connection update/deletion during delivery uses the original request endpoint for item URLs. No live Jira service was used.
