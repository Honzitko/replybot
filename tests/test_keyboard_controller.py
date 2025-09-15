import pytest
import time
import random
pk = pytest.importorskip(
    "pynput.keyboard", reason="requires pynput", exc_type=ImportError
)
from keyboard_controller import KeyboardController, is_app_generated


class DummyController:
    def __init__(self):
        self.events = []

    def press(self, key):
        self.events.append(("press", key))

    def release(self, key):
        self.events.append(("release", key))


def test_hotkey(monkeypatch):
    kc = KeyboardController()
    dummy = DummyController()
    monkeypatch.setattr(kc, "_controller", dummy)
    kc.hotkey("alt", "tab")
    assert dummy.events == [
        ("press", pk.Key.alt),
        ("press", pk.Key.tab),
        ("release", pk.Key.tab),
        ("release", pk.Key.alt),
    ]


def test_typewrite(monkeypatch):
    kc = KeyboardController()
    dummy = DummyController()
    monkeypatch.setattr(kc, "_controller", dummy)
    kc.typewrite("ab", interval=0)
    assert dummy.events == [
        ("press", "a"),
        ("release", "a"),
        ("press", "b"),
        ("release", "b"),
    ]


def test_typewrite_miss_chance(monkeypatch):
    kc = KeyboardController()
    dummy = DummyController()
    monkeypatch.setattr(kc, "_controller", dummy)
    kc.typewrite("abc", interval=0, miss_chance=1.0)
    assert dummy.events == []



def test_typewrite_jitter(monkeypatch):
    kc = KeyboardController()
    dummy = DummyController()
    monkeypatch.setattr(kc, "_controller", dummy)
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(random, "uniform", lambda a, b: b)
    kc.typewrite("ab", interval=0.2, jitter=0.1)
    assert sleeps == [0.3, 0.3]



def test_marks_generated(monkeypatch):
    kc = KeyboardController()
    dummy = DummyController()
    monkeypatch.setattr(kc, "_controller", dummy)
    kc.press("a")
    assert is_app_generated()


def test_case_insensitive(monkeypatch):
    kc = KeyboardController()
    dummy = DummyController()
    monkeypatch.setattr(kc, "_controller", dummy)
    kc.hotkey("CTRL", "ENTER")
    assert dummy.events == [
        ("press", pk.Key.ctrl),
        ("press", pk.Key.enter),
        ("release", pk.Key.enter),
        ("release", pk.Key.ctrl),
    ]
