"""Test-wide defaults.

Workflows with a Python tool are refused while the Docker sandbox is unavailable. Tests
that run such workflows with the tool call mocked must not depend on whether this
machine happens to have Docker and the python:3.12-alpine image, so the sandbox reads as
available unless a test marks itself real_sandbox and controls the probe itself.
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line('markers', 'real_sandbox: exercise the real Python sandbox probe instead of the default stub')


@pytest.fixture(autouse=True)
def python_sandbox_available(request, monkeypatch):
    if request.node.get_closest_marker('real_sandbox'):return
    from backend.app import sandbox
    async def available(fresh=False):return {'status': 'ok'}
    monkeypatch.setattr(sandbox, 'status', available)
