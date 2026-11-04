"""Relay application package.

onnxruntime 1.30.0 embeds Microsoft's 1DS telemetry client, which starts a worker thread
at import and has a collector endpoint (mobile.events.data.microsoft.com) compiled in.
It is disabled here, before any submodule can import onnxruntime, for two reasons:

* It is unwanted dependency telemetry. Relay does make external calls, but only ones a
  user configures: a cloud model provider they select, or a tool connection they set up.
  A runtime library reporting on its own use is not one of those, and it should not
  happen without the operator knowing. The crash stacks below show the client handling
  HTTP responses; they do not reveal what, if anything, was transmitted.
* Its worker thread races interpreter teardown. When a response arrives during shutdown
  it locks a mutex that static destruction has already freed, and the process aborts
  with SIGABRT (exit 134) after all work has finished. Five macOS crash reports from the
  test suite share exactly that stack.

onnxruntime reads ORT_DISABLE_TELEMETRY at import, so the switch must be set before the
first `import onnxruntime`. It is forced rather than defaulted, because no Relay
configuration intends this telemetry. With it set, the 1DS worker thread does not start;
tests identify that thread by native symbol and require it to be absent. This covers the
pinned onnxruntime version, not every future wheel or platform.
"""
import os

os.environ['ORT_DISABLE_TELEMETRY'] = '1'
