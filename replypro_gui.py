import os
import sys
import json
import time
import random
import platform
import pyautogui
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit, QLabel,
    QSpinBox, QHBoxLayout, QMessageBox
)
from PyQt5.QtCore import QTimer, QThread, pyqtSignal
import faulthandler
import urllib.request

# Enable faulthandler to help debug crashes
faulthandler.enable()


# Determine platform once for hotkey selection
IS_MAC = platform.system() == "Darwin"


def check_network(url: str = "https://www.google.com/generate_204", timeout: int = 5) -> bool:
    """Return True if a lightweight GET request succeeds."""
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


class ReplyWorker(QThread):
    """Background worker that types and posts replies automatically."""

    log = pyqtSignal(str)

    def __init__(self, replies, limit, cadence):
        super().__init__()
        self.replies = replies
        self.limit = limit
        self.cadence = cadence
        self._running = True

    def run(self):
        try:
            # initial countdown
            for i in range(10, 0, -1):
                if not self._running:
                    self.log.emit("Startup cancelled.")
                    return
                self.log.emit(f"Starting in {i}...")
                time.sleep(1)

            count = 0
            idx = 0

            # Slow down PyAutoGUI actions so the target app can keep up
            pyautogui.PAUSE = 2.0
            # Disable failsafe so the mouse in the corner doesn't abort the run
            pyautogui.FAILSAFE = False

            # Switch focus to the previously active window (expected browser)
            switch_keys = ("command", "tab") if IS_MAC else ("alt", "tab")
            pyautogui.hotkey(*switch_keys)
            self.log.emit("Activated previous window.")
            time.sleep(2.0)

            while self._running and count < self.limit:
                if not check_network():
                    self.log.emit("Network check failed. Stopping worker.")
                    self._running = False
                    QTimer.singleShot(
                        0,
                        lambda: QMessageBox.warning(
                            None, "Network Error", "Network appears unreachable."
                        ),
                    )
                    break
                # Like sequence: press J then L then R
                pyautogui.press("j")
                time.sleep(random.uniform(1.5, 2.0))
                pyautogui.press("l")
                time.sleep(random.uniform(1.5, 2.0))
                pyautogui.press("r")
                time.sleep(random.uniform(1.5, 2.0))

                text = self.replies[idx]
                idx = (idx + 1) % len(self.replies)
                pyautogui.typewrite(text, interval=random.uniform(0.05, 0.2))
                time.sleep(random.uniform(1.0, 2.0))

                # Platform-specific "send" shortcut (Ctrl+Enter on Windows, Cmd+Enter on macOS)
                submit_keys = ("command", "enter") if IS_MAC else ("ctrl", "enter")
                pyautogui.hotkey(*submit_keys)
                # Allow a brief moment for the comment to send
                time.sleep(2.0)

                # Verify that the page reflects the expected state after sending
                try:
                    posted = pyautogui.locateOnScreen("comment_posted.png", confidence=0.8)
                    error = pyautogui.locateOnScreen("error_popup.png", confidence=0.8)
                except Exception as exc:
                    posted = None
                    error = None
                    self.log.emit(f"Screen check failed: {exc}")

                if not posted or error:
                    self.log.emit("Screen state mismatch detected. Stopping worker.")
                    screenshot = f"mismatch_{int(time.time())}.png"
                    try:
                        pyautogui.screenshot(screenshot)
                        self.log.emit(f"Saved screenshot to {screenshot}")
                    except Exception as exc:
                        self.log.emit(f"Failed to save screenshot: {exc}")
                    self._running = False
                    break

                count += 1
                self.log.emit(f"Replied #{count}: '{text}'")

                delay = self.cadence + random.randint(1, 4)
                self.log.emit(f"Waiting {delay}s...")
                for _ in range(delay):
                    if not self._running:
                        break
                    time.sleep(1)

            self.log.emit(f"Finished: {count} replies.")
        except Exception as exc:
            self.log.emit(f"Error: {exc}")

    def stop(self):
        self._running = False


class ReplyPRO(QWidget):
    """Simple GUI for configuring and launching the reply bot."""

    SETTINGS_FILE = "settings.json"

    def __init__(self):
        super().__init__()
        self.worker = None
        self.initUI()
        self.load_settings()

    def initUI(self):
        self.setWindowTitle("ReplyPRO 3.0")
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Replies (one per line):"))
        self.reply_input = QTextEdit()
        layout.addWidget(self.reply_input)

        row = QHBoxLayout()
        row.addWidget(QLabel("Cadence (s):"))
        self.cadence = QSpinBox()
        self.cadence.setRange(1, 60)
        self.cadence.setValue(5)
        row.addWidget(self.cadence)
        row.addWidget(QLabel("Limit:"))
        self.limit = QSpinBox()
        self.limit.setRange(1, 500)
        self.limit.setValue(50)
        row.addWidget(self.limit)
        layout.addLayout(row)

        # Start/Stop buttons
        btns = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start)
        btns.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        btns.addWidget(self.stop_btn)
        layout.addLayout(btns)

        # Save/Load settings
        settings_btns = QHBoxLayout()
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self.save_settings)
        settings_btns.addWidget(save_btn)
        load_btn = QPushButton("Load Settings")
        load_btn.clicked.connect(self.load_settings)
        settings_btns.addWidget(load_btn)
        layout.addLayout(settings_btns)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(QLabel("Log:"))
        layout.addWidget(self.log_view)

        self.setLayout(layout)

    def log(self, message):
        timestamp = time.strftime('[%H:%M:%S]')
        QTimer.singleShot(0, lambda: self.log_view.append(f"{timestamp} {message}"))

    def start(self):
        replies = [r.strip() for r in self.reply_input.toPlainText().splitlines() if r.strip()]
        if not replies:
            QMessageBox.warning(self, "No replies", "Add at least one reply.")
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Running", "Bot already active.")
            return
        self.worker = ReplyWorker(replies, self.limit.value(), self.cadence.value())
        self.worker.log.connect(self.log)
        self.worker.start()


        self.log("Bot started. Switch to the browser window now.")
        # Minimize the GUI so the browser receives keystrokes
        self.showMinimized()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.log("Stop requested.")

    # --- Settings handling -------------------------------------------------
    def save_settings(self):
        data = {
            "replies": self.reply_input.toPlainText().splitlines(),
            "cadence": self.cadence.value(),
            "limit": self.limit.value(),
        }
        try:
            with open(self.SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            self.log("Settings saved.")
        except OSError as exc:
            self.log(f"Failed to save settings: {exc}")

    def load_settings(self):
        if not os.path.exists(self.SETTINGS_FILE):
            return
        try:
            with open(self.SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            self.log(f"Failed to load settings: {exc}")
            return
        self.reply_input.setPlainText("\n".join(data.get("replies", [])))
        self.cadence.setValue(data.get("cadence", 5))
        self.limit.setValue(data.get("limit", 50))
        self.log("Settings loaded.")


    def closeEvent(self, event):
        """Save settings automatically when the window closes."""
        self.save_settings()
        event.accept()


if __name__ == "__main__":

    app = QApplication(sys.argv)
    window = ReplyPRO()
    window.show()
    sys.exit(app.exec())
