import pytest
pk = pytest.importorskip("pynput.keyboard")
from keyboard_controller import KeyboardController


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
    kc.typewrite("ab")
    assert dummy.events == [
        ("press", "a"),
        ("release", "a"),
        ("press", "b"),
        ("release", "b"),
    ]
