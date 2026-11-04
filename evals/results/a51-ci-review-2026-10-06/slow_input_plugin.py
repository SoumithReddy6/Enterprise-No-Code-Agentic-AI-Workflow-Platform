"""Reproduce the CI timing assumption without editing production code or tests."""
import time
import pytest

@pytest.fixture(autouse=True)
def delayed_input_event(monkeypatch):
    from backend.app.storage import Store
    original=Store.worker_event
    def delayed(self,run_id,owner,event):
        if event.get("node_id")=="input" and event.get("status")=="running":
            time.sleep(.2)
        return original(self,run_id,owner,event)
    monkeypatch.setattr(Store,"worker_event",delayed)
