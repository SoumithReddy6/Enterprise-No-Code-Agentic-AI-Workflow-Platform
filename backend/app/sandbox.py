"""Whether the Docker sandbox that runs Python tools can actually run them.

Python tools execute in a python:3.12-alpine container started with --pull=never, so they
need the Docker CLI, a responding daemon and that image already present locally. When any
of these is missing, a Python tool fails at run time; a production campaign lost a
workflow that way while every other check was green.

The probe checks the prerequisites, then runs a minimal container built by the same
command builder and security flags as the executor: Docker can answer `version` and
`image inspect` while still refusing to start a container under those restrictions. It
answers with a safe reason code, never command output, and is bounded by a timeout that
stops the docker process and removes a container it may have started.

Readiness and validation share a probe and cache its result briefly, so polling does not
start a container on every request. The worker asks for a fresh probe immediately before
executing, so a cached success can never vouch for a sandbox that has since stopped.
Readiness treats the sandbox as required only when PYTHON_SANDBOX_REQUIRED is true;
submission, resume and execution refuse a workflow with a Python tool whenever it is
unavailable, before a job is created or a node runs.
"""
import asyncio
import os
import shutil
import time
import uuid

IMAGE='python:3.12-alpine'
PROBE_SECONDS=3.0
CACHE_SECONDS=5.0

MESSAGES={
    'cli_missing':'Docker is not installed or not on PATH. Install Docker to run Python tools.',
    'daemon_unavailable':'Docker is not running. Start Docker, then run the workflow again.',
    'image_missing':f'The {IMAGE} image is not available locally. Run: docker pull {IMAGE}',
    'container_failed':f"Docker could not run the {IMAGE} sandbox container with Relay's security settings. Check Docker's logs and resource limits.",
    'probe_timeout':'Docker did not respond in time. Check that Docker is healthy, then try again.',
}


def container_command(name,*program):
    """The docker run command for a sandboxed program. The executor and the probe both use
    it, so the probe proves exactly the configuration Python tools run under."""
    return ['docker','run','--rm','--pull=never','--name',name,'--network=none','--read-only','--cap-drop=ALL',
            '--security-opt=no-new-privileges','--pids-limit=32','--memory=128m','--cpus=0.5','--ulimit','fsize=65536:65536',
            '--user=65534:65534','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','-i',IMAGE,*program]


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


async def _smoke(docker):
    """Run print(1) in a real sandbox container; True only if it printed exactly 1."""
    name='relay-sandbox-probe-'+uuid.uuid4().hex[:12]
    command=container_command(name,'python','-I','-c','print(1)')
    process=await asyncio.create_subprocess_exec(docker,*command[1:],stdin=asyncio.subprocess.DEVNULL,
                                                 stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        out=await process.stdout.read(64);await process.wait()
        return process.returncode==0 and out.strip()==b'1'
    except asyncio.CancelledError:
        process.kill();await process.wait()
        # Killing the CLI does not stop a container it already started.
        cleanup=await asyncio.create_subprocess_exec(docker,'rm','-f',name,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        try:await asyncio.wait_for(cleanup.wait(),5)
        except TimeoutError:
            cleanup.kill();await cleanup.wait()  # Reap it; never leave the cleanup process behind.
        raise


async def probe():
    """None if the sandbox can run Python tools, otherwise a reason code."""
    docker=shutil.which('docker')
    if docker is None:return 'cli_missing'
    if not await _succeeds(docker,'version','--format','{{.Server.Version}}'):return 'daemon_unavailable'
    if not await _succeeds(docker,'image','inspect','--format','{{.Id}}',IMAGE):return 'image_missing'
    if not await _smoke(docker):return 'container_failed'
    return None


class SandboxProbe:
    def __init__(self):
        self.result=None;self.checked_at=0.0;self.task=None;self.loop=None

    async def status(self,fresh=False):
        if fresh:return await self._probe()  # Never a cached or in-flight result.
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


async def status(fresh=False):
    """The sandbox's state: {'status': 'ok'} or {'status': 'unavailable', 'reason': ...}.
    fresh probes now, for the moment before execution; otherwise a recent result may serve."""
    return await PROBE.status(fresh)


def uses_python(workflow):
    return any(node.type=='tool_python' for node in workflow.nodes)


async def preflight_errors(workflow,fresh=False):
    """Refuse a workflow with a Python tool while the sandbox cannot run it."""
    if not uses_python(workflow):return []
    state=await status(fresh=fresh)
    if state['status']=='ok':return []
    return [f"This workflow uses a Python tool, but the Python sandbox is unavailable ({state['reason']}). {MESSAGES[state['reason']]}"]
