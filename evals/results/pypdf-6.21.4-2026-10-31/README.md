# Dependency audit: pypdf 6.21.2 → 6.21.4 (2026-10-31)

## Cause

Hosted CI run `41223931155` on `e50fa65` (a documents-only commit) failed in the backend job
at `python -m pip_audit`. Seven advisories were published for `pypdf 6.21.2`
(`ci-41223931155-before.txt`):

| Fixed in | Advisories |
|---|---|
| 6.21.3 | CVE-2026-119206, CVE-2026-119207, CVE-2026-119208, CVE-2026-119209 |
| 6.21.4 | CVE-2026-119203, CVE-2026-119204, CVE-2026-119205 |

The other four jobs (native-macos, frontend, browser-layout, postgres-conformance) passed.

## Change

`backend/requirements.txt`: `pypdf==6.21.2` → `pypdf==6.21.4`, the lowest release that
fixes all seven. No application code changes. Relay uses pypdf only in
`backend/app/pdf_extract.py` (`PdfReader`).

The [pypdf changelog](https://github.com/py-pdf/pypdf/blob/main/CHANGELOG.md) lists 6.21.3
(2026-10-27) and 6.21.4 (2026-10-28) as security fixes (limits on cross-reference stream
entries, copied page attributes and error messages; EOF and loop detection; the validated
page count after decryption) plus one bug fix (indirect widths in CID font `/W` arrays). It
lists no deprecations or breaking changes.

## Results

| Check | Result |
|---|---|
| `pip-audit` in a fresh environment from `backend/requirements.txt` (`pip-audit.txt`) | No known vulnerabilities found, exit 0 |
| Backend `PYTHONHASHSEED` 0–4 (`seeds.txt`) | 1,898 passed each (unchanged count) |
| Isolation: `git archive e50fa65` + the changed file (`isolation.txt`) | 1,898 passed |
| Deployed scenario gate (`production/report.json`) | 24 of 24 rows passed |

The local fresh environment uses Python 3.14.6; hosted CI uses Python 3.12. The hosted
backend job runs the same audit on 3.12 after push.

The npm side is unchanged: the `braces` exception (build tooling, expires 2026-12-04)
remains an accepted residual vulnerability.

`production/report.json` SHA-256: `86abcc034f7fd69c6cfef0a5a807629741fa51e1d691fcd4bfeb103c144c6a1a`.

Logs (`*.log`), `runs.json`, `provider-requests.json` and `production/switch/` stay local.
