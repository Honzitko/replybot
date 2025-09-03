import os
import pytest

QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
from PyQt5.QtWidgets import QApplication

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from replypro_gui import ReplyPRO, ReplyWorker


@pytest.fixture(scope="module")
def app():
    app = QApplication([])
    yield app
    app.quit()


def test_pause_button_toggles(tmp_path, app):
    ReplyPRO.SETTINGS_FILE = str(tmp_path / "settings.json")
    window = ReplyPRO()
    window.worker = ReplyWorker(["hi"], 1, 1)

    # Initially disabled
    assert not window.pause_btn.isEnabled()

    # Enable manually for test and toggle
    window.pause_btn.setEnabled(True)
    window.pause_or_resume()
    assert window.worker.is_paused()
    assert window.pause_btn.text() == "Resume"
    window.pause_or_resume()
    assert not window.worker.is_paused()
    assert window.pause_btn.text() == "Pause"
