"""
app.py
Application entry point: Qt attributes that must be set before QApplication, then the window.
"""

import sys

from PyQt5.QtCore import QCoreApplication, Qt

# QtWebEngine requires both of these before a QApplication exists: the shared-context
# attribute, and the module import itself. Doing it at import time is the documented order.
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
try:
    from PyQt5 import QtWebEngineWidgets  # noqa: F401  pylint: disable=unused-import
except ImportError:  # pragma: no cover - the app falls back to a QTextBrowser card view
    pass

from PyQt5.QtWidgets import QApplication  # noqa: E402  (must follow the attribute call)

from .mainwindow import DrillWindow  # noqa: E402


def main(argv=None):
    """Start the drill app and return its exit code."""
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Anki Drill")
    app.setApplicationDisplayName("Anki Drill")

    window = DrillWindow()
    window.show()
    return app.exec_()
