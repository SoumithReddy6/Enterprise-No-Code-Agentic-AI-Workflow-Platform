"""Start and stop the real API and worker processes for one cell, through scripts.scenarios.lab."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
MARKER = 'SCENARIO_LAB_ONLY'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def check_isolated(directory, database_url):
    """Refuse anything that could be the user's data (spec section 4)."""
    if not (Path(directory) / MARKER).is_file():
        raise RuntimeError('The data directory has no isolation marker; refusing to start.')
    url = make_url(database_url)
    if url.get_backend_name() == 'postgresql' and not (url.database or '').startswith('relay_load_'):
        raise RuntimeError('A PostgreSQL load database name must start with relay_load_.')
    if url.get_backend_name() == 'sqlite' and Path(url.database).resolve().parent != Path(directory).resolve():
        raise RuntimeError('The SQLite load database must be inside the isolated data directory.')


class Stack:
    def __init__(self, directory, database_url, model_url, logs):
        self.directory = Path(directory)
        self.logs = Path(logs)
        self.logs.mkdir(parents=True, exist_ok=True)
        check_isolated(self.directory, database_url)
        self.port = free_port()
        self.url = f'http://127.0.0.1:{self.port}'
        self.env = {**os.environ, 'DATA_DIR': str(self.directory), 'DATABASE_URL': database_url,
                    'CREDENTIAL_ENCRYPTION_KEY': Fernet.generate_key().decode(), 'RELAY_EMBEDDED_WORKER': 'false',
                    'AUTH_REGISTRATION_MODE': 'closed', 'SCENARIO_FIXTURE_PORT': '1', 'OLLAMA_BASE_URL': model_url,
                    'PYTHONPATH': str(ROOT)}
        self.processes = []
        self.handles = []
        self.client = httpx.Client(base_url=self.url, timeout=30, trust_env=False)

    def _start(self, role):
        handle = open(self.logs / f'{role}-{len(self.processes)}.log', 'w')
        self.handles.append(handle)
        process = subprocess.Popen([sys.executable, '-m', 'scripts.scenarios.lab', role, '--port', str(self.port)],
                                   cwd=ROOT, env=self.env, stdout=handle, stderr=subprocess.STDOUT)
        self.processes.append(process)
        return process

    def start_api(self, model):
        self._start('api')
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                if self.client.get('/api/health').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError('The API did not start; read its log.')
        response = self.client.post('/api/auth/register', json={'email': 'load@example.test', 'password': 'Load-password-2026!'})
        assert response.status_code == 201, response.text
        response = self.client.post('/api/models', json={'provider': 'ollama', 'model': model})
        assert response.status_code == 201, response.text

    def submit(self, workflow, count):
        ids = []
        for _ in range(count):
            response = self.client.post('/api/runs', json={'workflow': workflow, 'message': 'load'})
            assert response.status_code == 201, response.text
            ids.append(response.json()['id'])
        return ids

    def start_workers(self, count):
        return [self._start('worker') for _ in range(count)]

    def close(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.client.close()
        for handle in self.handles:
            handle.close()
