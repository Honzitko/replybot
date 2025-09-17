import importlib.util
import pathlib
import sys
import threading
import time

root = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("x", root / "x.py")
x = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = x
spec.loader.exec_module(x)

SchedulerWorker = x.SchedulerWorker
xsys = x.sys
xtime = x.time
STEP_PAUSE_MIN = x.STEP_PAUSE_MIN
STEP_PAUSE_MAX = x.STEP_PAUSE_MAX
FAST_J_INITIAL_DELAY_RANGE = x.FAST_J_INITIAL_DELAY_RANGE
POPULAR_INITIAL_J_COUNT = x.POPULAR_INITIAL_J_COUNT

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
    worker.search_mode = "popular"
    worker._popular_initial_scroll_pending = False
    return worker


def test_build_search_url_popular():
    url = x.build_search_url("rocket science", "Popular")
    assert url == "https://x.com/search?q=rocket%20science&src=typed_query&f=top"


def test_build_search_url_latest():
    url = x.build_search_url("rocket science", "Latest")
    assert url == "https://x.com/search?q=rocket%20science&src=typed_query&f=live"


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
        ("press", "r"),
        ("press", "h"),
        ("press", "i"),
        ("hotkey", ("ctrl", "enter")),
    ]


def test_press_j_batch(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)
    ranges = []

    def fake_randint(a, b):
        ranges.append((a, b))
        return 3

    delays = iter([0.02, 0.03, 0.9])
    calls = []

    def fake_uniform(a, b):
        calls.append((a, b))
        return next(delays)

    monkeypatch.setattr(x.random, "randint", fake_randint)
    monkeypatch.setattr(x.random, "uniform", fake_uniform)
    monkeypatch.setattr(xtime, "sleep", lambda s: None)

    assert worker._press_j_batch() is True
    assert dummy.calls == [
        ("press", "j"),
        ("press", "j"),
        ("press", "j"),
    ]
    assert ranges == [(2, 5)]
    assert calls == [
        FAST_J_INITIAL_DELAY_RANGE,
        FAST_J_INITIAL_DELAY_RANGE,
        (STEP_PAUSE_MIN, STEP_PAUSE_MAX),
    ]


def test_press_j_batch_popular_initial_scroll(monkeypatch):
    dummy = DummyKB()
    worker = _make_worker(dummy)
    worker._popular_initial_scroll_pending = True

    ranges = []

    def fake_randint(a, b):
        ranges.append((a, b))
        return 3

    uniform_calls = []

    def fake_uniform(a, b):
        uniform_calls.append((a, b))
        if (a, b) == FAST_J_INITIAL_DELAY_RANGE:
            return 0.01
        return 0.8

    monkeypatch.setattr(x.random, "randint", fake_randint)
    monkeypatch.setattr(x.random, "uniform", fake_uniform)
    monkeypatch.setattr(xtime, "sleep", lambda s: None)

    assert worker._press_j_batch() is True
    assert dummy.calls == [("press", "j")] * POPULAR_INITIAL_J_COUNT
    assert ranges == []

    first_call_uniforms = uniform_calls.copy()
    fast_count_first = min(2, POPULAR_INITIAL_J_COUNT)
    assert first_call_uniforms[:fast_count_first] == [FAST_J_INITIAL_DELAY_RANGE] * fast_count_first
    assert all(r == (STEP_PAUSE_MIN, STEP_PAUSE_MAX) for r in first_call_uniforms[fast_count_first:])
    assert worker._popular_initial_scroll_pending is False

    dummy.calls.clear()
    ranges.clear()
    uniform_calls.clear()

    assert worker._press_j_batch() is True
    assert dummy.calls == [("press", "j")] * 3
    assert ranges == [(2, 5)]

    second_call_uniforms = uniform_calls.copy()
    fast_count_second = min(2, len(second_call_uniforms))
    assert second_call_uniforms[:fast_count_second] == [FAST_J_INITIAL_DELAY_RANGE] * fast_count_second
    assert all(r == (STEP_PAUSE_MIN, STEP_PAUSE_MAX) for r in second_call_uniforms[fast_count_second:])


def test_reset_step_open_state_clears_sections_once_per_section():
    dummy = DummyKB()
    worker = _make_worker(dummy)
    worker.search_open_policy = "once_per_section"
    worker._opened_sections = set()
    worker._opened_this_step = False

    assert worker._should_open_search_now("alpha") is True

    worker._mark_opened("alpha")
    assert worker._should_open_search_now("alpha") is False

    SchedulerWorker._reset_step_open_state(worker)

    assert worker._opened_sections == set()
    assert worker._opened_this_step is False
    assert worker._should_open_search_now("alpha") is True
