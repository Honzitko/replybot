import os
import sys
import json
import time
import random
import platform
import urllib.request
import urllib.error
from keyboard_controller import KeyboardController
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QTextEdit, QLabel,
    QSpinBox, QHBoxLayout, QMessageBox
)
from PyQt5.QtCore import QTimer, QThread, pyqtSignal
import faulthandler

# Enable faulthandler to help debug crashes
faulthandler.enable()


# Determine platform once for hotkey selection
IS_MAC = platform.system() == "Darwin"


def has_internet(timeout: float = 5.0) -> bool:
    """Check for a working internet connection.

    A simple HTTP request is used; failures are silently ignored and ``False``
    is returned so the caller can decide how to handle lack of connectivity.
    """
    try:
        urllib.request.urlopen("https://www.google.com", timeout=timeout)
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
        self._paused = False
        self.keyboard = KeyboardController()

    def run(self):
        try:
            # initial countdown – allow extra time for the user to focus the
            # browser window and the page to finish loading
            for i in range(15, 0, -1):
                if not self._running:
                    self.log.emit("Startup cancelled.")
                    return
                self.log.emit(f"Starting in {i}...")
                time.sleep(1)

            count = 0
            idx = 0

            # Switch focus to the previously active window (expected browser)
            switch_keys = ("command", "tab") if IS_MAC else ("alt", "tab")
            self.keyboard.hotkey(*switch_keys)
            self.log.emit("Activated previous window.")
            self.log.emit("Waiting for browser to load...")
            # Give the browser additional time to become active and load
            time.sleep(5.0)
            self.log.emit("Ensure only one browser tab is open.")

            while self._running and count < self.limit:
                while self._paused and self._running:
                    time.sleep(0.1)


                # Move forward through a few posts with 'J'
                jumps = random.randint(1, 3)
                for _ in range(jumps):
                    self.keyboard.press("j")
                    # give the platform time to load the next post
                    time.sleep(random.uniform(3.0, 5.0))

                # Like the current post
                self.keyboard.press("l")
                time.sleep(random.uniform(2.0, 4.0))

                # Open the reply field
                self.keyboard.press("r")
                time.sleep(random.uniform(3.0, 5.0))

                text = self.replies[idx]
                idx = (idx + 1) % len(self.replies)
                self.keyboard.typewrite(text, interval=random.uniform(0.05, 0.2))

                # brief pause before submitting the reply
                time.sleep(random.uniform(0.5, 1.5))

                # Hit Enter to submit the reply and allow time for it to post
                self.keyboard.press("enter")
                time.sleep(random.uniform(4.0, 6.0))

                # Close the reply box so navigation shortcuts work on the next loop
                self.keyboard.press("esc")
                time.sleep(random.uniform(1.0, 2.0))

                count += 1
                self.log.emit(f"Replied #{count}: '{text}'")

                delay = self.cadence + random.randint(1, 4)
                self.log.emit(f"Waiting {delay}s...")
                for _ in range(delay):
                    if not self._running:
                        break
                    while self._paused and self._running:
                        time.sleep(0.1)
                    time.sleep(1)

            self.log.emit(f"Finished: {count} replies.")
        except Exception as exc:
            self.log.emit(f"Error: {exc}")

    def stop(self):
        self._running = False

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def is_paused(self):
        return self._paused


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

        # Start/Pause/Stop buttons
        btns = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start)
        self.start_btn.setObjectName("start_btn")
        btns.addWidget(self.start_btn)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.pause_or_resume)
        self.pause_btn.setEnabled(False)

        self.pause_btn.setObjectName("pause_btn")
        btns.addWidget(self.pause_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setObjectName("stop_btn")
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
        if not has_internet():
            QMessageBox.warning(self, "No internet", "An active internet connection is required.")
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Running", "Bot already active.")
            return
        self.worker = ReplyWorker(replies, self.limit.value(), self.cadence.value())
        self.worker.log.connect(self.log)
        self.worker.start()

        self.pause_btn.setEnabled(True)
        self.pause_btn.setText("Pause")


        self.log("Bot started. Switch to the browser window now.")
        # Minimize the GUI so the browser receives keystrokes
        self.showMinimized()

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.log("Stop requested.")
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText("Pause")

    def pause_or_resume(self):
        if not self.worker:
            return
        if not self.worker.is_paused():
            self.worker.pause()
            self.pause_btn.setText("Resume")
            self.log("Pause requested.")
        else:
            self.worker.resume()
            self.pause_btn.setText("Pause")
            self.log("Resume requested.")

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
