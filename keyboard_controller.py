import time
from pynput.keyboard import Controller, Key

# Mapping of string names to pynput Key constants
_KEY_MAP = {
    "enter": Key.enter,
    "esc": Key.esc,
    "tab": Key.tab,
    "alt": Key.alt,
    "command": Key.cmd,
    "cmd": Key.cmd,
}


class KeyboardController:
    """Wrapper around pynput's Controller providing simple key helpers."""

    def __init__(self):
        self._controller = Controller()

    def _to_key(self, key: str):
        return _KEY_MAP.get(key, key)

    def press(self, key: str) -> None:
        k = self._to_key(key)
        self._controller.press(k)
        self._controller.release(k)

    def hotkey(self, *keys: str) -> None:
        mapped = [self._to_key(k) for k in keys]
        for k in mapped:
            self._controller.press(k)
        for k in reversed(mapped):
            self._controller.release(k)

    def typewrite(self, text: str, interval: float = 0.0) -> None:
        for ch in text:
            self.press(ch)
            if interval:
                time.sleep(interval)
