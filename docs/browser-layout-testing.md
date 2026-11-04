# Browser layout regression gate

The test renders Relay's real editor in Chromium against deterministic API fixtures.
It checks four causes, long reasons and legacy uncertainty at 1280 × 720 and 768 × 720.
Every cause must be reachable, the response area must have positive height, the final
answer line must be visible after scrolling, and neither the page nor the response may
overflow sideways. It fails against the original zero-height layout from `bc78cb3`.

```bash
cd frontend
npm ci
npx playwright install chromium
npm run test:browser
```

CI installs Chromium and its Linux system dependencies with
`npx playwright install --with-deps chromium`. Failures retain a trace and screenshot;
the CI job uploads both the HTML report and test artifacts. Tests have no retry waiver.

The test server uses port 3317 and refuses to reuse an existing server. All API requests
are intercepted; unrecognised routes and writes fail the test. The test-only Vite config
has no proxy to port 8000. No backend, model, credential or workspace database is used.
This is a layout regression check, not backend integration or cross-browser certification.

The PostgreSQL CI job separately checks migration shape, four competing worker processes,
lease fencing after SIGSTOP/SIGCONT, and loop recovery after SIGKILL. It uses disposable
schemas and a local HTTP receiver, never external delivery accounts. For the complete
local milestone probe (including storage measurements and a read-only workspace copy):

```bash
docker compose up -d postgres
.venv/bin/python -u -m scripts.check_milestone_storage
```

The full diagnostic also probes a **lossless populated downgrade**, a capability Relay
does not promise. That probe currently returns failure: revision 0003 drops item rows
and accounting on downgrade. It is retained as an explicit diagnostic, not a required
CI check or a reason to reinterpret an empty-schema migration pass as data preservation.
Production rollback must restore a consistent database/key backup. To run only the
supported conformance checks locally:

```bash
.venv/bin/python -u -m scripts.check_milestone_storage \
  --checks postgres-migrations postgres-four-workers postgres-stale-lease \
  postgres-sigkill-loop storage-cost copied-workspace
```

The storage probe reports SQL parameter bytes, not physical disk/WAL consumption. A
committed loop item must never be repeated; an in-flight read can be repeated after a
crash. An unresolved external write is covered by the production campaign and must
require reconciliation instead of automatic replay.
