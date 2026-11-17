"""The model stub: an Ollama-compatible /api/chat endpoint with a set latency."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = 'Load stub answer. ' * 12  # About 200 characters of final text.


class ModelStub:
    def __init__(self, latency_ms=0):
        self.latency = latency_ms / 1000
        self.calls = 0
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                with owner.lock:
                    owner.calls += 1
                if owner.latency:
                    time.sleep(owner.latency)
                body = json.dumps({'message': {'role': 'assistant', 'content': ANSWER},
                                   'prompt_eval_count': 100, 'eval_count': 50}).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass  # The caller went away.

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}'

    def reset(self):
        with self.lock:
            self.calls = 0

    def close(self):
        self.server.shutdown()
        self.server.server_close()
