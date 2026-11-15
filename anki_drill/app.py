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

import threading  # noqa: E402

from PyQt5.QtGui import QPixmap  # noqa: E402
from PyQt5.QtWidgets import QApplication, QSplashScreen  # noqa: E402  (must follow the attribute call)

from . import ankisync  # noqa: E402
from .collection import findProfiles  # noqa: E402
from .mainwindow import DrillWindow  # noqa: E402


def syncWithSplash(app):
    """Sync with AnkiWeb before the collection is read, keeping a splash responsive."""
    if not ankisync.ankiAvailable():
        return []
    pixmap = QPixmap(420, 90)
    pixmap.fill(Qt.white)
    splash = QSplashScreen(pixmap)
    splash.showMessage("Syncing with AnkiWeb…", Qt.AlignCenter, Qt.black)
    splash.show()
    app.processEvents()

    results = []
    worker = threading.Thread(
        target=lambda: results.extend(ankisync.syncAll(findProfiles())), daemon=True)
    worker.start()
    while worker.is_alive():
        app.processEvents()
        worker.join(0.05)
    splash.close()
    return results


def main(argv=None):
    """Start the drill app and return its exit code."""
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Anki Drill")
    app.setApplicationDisplayName("Anki Drill")

    results = syncWithSplash(app)
    for line in results:
        print(line, file=sys.stderr)

    window = DrillWindow()
    if results:
        window.statusBar().showMessage(" · ".join(results), 15000)
    window.show()
    return app.exec_()
