"""Very small subset of the pynput.keyboard API used for testing.

This stub is only intended to satisfy the unit tests in environments where
pynput is not installed.  It provides Key constants for the keys referenced in
our tests and a Controller class with ``press``, ``release`` and ``pressed``
methods.
"""

class Key:
    alt = "alt"
    tab = "tab"
    cmd = "cmd"
    enter = "enter"
    esc = "esc"


class Controller:
    """Minimal stand‑in for ``pynput.keyboard.Controller``."""

    def press(self, key):
        """Record a key press (no-op in stub)."""
        # The real implementation sends the key event to the OS.  Tests patch
        # the controller object so this stub does not need to do anything.
        pass

    def release(self, key):
        """Record a key release (no-op in stub)."""
        pass

    class _Pressed:
        def __init__(self, controller, keys):
            self._controller = controller
            self._keys = keys

        def __enter__(self):
            for k in self._keys:
                self._controller.press(k)
            return self

        def __exit__(self, exc_type, exc, tb):
            for k in reversed(self._keys):
                self._controller.release(k)

    def pressed(self, *keys):
        return Controller._Pressed(self, keys)
