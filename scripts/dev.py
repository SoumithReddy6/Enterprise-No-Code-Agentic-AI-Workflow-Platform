#!/usr/bin/env python3
"""Start the authenticated API, durable worker and editor; stop all on Ctrl-C."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

def main():
    python=ROOT/'.venv/bin/python'
    if not python.exists() or not (ROOT/'frontend/node_modules').exists():
        sys.exit('Install dependencies first. See README.md → Setup.')
    for port in (3000,8000,8011,8012):
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try: probe.bind(('127.0.0.1',port))
            except OSError: sys.exit(f'Port {port} is already in use. Stop the existing Relay server before starting another.')
    processes=[]
    service_env={**os.environ,'RELAY_EMBEDDED_WORKER':'false'}
    def stop(signum=None,frame=None):
        for process in processes:
            if process.poll() is None: os.killpg(process.pid,signal.SIGTERM)
        for process in processes:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL)
    signal.signal(signal.SIGINT, lambda *_:sys.exit(0))
    signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
    def service(module,port):
        args=[str(python),'-m','uvicorn',module+':create_app','--factory','--host','127.0.0.1','--port',str(port)]
        if (ROOT/'.env').exists():args+=['--env-file','.env']
        process=subprocess.Popen(args,cwd=ROOT,start_new_session=True,env=service_env);processes.append(process)
        import urllib.request
        for _ in range(150):
            if process.poll() is not None:sys.exit(f'Knowledge service on {port} could not start.')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
                    if response.status==200:return
            except OSError:time.sleep(.1)
        sys.exit(f'Knowledge service on {port} did not become ready.')
    try:
        service('backend.app.kb.management_app',8011)
        service('backend.app.kb.search_app',8012)
        processes.append(subprocess.Popen([str(python),'-m','uvicorn','backend.app.main:create_app','--factory','--host','127.0.0.1','--port','8000','--env-file','.env'] if (ROOT/'.env').exists() else [str(python),'-m','uvicorn','backend.app.main:create_app','--factory','--host','127.0.0.1','--port','8000'],cwd=ROOT,start_new_session=True,env=service_env))
        # Finish API schema migration before starting another database process.
        import urllib.request
        for _ in range(100):
            if processes[0].poll() is not None:sys.exit('The API could not start.')
            try:
                with urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=1) as response:
                    if response.status==200:break
            except OSError:time.sleep(.1)
        else:sys.exit('The API did not become ready.')
        processes.append(subprocess.Popen([str(python),'-m','backend.app.worker'],cwd=ROOT,start_new_session=True,env=service_env))
        processes.append(subprocess.Popen([str(python),'-m','backend.app.kb.ingestion'],cwd=ROOT,start_new_session=True,env=service_env))
        processes.append(subprocess.Popen(['npm','run','dev'],cwd=ROOT/'frontend',start_new_session=True))
        print('\nRelay editor: http://127.0.0.1:3000\nAPI docs: http://127.0.0.1:8000/docs\nPress Ctrl-C to stop Relay and knowledge services.\n',flush=True)
        while all(p.poll() is None for p in processes): time.sleep(.25)
        sys.exit(next((p.returncode for p in processes if p.returncode is not None),1))
    finally: stop()

if __name__=='__main__': main()
