# Dependency release-gate review — October 5, 2026

**Latest status (`64e03f7`): R01–R04 are closed for the reported defects and follow-ups.** The live dependency gate passes with the documented, expiring braces residual; this is not a clean audit or a new whole-system release certification. Earlier recommendations below are preserved as dated snapshots. Final verification is recorded at the end.

Reviewed commit: `dded39de7da6b475083a4febeac7d147b16502cb`, following Python-pin commit `61418959cd32502e0fdd4e309953fab15abf7df5`.
Live registry checks completed around `2026-10-06 01:46 UTC` (October 5 in the workspace timezone). Application source, dependencies, servers and Git history were not modified by this review.

**Original recommendation (`dded39d`): keep release blocked.** The original Python findings are closed for the declared requirements. The live npm report now contains additional unaccepted advisories, and the new exception gate has three categories of fail-open behavior under incomplete or invalid inputs. These are gate integrity defects; this review does not establish remote exploitability of Relay through the affected development dependencies.

## Fresh verification

- `pip_audit -r backend/requirements.txt`: exit 0, no known vulnerabilities. This audits the declared set; it does not certify unrelated packages in the local virtualenv.
- `npm test`: 43 passed, exit 0, including the 12 gate tests.
- `node frontend/scripts/audit-gate.mjs` run from `frontend`: exit 1, correctly rejecting the additional advisories below.
- Live npm audit: 7 high and 2 critical package findings, 0 moderate. Six high findings represent the accepted braces chain; three additional package findings represent three unaccepted root advisories.
- The saved `dependency-fix-production/report.json` contains 24 passed rows, no source changes, and its recorded source hashes still match the corresponding files. This is saved evidence, not a fresh rerun of the campaign. The campaign does not include a live dependency-audit stage and does not hash the new gate module.
- `git diff --check`: exit 0. The full backend suite, fresh-install checks and browser campaign were not repeated in this review.

## R01 — P1: current npm release gate rejects additional advisories

| Installed package | Path | Advisory | Required patched release |
| --- | --- | --- | --- |
| `source-map-js@1.2.1` | Tailwind/PostCSS | [GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q), high | `1.2.2` |
| `tinypool@2.1.0` | `oxfmt@0.61.0` | [GHSA-5gmw-xhrv-c9v3](https://github.com/advisories/GHSA-5gmw-xhrv-c9v3), critical | `2.1.1` for this advisory |
| `tinypool@2.1.0` | `oxfmt@0.61.0` | [GHSA-85c8-ppgw-ccpr](https://github.com/advisories/GHSA-85c8-ppgw-ccpr), critical | `2.1.2` to close both |

Both `source-map-js@1.2.2` and `tinypool@2.1.2` were confirmed published with `npm view`. `oxfmt` pins `tinypool` exactly to `2.1.0`; updating a transitive lock entry within that declared range is insufficient. npm proposes `oxfmt@0.72.0`, flagged as a semver-major change. Assess and test an explicit parent update or a narrowly scoped override; do not use `--force` or broaden the braces exception.

The tinypool advisories describe prototype-pollution gadgets that require another pollution primitive and applicable worker-option usage. This review did not demonstrate such a primitive in Relay. The release policy nevertheless includes these build/development dependencies, and the gate currently fails as intended.

## R02 — P1: malformed reports can authorize an unaccepted high finding

Location: `frontend/scripts/audit-gate-core.mjs:64–76` and `:100–102`.

Only the existence of objects and an array-shaped `via` is validated. An advisory object without `severity` is silently skipped by `BLOCKING.has(root.severity)`. The fallback excludes entries containing an object, so a high finding with an unaccepted advisory and missing severity passes without an exception. A numeric `via` entry also passes. Arrays in place of the report maps pass. A report with a nonzero high count but an empty findings map passes and prints contradictory output: “no high or critical advisories (reported: 1 high, 1 total).”

All four malformed inputs returned `ok: true` in direct probes. Require valid report maps, valid severities and complete advisory records; validate string references and reconcile reported counts with entries. Missing/unknown data must produce a failure rather than being treated as nonblocking. Validate duplicate advisory records before deduplicating them.

## R03 — P2: command failures and invalid dependency trees can pass

Location: `frontend/scripts/audit-gate.mjs:10–16`; `dependencyPaths` does not inspect tree diagnostics.

`npmJson` checks launch errors and JSON parsing but ignores exit status. Using a temporary fake npm executable to exercise the real CLI, all three inputs below exited the gate with 0:

1. `npm audit` exit 2 with otherwise accepted JSON.
2. `npm ls` exit 1 with the reviewed path and `problems: ['invalid: braces@3.0.3']`.
3. `npm view` exit 1 with an empty JSON array.

Permit only the documented audit vulnerability exit alongside a fully validated report. Require success for dependency-tree and registry commands; reject signals and invalid/missing/extraneous tree diagnostics that invalidate the reviewed installation. Add CLI-level tests rather than relying solely on the pure evaluator tests.

## R04 — P2: absent patch-monitor data is interpreted as no patch

Location: `frontend/scripts/audit-gate-core.mjs:89–96`.

`published.braces = []` and `['not-a-version']` both pass. The code checks only `Array.isArray`, then discards unparseable values, so unusable registry data is equivalent to “no compatible patched release.” Require a nonempty, validated version list containing the installed reviewed version. Use valid semver handling, including legitimate prerelease entries, and reject invalid entries instead of ignoring them.

## Reproduction and evidence

Run `node evals/results/dependency-gate-review-2026-10-05/probe_gate.mjs`. At the reviewed commit it exits 1: the control passes, but all seven adversarial policy checks fail because the evaluator accepts the inputs. `core-probes.json` records the results. `cli-probes.json` records the three actual CLI invocations with mocked npm responses; no real npm executable or installation was modified.

`npm-audit.json` and `pip-audit.json` contain the fresh registry results. `review-inputs.json` pins the reviewed commit and SHA256 of the gate, policy, lockfile and requirements. The acceptance bar remains: known braces exception only; every other blocking advisory and every invalid/unavailable audit input fails.

Fix R02–R04 with regression tests, patch R01's dependencies, then rerun the live gate before revising the release recommendation. Preserve the previous passing campaign as a dated functional snapshot rather than replacing it with a claim of current security acceptance.

## Follow-up — commit 8797054

Independently reviewed `87970549ed00bf0429c538a099bfad8c258e6842`. The live gate now exits 0 with **accepted residual vulnerability, not a clean audit**, reporting 6 high / 6 total on the reviewed braces chain. The installed dependency tree validates with `npm ls --all`, the 57 frontend unit tests pass, and lint and typecheck pass. No backend source changed in this commit; the backend suite and production campaign were not rerun for this focused review.

The original `probe_gate.mjs` fixture was incomplete for the new stricter validator: it omitted zero-valued info/low/moderate counts. Those keys are now supplied. The numeric-via case also now counts its critical finding as critical, so a counts mismatch cannot mask the intended defect. Each negative probe checks its intended failure message. All eight original policy checks now pass, including the positive control. The old `core-probes.json` remains the historical result; `core-probes-8797054.json` contains the corrected rerun.

**R01, R03 and R04 are closed for their reported reproductions. R02 retains one P2 boundary defect.** `reportProblems` accepts a finding with package severity `high` and its sole advisory severity `moderate`, with complete metadata correctly counting one high finding. The root-advisory loop skips it as nonblocking. The final package-level check at `audit-gate-core.mjs:195–198` also skips it because `via` contains an object. The result is exit 0 and the contradictory line:

```text
npm audit gate: passed, no high or critical advisories (reported: 1 high, 1 total)
```

This was reproduced through the real CLI with a temporary fake npm executable: audit exit 1 with one finding, ls/view exit 0 with valid data, complete counts, and no incomplete fields. `severity-cli-8797054.json` records that run. `probe_severity.mjs` also exercises moderate, high and critical variants; the moderate control is correctly allowed, but both blocking variants incorrectly pass. Its exit 1 is an expected failing policy test at this commit, not a frontend/application test failure.

This is an inconsistent-report boundary, not evidence that the current live registry report was bypassed or that Relay is remotely exploitable. Under the documented fail-closed policy, nevertheless, every high/critical package finding must be accounted for by accepted roots and paths or rejected. Do not suppress the package-level safety check merely because a `via` entry is an object. Reject the inconsistent case while continuing to allow a genuinely moderate finding and a legitimate parent whose higher severity comes from a blocking dependency reference.

The empty-PATH CLI-test portability correction is present. Formatter-only churn remains separate. No application or gate source was changed during this follow-up; only the auditor's fixtures and evidence were updated.

## Final verification — commit 64e03f7

Independently reviewed `64e03f7840b12a26593500d2e470a4cf44e8cca0`. The final coverage check now applies to every high/critical finding, including findings containing an advisory object. The new severity bounds reject the inconsistent high/critical variants while retaining a genuine moderate finding and a parent whose applicable severity is below its referenced child. The off-path case carrying the accepted braces advisory is explicitly covered by a passing regression test.

Fresh checks in this review:

| Check | Result |
| --- | --- |
| Frontend unit suite, including actual CLI with fake npm | 61 passed, exit 0 |
| Corrected original policy probes | 8/8, exit 0; intended reasons checked |
| Severity probes | 3/3, exit 0; moderate allowed, high/critical rejected |
| Saved milestone npm report through `reportProblems` | No structural errors |
| Saved independent-review npm report through `reportProblems` | No structural errors |
| Live registry through the full CLI | Exit 0; accepted residual, 6 high / 6 total |
| Typecheck, lint, `git diff --check` | Exit 0 |

**R01–R04 are closed within the scope of this review.** No additional blocker was found in this diff. Keep GHSA-vfj7-8cjw-p6xm visible as an accepted residual vulnerability, expiring December 4, 2026; the six package findings are one reviewed root advisory, not six separately accepted advisories. Closure does not waive that exception's version, path, patch-availability or expiry checks.

The backend suite, fresh npm installation, Playwright, mutation campaign and whole production campaign were not rerun in this final review: the diff changes only the gate, its tests and its documentation, with no dependency changes. Historical functional evidence remains separate. `closure-inputs-64e03f7.json`, `core-probes-64e03f7.json` and `severity-probes-64e03f7.json` record the reviewed commit, source hashes and corrected probe results. No runtime or gate source was edited by the auditor.
