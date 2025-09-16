import importlib.util
import pathlib
import sys
import threading
import time
import types

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
    def press(self, key, delay=0):
        self.calls.append(("press", key))

def _make_worker(kb):
    worker = object.__new__(SchedulerWorker)
    worker.kb = kb
    worker._push_to_clipboard = lambda text: None
    worker.stop_event = threading.Event()
    worker.pause_event = threading.Event()
    worker._query_navigation_enabled = True
    return worker


def test_send_reply_linux(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)
    monkeypatch.setattr(xsys, "platform", "linux")
    monkeypatch.setattr(xtime, "sleep", lambda s: None)
    SchedulerWorker._send_reply(worker, "hi")
    assert dummy.calls == [
        ("press", "h"),
        ("press", "i"),
        ("hotkey", ("ctrl", "enter")),
    ]

def test_send_reply_macos(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)
    monkeypatch.setattr(xsys, "platform", "darwin")
    monkeypatch.setattr(xtime, "sleep", lambda s: None)
    SchedulerWorker._send_reply(worker, "hi")
    assert dummy.calls == [
        ("press", "h"),
        ("press", "i"),
        ("hotkey", ("cmd", "enter")),
    ]


def test_interact_and_reply(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)
    monkeypatch.setattr(xsys, "platform", "linux")
    monkeypatch.setattr(xtime, "sleep", lambda s: None)
    SchedulerWorker._interact_and_reply(worker, "hi")
    assert dummy.calls == [
        ("press", "l"),
        ("press", "n"),
        ("press", "h"),
        ("press", "i"),
        ("hotkey", ("ctrl", "enter")),
    ]


def test_press_j_batch(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)
    monkeypatch.setattr(x.random, "randint", lambda a, b: 3)

    delays = iter([0.2, 0.21, 0.22])

    def fake_uniform(a, b):
        return next(delays)

    monkeypatch.setattr(x.random, "uniform", fake_uniform)
    monkeypatch.setattr(xtime, "sleep", lambda s: None)

    assert worker._press_j_batch() is True
    assert dummy.calls == [
        ("press", "j"),
        ("press", "j"),
        ("press", "j"),
    ]


def test_query_navigation_context(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)

    batches = []

    def fake_press(self, stop_event=None):
        batches.append("batch")
        stop_event.wait(0.01)
        return True

    def fake_wait(self, stop_event, duration):
        stop_event.wait(0.01)

    worker._press_j_batch = types.MethodType(fake_press, worker)
    worker._pause_aware_wait = types.MethodType(fake_wait, worker)

    with worker._query_navigation():
        time.sleep(0.03)

    assert batches


def test_query_navigation_stop_callable(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)

    events = []

    def fake_loop(self, stop_event):
        events.append("start")
        stop_event.wait()
        events.append("stopped")

    monkeypatch.setattr(SchedulerWorker, "_query_navigation_loop", fake_loop)

    with worker._query_navigation() as stop:
        assert events == ["start"]
        stop()
        assert events == ["start", "stopped"]

