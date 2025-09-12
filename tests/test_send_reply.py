import importlib.util
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("x", root / "x.py")
x = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = x
spec.loader.exec_module(x)

SchedulerWorker = x.SchedulerWorker
xsys = x.sys
xtime = x.time

class DummyKB:
    def __init__(self):
        self.calls = []
    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))
    def press(self, key):
        self.calls.append(("press", key))

def _make_worker(kb):
    worker = object.__new__(SchedulerWorker)
    worker.kb = kb
    worker._push_to_clipboard = lambda text: None
    return worker


def test_send_reply_linux(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)
    monkeypatch.setattr(xsys, "platform", "linux")
    monkeypatch.setattr(xtime, "sleep", lambda s: None)
    SchedulerWorker._send_reply(worker, "hi")
    assert dummy.calls == [
        ("hotkey", ("ctrl", "v")),
        ("hotkey", ("ctrl", "enter")),
    ]

def test_send_reply_macos(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)
    monkeypatch.setattr(xsys, "platform", "darwin")
    monkeypatch.setattr(xtime, "sleep", lambda s: None)
    SchedulerWorker._send_reply(worker, "hi")
    assert dummy.calls == [
        ("hotkey", ("cmd", "v")),
        ("hotkey", ("cmd", "enter")),
    ]
