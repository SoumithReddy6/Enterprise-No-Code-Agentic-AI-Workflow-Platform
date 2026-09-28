"""onnxruntime's telemetry client must never start in a Relay process.

It uploads to Microsoft's collector, which a local-first product must not do, and its
uploader thread races interpreter teardown: five macOS crash reports show it locking a
destroyed mutex at exit and aborting the process with exit 134 after every test passed.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

COUNT_THREADS = '''
import os, sys, subprocess
def native_threads():
    if sys.platform == "darwin":
        out = subprocess.run(["ps", "-M", "-p", str(os.getpid())], capture_output=True, text=True).stdout
        return len(out.strip().splitlines()) - 1
    return len(os.listdir("/proc/self/task"))
'''


def run(code):
    # Strip the switch from the inherited environment. Once any earlier test has imported
    # backend.app, this process carries ORT_DISABLE_TELEMETRY=1, and a child would inherit
    # it - making the "without the switch" measurement silently have the switch too.
    env = {key: value for key, value in os.environ.items() if key != 'ORT_DISABLE_TELEMETRY'}
    result = subprocess.run([sys.executable, '-c', COUNT_THREADS + code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout.strip()


def test_importing_the_app_disables_onnxruntime_telemetry_before_it_loads():
    out = run('import backend.app, onnxruntime, os\nprint(os.environ.get("ORT_DISABLE_TELEMETRY"))')
    assert out == '1'


@pytest.mark.skipif(sys.platform != 'darwin', reason='the telemetry threads are observed on macOS builds')
def test_the_telemetry_threads_never_start():
    """Measured, not assumed: with the switch, onnxruntime starts fewer native threads.

    Without backend.app, a bare import starts the telemetry client; after backend.app, it
    does not. If the switch is removed or set after the first import, these become equal.
    """
    guarded = int(run('import backend.app, onnxruntime, time\ntime.sleep(1)\nprint(native_threads())'))
    bare = int(run('import onnxruntime, time\ntime.sleep(1)\nprint(native_threads())'))
    assert guarded < bare, f'telemetry threads still started: {guarded} with the switch, {bare} without'


def test_every_onnxruntime_importer_loads_the_app_package_first():
    """A script that imports onnxruntime without importing backend.app would start the
    telemetry client, because the switch lives in the package's __init__."""
    offenders = []
    for path in [*ROOT.joinpath('scripts').glob('*.py')]:
        text = path.read_text()
        if 'import onnxruntime' in text and 'import backend.app' not in text and 'from backend.app' not in text:
            offenders.append(path.name)
    assert not offenders, offenders
