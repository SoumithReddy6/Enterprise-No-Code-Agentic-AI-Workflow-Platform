# R01 implementation review

Reviewed commit: `06b106215de79300b2568231e73f76c7cc80ebb7`.
Report: `docs/reviews/r01-fix-review-2026-10-31.md`.

`probe_repeated_resume.py` is a regression with a security assertion, not an assertion that
the bug exists. It fails on the reviewed commit because a cached Response publishes removed
document content without a fresh dependency check. It uses real temporary KB ingestion,
removal, indexes, Worker and SQLite; model and Jira responses are substituted.

Three attempts are required: loop checkpoint; resume and save a source-empty Response;
resume again and remove the document after the cached Prompt but before cached Response.
Only one model call occurs across all attempts.

Run from the repository root; output is printed and old evidence is untouched:

```sh
review_dir="$PWD/evals/results/r01-fix-review-06b1062"
.venv/bin/python "$review_dir/probe_repeated_resume.py" "$PWD"
```

Expected on `06b1062`: exit 1 at the removed-source publication assertion.
`repeated-resume.json` records success, empty displayed sources, zero checks after removal
and cached Response republication. `repeated-resume-control.json` records refusal after
adding the planned `loop_progress` argument only in a disposable export.

Reproduce the diagnostic correction without changing the application's working tree:

```sh
control_dir=$(mktemp -d /private/tmp/relay-r01-implementation-review-XXXXXXXX)
git archive 06b1062 | tar -x -C "$control_dir"
patch -d "$control_dir" -p1 < "$review_dir/validate-cached-progress.patch"
.venv/bin/python "$review_dir/probe_repeated_resume.py" "$control_dir"
```

Expected: exit 0, named `each:0` refusal, one check after removal, no output and no cached
Response success, still one model call. The normal implementation was not changed.

`revision3-probes.json` contains a fresh run of the prior review's 13 passing cases against
the actual implementation. 106 focused tests pass both on that tree and on the diagnostic
tree. `checks.json` records the commands/results; `hosted-ci.json` records the CI snapshot.
Logs stay local. `preserved-inputs.json` records before/after hashes of the original inputs.
