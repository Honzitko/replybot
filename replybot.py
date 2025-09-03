"""Entry point for the Reply Bot application.

This thin wrapper simply launches the :class:`ReplyPRO` GUI from
``replypro_gui``.  It exists so the application can be started with
``python replybot.py`` and to match the expected program name in user
instructions.
"""

import sys
from PyQt5.QtWidgets import QApplication
from replypro_gui import ReplyPRO


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ReplyPRO()
    window.show()
    sys.exit(app.exec())
