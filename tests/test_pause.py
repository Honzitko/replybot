import os
import pytest

QtWidgets = pytest.importorskip(
    "PyQt5.QtWidgets", reason="requires PyQt5", exc_type=ImportError
)
from PyQt5.QtWidgets import QApplication

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

replypro_gui = pytest.importorskip(
    "replypro_gui", reason="requires replypro_gui", exc_type=ImportError
)
ReplyPRO = replypro_gui.ReplyPRO
ReplyWorker = replypro_gui.ReplyWorker


@pytest.fixture(scope="module")
def app():
    app = QApplication([])
    yield app
    app.quit()



def test_pause_button_exists_and_toggles(tmp_path, app):
    ReplyPRO.SETTINGS_FILE = str(tmp_path / "settings.json")
    window = ReplyPRO()

    pause_button = window.findChild(QtWidgets.QPushButton, "pause_btn")
    assert pause_button is not None
    assert pause_button is window.pause_btn
    assert not pause_button.isEnabled()

    window.worker = ReplyWorker(["hi"], 1, 1)

    # Enable manually for test and toggle
    pause_button.setEnabled(True)
    window.pause_or_resume()
    assert window.worker.is_paused()
    assert pause_button.text() == "Resume"
    window.pause_or_resume()
    assert not window.worker.is_paused()
    assert pause_button.text() == "Pause"
