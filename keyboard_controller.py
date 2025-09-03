import time
from pynput.keyboard import Controller, Key

# Mapping for synonymous key names.  The `Key` enum from pynput exposes
# attributes such as ``Key.enter`` or ``Key.cmd``.  Rather than maintain a
# large lookup table of every supported key we only provide aliases for the few
# names that differ from the attribute on ``Key`` and fall back to the provided
# string if no attribute exists.
_ALIASES = {
    "command": "cmd",
}


class KeyboardController:
    """Wrapper around pynput's Controller providing simple key helpers."""

    def __init__(self):
        self._controller = Controller()

    def _to_key(self, key: str):
        key = _ALIASES.get(key, key)
        return getattr(Key, key, key)

    def press(self, key: str) -> None:
        k = self._to_key(key)
        self._controller.press(k)
        self._controller.release(k)

    def hotkey(self, *keys: str) -> None:
        mapped = [self._to_key(k) for k in keys]
        # `Controller.pressed` handles pressing the given keys on entry and
        # releasing them on exit, ensuring they are released even if an error
        # occurs while holding them.
        with self._controller.pressed(*mapped):
            pass

    def typewrite(self, text: str, interval: float = 0.0) -> None:
        for ch in text:
            self.press(ch)
            if interval:
                time.sleep(interval)
