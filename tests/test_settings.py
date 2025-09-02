import os
import pytest

# Use pytest.importorskip so the test suite is skipped if PyQt5 or its
# dependencies (e.g., libGL) are unavailable in the execution environment.
QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
from PyQt5.QtWidgets import QApplication

# Set Qt to run offscreen for headless environments
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from replypro_gui import ReplyPRO

@pytest.fixture(scope="module")
def app():
    app = QApplication([])
    yield app
    app.quit()


def test_save_and_load_settings(tmp_path, app):
    # Use a temporary settings file
    ReplyPRO.SETTINGS_FILE = str(tmp_path / "settings.json")

    window = ReplyPRO()
    window.reply_input.setPlainText("Hello\nWorld")
    window.cadence.setValue(7)
    window.limit.setValue(3)

    window.save_settings()
    settings_path = tmp_path / "settings.json"
    assert settings_path.exists()

    # Reset fields and load
    window.reply_input.clear()
    window.cadence.setValue(1)
    window.limit.setValue(1)

    window.load_settings()
    assert window.reply_input.toPlainText().splitlines() == ["Hello", "World"]
    assert window.cadence.value() == 7
    assert window.limit.value() == 3
