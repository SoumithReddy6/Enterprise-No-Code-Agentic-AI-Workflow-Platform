# W03 evidence: truth pass and scope freeze (2026-11-27)

Parent commit: `813b7ec`. Documentation item; the only behavior change is the palette entry of
the request reader (`frontend/lib/workflow.ts`, `paletteCategory('request_reader')` is now `null`).

| File | What it shows |
|---|---|
| `red.txt` | The new tests in a `git archive 813b7ec` copy with only `scripts/check_readme_claims.py` and `backend/tests/test_readme_claims.py` added: 3 failed, 3 passed. Failures: `README.md:13` and `:425` state 2,176 backend tests (collection: 2,204); `README.md:426` states 27 scenarios (gate: 34); the stack row names OpenAI, Claude, Elasticsearch, Pinecone and there is no "run live" row. `test_ci_job_count_matches_the_workflow` passes on the parent: "five GitHub Actions jobs" was already correct |
| `green.txt` | The same tests on the working tree: 6 passed |
| `suite-seeds.txt` | `PYTHONHASHSEED` 0–4, SQLite, no PostgreSQL: each 2,194 passed, 3 skipped (`RELAY_TEST_POSTGRES_URL` not set), 7 xfailed; 128–130 s each |
| `gate-stdout.txt`, `gate-report.json` | `check_production_scenarios --all`: 40 of 40 rows (34 scenarios, M01, backend, frontend-tests, typecheck, lint, build); `source_files_changed: []`; 270.8 s; S16 `writes: 1`, `uncertain_resume_refused: true` |
| `mutants/` | `mutants.py` (scratch copies only; M00 control; guard on "no tests ran"), `run.txt`, `results.json`: 13 of 14 killed. M12 (delete the count in the Verification row) survives and is equivalent: the same count is still stated, and checked, in two other places, so no false claim remains |

Also run, not saved: frontend `npm test` 128 passed, `typecheck`, `lint`, `build` pass;
`npm run test:browser` 68 passed (1.3 min). The browser log shows React Flow "Couldn't create edge"
warnings for request-reader handles; `paletteCategory` is used only for the palette list and its
count (`workflow-editor.tsx:987,1181`), so the change cannot cause them. A parent comparison was run
afterwards (`browser-warnings.txt`): the warnings exist on the parent too (6,081 parent, 6,125
new, 68 passed in both). The request-reader handles (`continue`, `reply`) have the same count;
only the four Switch case handles differ, by 11 each (render count), so the palette change does
not cause the warnings. The PostgreSQL checks were not run: no
write path, storage or approval code changed.

Not verified in this item: GitHub CI on the new commit (network); the live-model campaign; the
grounding gate.
