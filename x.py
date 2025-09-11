"""Entry point for the Reply Bot application.

This wrapper can launch either the :class:`ReplyPRO` GUI or the manual
"X Scheduler" interface.  By default ``python x.py`` starts the ReplyPRO
PyQt5 application.  Passing ``--scheduler`` will instead launch the
Tkinter‑based scheduler.
"""

import argparse
import sys


def run_replypro() -> None:
    """Launch the original ReplyPRO PyQt5 GUI."""

    from PyQt5.QtWidgets import QApplication
    from replypro_gui import ReplyPRO

    app = QApplication(sys.argv)
    window = ReplyPRO()
    window.show()
    sys.exit(app.exec())


def run_scheduler() -> None:
    """Launch the X Scheduler Tkinter GUI."""

    from x_scheduler_gui_safe import App

    app = App()
    app.mainloop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reply Bot launcher")
    parser.add_argument(
        "--scheduler",
        action="store_true",
        help="Launch the X Scheduler interface instead of ReplyPRO",
    )
    args = parser.parse_args()

    if args.scheduler:
        run_scheduler()
    else:
        run_replypro()
