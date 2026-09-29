# Telemetry shutdown-fix audit

Audited commit: `dc41a3a6f088b848886857bc61138fa1e8cee6cf`, parent `b2a7101`. Scope: import ordering, environment enforcement, native-thread regression tests, crash-report evidence, CI coverage and committed completeness. No application code changed.

## Findings

### T01 — P2: the import-order regression test accepts the unsafe order

`backend/tests/test_onnxruntime_telemetry.py:54–62` searches top-level scripts for strings. It does not check execution order. In an isolated checkout, adding a script containing `import onnxruntime` followed by `import backend.app` still produced **1 passed** for this test. The temporary script was then removed. Evidence: `evals/results/telemetry-audit-2026-09-28/wrong-order-probe.txt`.

It also misses nested scripts and `from onnxruntime import ...`, and comments can satisfy the string check. This is a regression-coverage defect, not a demonstrated unsafe importer in the current application. Prefer fresh-process tests of actual supported entry points, with an import hook asserting the environment at the first ONNX Runtime import; use a recursive AST audit as additional coverage.

### T02 — P2: CI does not exercise the native mechanism

The behavioral test is skipped outside Darwin, while `.github/workflows/ci.yml` runs the backend only on Ubuntu. CI checks that an environment variable equals `1`, but does not prove that the pinned native runtime honors it. The Mac test only checks `guarded < bare`; fewer total threads do not independently identify which thread disappeared.

Add a focused macOS job for this dependency-specific regression. Retain diagnostic evidence identifying the telemetry worker in the control and its absence in the guarded process, or validate an equivalent native telemetry-initialization signal. Avoid treating a relative thread count as proof of universal network silence.

### T03 — P3: the privacy comment overstates the platform's guarantee

`backend/app/__init__.py:7–8` says nothing about a workflow, document or model call is meant to leave the machine. Relay deliberately supports configured cloud models and external tools. Narrow the comment to unwanted dependency telemetry, distinct from user-configured external calls. The crash stacks establish telemetry response-handler activity; they do not reveal transmitted payload contents.

## Verified positives

- All three intended files are in the commit: package initialization, NLI script import ordering and telemetry tests.
- Current direct production importers are `kb/answerability.py`, `kb/reranking.py`, and `scripts/eval_nli.py`. The app package initializes first for the package importers; the NLI script explicitly imports it before its lazy ONNX import. No current unsafe direct production importer was found.
- Starting a fresh process with `ORT_DISABLE_TELEMETRY=0`, then importing the app, yields `1`; the installed ONNX Runtime reports `1.30.0`, matching `backend/requirements.txt`.
- The installed native binary contains both the switch and collector-host strings. This supports the version-specific mechanism, not a guarantee for every future wheel or platform.
- The three telemetry tests pass with native process inspection permitted, exit 0. JUnit retained.
- Independently inspected all five September 27 crash reports: their triggered thread includes `recursive_mutex::lock`, Microsoft event dispatch and HTTP response handling. The stacks are consistent with the reported shutdown bug. They do not by themselves prove the exact mutex lifetime or reproduce the crash on demand.
- The full committed suite was tested from `git archive HEAD` with no working-tree overlays, using the existing dependency environment. Source isolation is verified; this is not a fresh dependency installation.
- Permitted full-suite rerun in that exact committed checkout: **594 passed, exit 0**, four deprecation warnings, 28.89 seconds. `clean-suite-unrestricted.xml` retains the results. This is one independently observed full run, not a replication of the previously reported ten runs.

## Evidence and limitations

Artifacts are under `evals/results/telemetry-audit-2026-09-28/`. `checkout.json` identifies the exported commit and directory. The initial full-suite sandbox run passed 593 tests and failed only because `ps` was denied by the sandbox; that is an environment restriction, not a product regression. The permitted native test rerun passed all three.

An initial launcher accidentally resolved the virtualenv interpreter symlink to the system interpreter, which lacked pytest; it was corrected before the recorded suite execution. No crash-free run count here establishes that an intermittent native abort is impossible. No packet capture or telemetry-content inspection was performed.

## Recommendation

No new runtime blocker was found in the current supported import paths. The committed fix is coherent and the native mechanism test passes locally. Track T01 and T02 as regression-coverage work and correct T03's wording. These findings do not require reopening the S07 implementation; S08 can proceed with these limitations recorded.
