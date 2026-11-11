# React 19.2.9 for GHSA-xqc2-xf4c-x3h3 (2026-11-11)

CI run 43108569244 (`936e6c8`) failed only in the frontend job's npm audit gate:
`Unaccepted critical advisory GHSA-xqc2-xf4c-x3h3 in react-server-dom-webpack (>=19.2.0 <19.2.9)`.
The advisory was published 2026-11-10, after the last green run (`c630aed`, 2026-11-09); the
four other jobs passed. The lowest fixed release is 19.2.9 (published 2026-10-14). Its peer
range `react`/`react-dom` `^19.2.9` moves both to 19.2.9. No `npm audit fix --force`, no
`--legacy-peer-deps`, no exception.

| Check | Result |
|---|---|
| `package.json` | `react`, `react-dom`, `react-server-dom-webpack` 19.2.8 → 19.2.9 (exact pins) |
| `package-lock.json` | only these three versions (with `resolved`/`integrity`) changed |
| `audit-gate.txt` | passed: no high or critical advisories; 4 moderate remain (the accepted residual vulnerability, braces exception expires 2026-12-04) |
| `npm-audit.json` | the full `npm audit --json` after the upgrade |
| Frontend | 118 unit, typecheck, lint, build, 54 Chromium passed |
