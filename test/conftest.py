"""
conftest.py
Shared fixtures: an offscreen QApplication for the GUI tests, built at most once per run.
"""

import os

import pytest


@pytest.fixture(scope="session", name="qtApp")
def qtAppFixture():
    """Return a session-wide offscreen QApplication, skipping if PyQt5 is unavailable."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault(
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "--no-sandbox --disable-gpu --disable-dev-shm-usage")

    QtCore = pytest.importorskip("PyQt5.QtCore")
    # QtWebEngine insists on both of these before any QApplication exists.
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_ShareOpenGLContexts)
    pytest.importorskip("PyQt5.QtWebEngineWidgets")
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolateTypingStats(tmp_path, monkeypatch):
    """Keep every test away from the real ~/.local/share/anki-drill/typing-stats.json."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from anki_drill import persistence
    monkeypatch.setattr(persistence, "TYPING_PATH", str(tmp_path / "typing-stats.json"))
    # Some window tests never patched the session file, so they wrote to the real one.
    monkeypatch.setattr(persistence, "APP_DIR", str(tmp_path / "app-state"))
    monkeypatch.setattr(persistence, "STATE_PATH",
                        str(tmp_path / "app-state" / "session-state.json"))
    monkeypatch.setattr(persistence, "SETTINGS_PATH",
                        str(tmp_path / "app-state" / "settings.json"))
