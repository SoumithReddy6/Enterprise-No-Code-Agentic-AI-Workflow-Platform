"""Relay application package.

onnxruntime ships an embedded telemetry client (Microsoft 1DS) that starts two native
threads at import and uploads to mobile.events.data.microsoft.com. It is disabled here,
before any submodule can import onnxruntime, for two reasons:

* Relay is local-first. Nothing about a workflow, document or model call is meant to
  leave the machine, and a dependency's telemetry is no exception.
* The uploader thread races interpreter teardown. When an HTTP response arrives during
  shutdown it locks a mutex that static destruction has already freed, and the process
  aborts with SIGABRT (exit 134) after all work has finished. Five macOS crash reports
  from the test suite share exactly that stack.

The switch is read at import, so it must be set before the first `import onnxruntime`.
It is forced rather than defaulted: there is no configuration of Relay in which sending
runtime telemetry is intended. Set before import, onnxruntime starts one native thread
instead of three.
"""
import os

os.environ['ORT_DISABLE_TELEMETRY'] = '1'
