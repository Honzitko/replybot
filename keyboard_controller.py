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

    def press(self, key: str, delay: float = 0.0) -> None:
        """Press and release ``key``.

        Parameters
        ----------
        key:
            Name of the key to press.  Values that match attributes on
            ``pynput.keyboard.Key`` will be converted automatically, otherwise
            the raw string is forwarded.
        delay:
            Optional delay in seconds to wait after the key is released.  A
            small delay can improve reliability on some platforms and allows
            more precise timing when sending a series of key presses.
        """

        k = self._to_key(key)
        self._controller.press(k)
        self._controller.release(k)
        if delay:
            time.sleep(delay)

    def hotkey(self, *keys: str) -> None:
        mapped = [self._to_key(k) for k in keys]
        # ``Controller.pressed`` is not guaranteed to exist on the object
        # assigned to ``_controller`` (tests replace it with a simple dummy
        # object).  Fall back to manually pressing and releasing the keys if the
        # helper is missing.
        if hasattr(self._controller, "pressed"):
            with self._controller.pressed(*mapped):
                pass
        else:
            for k in mapped:
                self._controller.press(k)
            for k in reversed(mapped):
                self._controller.release(k)

    def typewrite(self, text: str, interval: float = 0.0) -> None:
        for ch in text:
            self.press(ch)
            if interval:
                time.sleep(interval)
