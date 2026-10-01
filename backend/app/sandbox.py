"""Whether the Docker sandbox that runs Python tools can actually run them.

Python tools execute in a python:3.12-alpine container started with --pull=never, so they
need the Docker CLI, a responding daemon and that image already present locally. When any
of these is missing, a Python tool fails at run time; a production campaign lost a
workflow that way while every other check was green.

The probe answers with a safe reason code, never command output. It is bounded by a
timeout that also stops the docker process, coalesced so concurrent callers share one
probe, and cached briefly so readiness polling does not launch Docker commands on every
request. Readiness reports it and treats it as required only when PYTHON_SANDBOX_REQUIRED
is true; submission and execution refuse a workflow with a Python tool whenever the
sandbox is unavailable, before a job is created or a node runs.
"""
import asyncio
import os
import shutil
import time

IMAGE='python:3.12-alpine'
PROBE_SECONDS=3.0
CACHE_SECONDS=5.0

MESSAGES={
    'cli_missing':'Docker is not installed or not on PATH. Install Docker to run Python tools.',
    'daemon_unavailable':'Docker is not running. Start Docker, then run the workflow again.',
    'image_missing':f'The {IMAGE} image is not available locally. Run: docker pull {IMAGE}',
    'probe_timeout':'Docker did not respond in time. Check that Docker is healthy, then try again.',
}


def required():
    """PYTHON_SANDBOX_REQUIRED: true makes an unavailable sandbox fail readiness."""
    raw=os.environ.get('PYTHON_SANDBOX_REQUIRED','false').strip().lower()
    if raw in ('true','1','yes'):return True
    if raw in ('false','0','no',''):return False
    raise ValueError('PYTHON_SANDBOX_REQUIRED must be true or false.')


async def _succeeds(*command):
    process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.DEVNULL,
                                                 stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
    try:
        return await process.wait()==0
    except asyncio.CancelledError:
        # A timed-out probe must not leave a docker process behind.
        process.kill();await process.wait()
        raise


async def probe():
    """None if the sandbox can run Python tools, otherwise a reason code."""
    docker=shutil.which('docker')
    if docker is None:return 'cli_missing'
    if not await _succeeds(docker,'version','--format','{{.Server.Version}}'):return 'daemon_unavailable'
    if not await _succeeds(docker,'image','inspect','--format','{{.Id}}',IMAGE):return 'image_missing'
    return None


class SandboxProbe:
    def __init__(self):
        self.result=None;self.checked_at=0.0;self.task=None;self.loop=None

    async def status(self):
        if self.result is not None and time.monotonic()-self.checked_at<CACHE_SECONDS:return self.result
        loop=asyncio.get_running_loop()
        # One probe in flight per event loop; the API, the worker and tests each run their own.
        if self.task is None or self.task.done() or self.loop is not loop:
            self.task=loop.create_task(self._probe());self.loop=loop
        return await asyncio.shield(self.task)

    async def _probe(self):
        try:reason=await asyncio.wait_for(probe(),PROBE_SECONDS)
        except TimeoutError:reason='probe_timeout'
        except OSError:reason='cli_missing'
        self.result={'status':'unavailable','reason':reason} if reason else {'status':'ok'}
        self.checked_at=time.monotonic()
        return self.result


PROBE=SandboxProbe()


async def status():
    """The sandbox's current state: {'status': 'ok'} or {'status': 'unavailable', 'reason': ...}."""
    return await PROBE.status()


def uses_python(workflow):
    return any(node.type=='tool_python' for node in workflow.nodes)


async def preflight_errors(workflow):
    """Refuse a workflow with a Python tool while the sandbox cannot run it."""
    if not uses_python(workflow):return []
    state=await status()
    if state['status']=='ok':return []
    return [f"This workflow uses a Python tool, but the Python sandbox is unavailable ({state['reason']}). {MESSAGES[state['reason']]}"]
