# Relay milestone audit — October 2026

Date: **2026-10-04** (America/New_York). Audited revision: **`de3296368cc88e4efcbded03bdf6c0e05c781a31`**.
Product runtime remains the version from `448c91c`; the audit commit adds tests, scripts,
CI configuration and Playwright, with no backend or editor runtime changes.

## Closure — 2026-10-06

**The dependency blockers below are resolved.** The recommendation and findings that follow
are the dated snapshot from `de32963` and are kept unchanged.

| Blocker | Resolution | Commit |
| --- | --- | --- |
| Python: `pypdf`, `urllib3` | Pinned `pypdf` 6.19.0 and `urllib3` 2.8.0. A fresh environment built from `backend/requirements.txt` audits clean | `6141895` |
| Python: `oauthlib` | Not a declared requirement: it came from an unrelated `kubernetes` install in the local virtualenv. CI audits a fresh environment | — |
| npm: Cloudflare `undici` chain, `shadcn` brace/glob chains, `tinypool`, `source-map-js` | Updated `@cloudflare/vite-plugin`, `wrangler` and its peer types; removed the unused `shadcn` CLI; updated `oxfmt` and `source-map-js`. No `--force` or `--legacy-peer-deps` | `dded39d`, `8797054` |
| npm: `braces` through `vinext` | No patched release exists. **Accepted residual vulnerability**, gated by a reviewed exception that **expires on 2026-12-04** with no automatic extension | `dded39d` |
| npm: `sharp` through `miniflare` (new on 2026-10-06, [GHSA-wq5f-xc86-pv6w](https://github.com/advisories/GHSA-wq5f-xc86-pv6w)) | The gate failed closed on it. `miniflare` pins the vulnerable `sharp` exactly, so no upstream release fixes it. The unused `@cloudflare/vite-plugin` and `wrangler` that brought it in were removed | this branch, after `64e03f7` |
| Audit gate integrity (R01–R04) | The gate fails closed on malformed reports, failed commands, tree problems, invalid registry data and severities that contradict their `via`. Each fix has tests that fail on the previous code | `8797054`, `64e03f7` |

After the Cloudflare removal, as at `64e03f7`, the live npm gate prints *passed with ACCEPTED RESIDUAL VULNERABILITY - not a
clean audit* (6 high, all on the reviewed `braces` chain). This is not a clean audit. The
independent review closing R01–R04 is in
[dependency-gate-audit-2026-10-05.md](dependency-gate-audit-2026-10-05.md).

The populated-downgrade limitation is unchanged and remains documented, not fixed.

Hosted CI on the final commit is the remaining closure check: the browser-layout and
postgres-conformance jobs have not yet been observed running on GitHub.

## Release recommendation (2026-10-04, at `de32963`)

**Do not release yet.** Functional, layout, process-recovery and PostgreSQL conformance
checks passed. Both dependency audits fail the project's existing security policy:
Python has seven reported advisories across three packages; npm reports eleven high and
five moderate vulnerable dependency entries. These counts are scanner findings, not
claims of eleven independent exploitable runtime vulnerabilities.

Patch or remove the affected dependencies and rerun their audits and the relevant
regressions. Do not use `npm audit fix --force` blindly: npm proposes framework/tooling
downgrades for some chains. Reachability and compatibility need review first.

The populated-downgrade experiment is red too, but it is **a documented capability
limitation, not a newly discovered head-version runtime defect**. Operations already
states that downgrades can discard data. Production rollback requires restoring a
consistent database/encryption-key backup and the compatible application version.

## Verified gates

| Check | Current evidence |
| --- | --- |
| Backend, hash seeds 0–4 | 902 tests passed in each of five independent processes; every exit code 0; JUnit retained |
| Frontend unit tests | 31 passed |
| Frontend typecheck, lint, production build | All exit 0 |
| Chromium layout | Six cases passed: four causes, long reasons, legacy uncertainty at 1280 × 720 and 768 × 720 |
| Layout negative control | Same test against `bc78cb3` fails because the response region has **0 px** height; tested in a throwaway copy |
| Production campaign with live models | **24/24 stages passed**, including all 18 scenarios and 15 live workflow shapes |
| PostgreSQL base conformance | Workflow/run persistence, tenants, encrypted connections, lease takeover and fencing passed |
| PostgreSQL migration shape | 0001 → 0003, downgrade to 0001, upgrade to head: empty schema shape matches models |
| PostgreSQL concurrent workers | Four processes, one execution slot each; 50 runs, 50 unique claims, 100 distinct deliveries, 100 item rows; action counter 3 on each run |
| PostgreSQL stale worker | SIGSTOP past a real one-second probe lease, replacement worker completes, SIGCONT: five stale operations refused; final run unchanged |
| PostgreSQL hard kill in loop | SIGKILL mid-read, real default 30-second lease expiry; committed items not repeated; checkpoints/output match uninterrupted execution |
| Knowledge services on PostgreSQL | Separate management/search schemas: ingestion, rebuild, archive cleanup, tenant isolation, deletion and source revocation passed |
| Real workspace database copy | SQLite read-only backup tested at head; 27 table row counts preserved; original database never changed |
| Legacy adoption/checkpoint regressions | Included in the full backend suite; the real workspace copy is current, not falsely labelled pre-Alembic |
| Dependency audits | **Failed**, details below |

The in-flight read in the SIGKILL case was retried once, as required to recover an
unknown read outcome. The extra action remained charged (five actions instead of four).
This is not a promise that every attempted call runs exactly once. The campaign's S15
and S16 separately verify durable completed writes and refusal to replay uncertain writes.
Approvals in loops, uncertain writes under `continue`/`route`, and budget policies are
covered by the current campaign and backend suite, not merely historical reports.

## Storage measurements

Each item returns 60,000 ASCII bytes. These measurements count serialized string/JSON
parameters submitted to SQL, including psycopg's JSON wrappers. They **exclude physical
WAL, index, page and filesystem overhead**. The tool return is scripted to isolate
storage cost; Worker, Store, checkpoint hydration and both databases are real.

| Database | Items | Final run JSON bytes | Item-row bytes | Event payload bytes | Total submitted write bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| sqlite | 25 | 8,382 | 961,697 | 111,396 | 1,128,925 |
| sqlite | 50 | 13,611 | 963,772 | 188,875 | 1,257,362 |
| sqlite | 100 | 24,065 | 967,922 | 343,832 | 1,547,982 |
| postgres | 25 | 8,382 | 961,697 | 111,397 | 1,128,926 |
| postgres | 50 | 13,611 | 963,772 | 188,876 | 1,257,363 |
| postgres | 100 | 24,065 | 967,922 | 343,835 | 1,547,985 |

At 100 items, saved item results remain below 1.1 MB and run JSON is about 24 KB.
Doubling 25 → 50 → 100 items increases submitted write bytes by roughly 1.11× then
1.23× on both databases, rather than the earlier quadratic growth. Value dropping is
reported as incomplete work; bounding storage does not claim that all values survived.

## Dependency findings

### Python — release blocker

`pip-audit` exits 1. These are installed packages from the audited environment:

| Package | Installed | Advisory IDs | Scanner-listed fixed versions |
| --- | --- | --- | --- |
| oauthlib | 3.3.1 | PYSEC-2026-4114 | 4.0.0 |
| pypdf | 6.18.1 | PYSEC-2026-4160, PYSEC-2026-4159, PYSEC-2026-4157 | 6.19.0 |
| urllib3 | 2.7.0 | PYSEC-2026-4177, PYSEC-2026-4176, PYSEC-2026-4175 | 2.8.0 |

The PDF parser is part of the document ingestion path. This audit did not attempt to
exploit these advisories or establish reachability for every transitive package.

### Frontend — release blocker

`npm audit` exits 1: **11 high**, **5 moderate entries.
High findings include the brace/glob chains through `shadcn` and `vinext`, and `undici`
through the Cloudflare tooling. Some fixes are available without breaking version changes;
others have no compatible scanner-listed resolution. Playwright itself has no reported
entry in this audit. The install exposed existing dependency debt; it did not justify
forcing incompatible framework downgrades.

### Populated downgrade — documented limitation

The extra lossless-round-trip probe returns 1. Downgrading through 0003 drops
`run_loop_items` and `runs.accounting`; upgrading cannot recreate the lost data. A run
that formerly had two results then has a compact marker with `unavailable`, and saved
accounting becomes null. The result and its before/after comparison are retained.

This is stricter than Relay's documented downgrade contract. It is deliberately omitted
from the PostgreSQL CI conformance selection. Do not interpret an empty-schema downgrade
pass as proof of data preservation, and do not use schema reversal to roll back production
runs. Restore the stopped-system backup instead. The earlier in-session description of
this as a new rollback defect was corrected after reading the documented policy.

## Delivered test infrastructure and operator corrections

- Pinned `@playwright/test` **1.63.0**, `npm run test:browser`, dedicated frontend port 3317,
  no existing-server reuse, and intercepted API fixtures. No production account/database
  or model is needed for the layout test. Unknown API requests and writes fail it.
- New `browser-layout` CI job installs Chromium, runs the tests with zero retries and
  retains HTML reports, screenshots and failure traces.
- New `postgres-conformance` CI job runs the local-equivalent supported checks against
  PostgreSQL 17. It uses disposable schemas and a local delivery receiver.
- Operations wording was corrected: durable accounting, not approval frames, owns the
  resumed action budget; the current configured ceiling applies on resume.
- The campaign documentation now distinguishes its text/build checks from the separate
  browser layout gate.

The GitHub jobs are **configured and their commands tested locally**. They have not been
pushed or observed running on GitHub. Local validation used macOS/Python 3.14; CI uses
Linux/Python 3.12, with a separate native macOS job.

## Provenance and retained evidence

All standard gates ran on `de32963`. HEAD at completion is identical; tracked
code/config hashes have **no changes during execution**. PostgreSQL probes and the
production runner independently record the same revision and unchanged sources.
Model digests, synthetic corpus SHA256 hashes and PostgreSQL version are retained too.

- [Gate manifest](../../evals/results/milestone-2026-10/gates.json)
- [Environment/model provenance](../../evals/results/milestone-2026-10/environment.json)
- [Production report](../../evals/results/milestone-2026-10/production/report.json)
- [PostgreSQL, faults and storage report](../../evals/results/milestone-2026-10/storage-final/report.json)
- [Populated downgrade comparison](../../evals/results/milestone-2026-10/storage-final/downgrade/comparison.json)
- [Python audit](../../evals/results/milestone-2026-10/pip-audit-final.json)
- [npm audit output](../../evals/results/milestone-2026-10/npm-audit-final.json)
- [Original zero-height screenshot](../../evals/results/milestone-2026-10/browser-original-bug.png)
- [Desktop answer reached](../../evals/results/milestone-2026-10/browser-answer-1280.png)
- [Tablet answer reached](../../evals/results/milestone-2026-10/browser-answer-768.png)
- Full process logs and five JUnit reports remain locally under the same artifact directory.

`gates.json` preserves all raw exits, including the red stronger downgrade experiment;
it has not been edited to turn the audit green. The release recommendation follows the
supported contract and the failing dependency policies, not the aggregate diagnostic flag.
Earlier development probe artifacts are kept separately and are not passing evidence:
the first probe used incorrect fixture assumptions about `action_budget`, typed arrays
and psycopg JSON parameter counting; those were corrected before the recorded gates.

## Limits

This is a milestone regression audit, not a penetration test or proof of universal
exactly-once execution. External SaaS writes use a controlled receiver, not disposable
real Jira/SMTP/GitHub accounts. The browser suite is Chromium-only and tests the
incomplete-run view, not all editor interactions. Large distributed deployments,
per-tenant scheduling fairness and every PostgreSQL load pattern are not certified.
The 53-question generation-quality gate was not rerun here; live workflow success is
not a substitute for semantic/grounding quality evaluation. Dependency telemetry claims
remain limited to the existing pinned-runtime regression coverage.
