"""onnxruntime's embedded telemetry client must never start in a Relay process.

onnxruntime 1.30.0 embeds Microsoft's 1DS telemetry client, which starts a worker thread
at import. That thread races interpreter teardown: five macOS crash reports show it
handling an HTTP response during shutdown and locking a destroyed mutex, aborting the
process with exit 134 after every test had passed. The client is also unwanted
dependency telemetry. onnxruntime reads ORT_DISABLE_TELEMETRY at import, so the switch
must be in the environment at the moment of the *first* onnxruntime import.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(code):
    # Strip the switch from the inherited environment. Once any earlier test has imported
    # backend.app, this process carries ORT_DISABLE_TELEMETRY=1 and a child would inherit
    # it, silently giving the control measurement the switch too.
    env = {key: value for key, value in os.environ.items() if key != 'ORT_DISABLE_TELEMETRY'}
    env['PYTHONPATH'] = str(ROOT)
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout.strip().splitlines()[-1]


# --------------------------------------------------------------------------- entry points

WATCH_FIRST_IMPORT = '''
import os, sys, importlib.abc
from pathlib import Path
seen = {}
class Watch(importlib.abc.MetaPathFinder):
    """Record the environment at the first onnxruntime import, then let it proceed."""
    def find_spec(self, name, path, target=None):
        if name == "onnxruntime" and "env" not in seen:
            seen["env"] = os.environ.get("ORT_DISABLE_TELEMETRY")
        return None
sys.meta_path.insert(0, Watch())
'''

ENTRY_POINTS = {
    'reranker': 'from backend.app.kb import reranking\ncall = lambda: reranking.load(Path("/nonexistent"))',
    'answerability reader': 'from backend.app.kb.answerability import AnswerabilityReader\ncall = lambda: AnswerabilityReader(Path("/nonexistent"))',
    'NLI script': 'import scripts.eval_nli as nli\ncall = lambda: nli.NLIJudge(Path("/nonexistent"))',
}


@pytest.mark.parametrize('name', sorted(ENTRY_POINTS))
def test_the_switch_is_set_when_each_entry_point_first_imports_onnxruntime(name):
    """Drive the real code path and observe the environment at the first import itself.

    Each entry point imports onnxruntime lazily and then fails on the missing model
    directory; the failure is irrelevant, the import is what is observed. The test also
    requires that the import happened, so it cannot pass without exercising anything.
    """
    code = WATCH_FIRST_IMPORT + ENTRY_POINTS[name] + '''
try:
    call()
except Exception:
    pass
print(repr(seen.get("env", "<onnxruntime was never imported>")))
'''
    assert run(code) == "'1'", f'{name}: first onnxruntime import happened without the switch'


# --------------------------------------------------------------------------- import-order audit

def unsafe_importer(source, inside_app_package=False):
    """Why a module could import onnxruntime before the switch is set, or None if it cannot.

    Code inside the backend.app package is safe: importing any of it runs the package's
    __init__ first. Anything else needs a module-level import of backend.app, and if
    onnxruntime is itself imported at module level, backend.app must come first.
    """
    tree = ast.parse(source)

    def is_ort(node):
        if isinstance(node, ast.Import):
            return any(alias.name.split('.')[0] == 'onnxruntime' for alias in node.names)
        return isinstance(node, ast.ImportFrom) and (node.module or '').split('.')[0] == 'onnxruntime'

    def is_app(node):
        if isinstance(node, ast.Import):
            return any(alias.name == 'backend.app' or alias.name.startswith('backend.app.') for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            module = node.module or ''
            return (module == 'backend.app' or module.startswith('backend.app.')
                    or (module == 'backend' and any(alias.name == 'app' for alias in node.names)))
        return False

    ort_anywhere = [node.lineno for node in ast.walk(tree) if is_ort(node)]
    if not ort_anywhere or inside_app_package:
        return None
    app_top = [node.lineno for node in tree.body if is_app(node)]
    ort_top = [node.lineno for node in tree.body if is_ort(node)]
    if not app_top:
        return 'imports onnxruntime without importing backend.app at module level'
    if ort_top and min(ort_top) < min(app_top):
        return f'imports onnxruntime on line {min(ort_top)} before backend.app on line {min(app_top)}'
    return None


def test_no_module_can_import_onnxruntime_before_the_switch():
    offenders = {}
    for path in [*ROOT.joinpath('backend').rglob('*.py'), *ROOT.joinpath('scripts').rglob('*.py')]:
        inside = ROOT.joinpath('backend', 'app') in path.parents
        reason = unsafe_importer(path.read_text(), inside_app_package=inside)
        if reason:offenders[str(path.relative_to(ROOT))] = reason
    assert not offenders, offenders


@pytest.mark.parametrize('source,expected_unsafe', [
    ('import onnxruntime\nimport backend.app\n', True),                     # wrong order
    ('from onnxruntime import InferenceSession\nimport backend.app\n', True),  # from-import, wrong order
    ('import onnxruntime\n', True),                                          # no switch at all
    ('# import backend.app\nimport onnxruntime\n', True),                    # a comment is not an import
    ('import backend.app\nimport onnxruntime\n', False),                     # correct order
    ('import backend.app\ndef f():\n    import onnxruntime\n', False),       # lazy import after package
    ('def f():\n    import onnxruntime\nimport backend.app\n', False),       # lazy: runs after module import
    ('x = "import onnxruntime"\n', False),                                    # a string is not an import
])
def test_the_audit_itself_catches_unsafe_orders(source, expected_unsafe):
    """The previous version passed a script importing onnxruntime before backend.app.
    These cases pin the audit so it cannot regress into a string search again."""
    assert (unsafe_importer(source) is not None) == expected_unsafe


# --------------------------------------------------------------------------- native mechanism

SAMPLE_THREADS = '''
import os, subprocess, time
{prelude}
import onnxruntime
time.sleep(1)
stacks = subprocess.run(["sample", str(os.getpid()), "1", "-mayDie"], capture_output=True, text=True).stdout
print("Microsoft::Applications::Events" in stacks and "WorkerThread" in stacks)
'''


@pytest.mark.skipif(sys.platform != 'darwin', reason='identifies the 1DS worker by native symbol with macOS sample; run by the macOS CI job')
def test_the_telemetry_worker_thread_is_identified_and_absent_when_guarded():
    """Identify the specific thread rather than compare counts.

    The control proves the pinned wheel still ships the 1DS worker, so this fails loudly
    if a future onnxruntime changes the mechanism. The guarded process proves the switch
    stops that thread from starting. Neither is a claim about all network traffic.
    """
    control = run(SAMPLE_THREADS.format(prelude=''))
    guarded = run(SAMPLE_THREADS.format(prelude='import backend.app'))
    assert control == 'True', 'the 1DS telemetry worker is no longer present in the control; re-verify the mechanism for this onnxruntime version'
    assert guarded == 'False', 'the 1DS telemetry worker started despite the switch'


def test_importing_the_app_sets_the_switch_even_over_an_inherited_override():
    code = 'import os\nos.environ["ORT_DISABLE_TELEMETRY"] = "0"\nimport backend.app\nprint(os.environ["ORT_DISABLE_TELEMETRY"])'
    assert run(code) == '1'
