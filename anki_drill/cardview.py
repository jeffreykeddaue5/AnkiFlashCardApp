"""
cardview.py
The flip-card display surface, with a QtWebEngine implementation and a plain-Qt fallback.

The web view is preferred because Anki note templates are HTML and CSS, and because the
Quizlet-style flip is a CSS 3D transform. Where QtWebEngine is missing the app degrades to a
QTextBrowser that swaps sides without animating.
"""

import json

from PyQt5.QtCore import QFile, QIODevice, QObject, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QTextBrowser

from .rendering import setCardScript, shellHtml

try:
    from PyQt5.QtWebChannel import QWebChannel
    from PyQt5.QtWebEngineWidgets import (
        QWebEnginePage, QWebEngineSettings, QWebEngineView)
    WEB_ENGINE_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the local PyQt5 build
    WEB_ENGINE_AVAILABLE = False
    # Aliased purely so the web classes below still parse; createCardView never builds them.
    QWebEnginePage = QObject


def _channelScript():
    """Return Qt's bundled qwebchannel.js source, or '' when it cannot be read."""
    handle = QFile(":/qtwebchannel/qwebchannel.js")
    if not handle.open(QIODevice.ReadOnly):
        return ""
    try:
        return bytes(handle.readAll()).decode("utf-8", "replace")
    finally:
        handle.close()


class CardViewBase(QObject):
    """Common interface the main window drives, regardless of the backing widget."""

    flipRequested = pyqtSignal()

    def widget(self):
        """Return the QWidget to place in the layout."""
        raise NotImplementedError

    def setCard(self, term, definition, css, flipped, frontLabel, backLabel):
        """Show a card, with `flipped` deciding which face starts face-up."""
        raise NotImplementedError

    def setFlipped(self, flipped):
        """Flip the card already on screen."""
        raise NotImplementedError

    def setZoom(self, factor):
        """Scale the note content, leaving the deck's own relative styling intact."""
        raise NotImplementedError

    def showMessage(self, text):
        """Replace the card with a plain centred message (end of round, nothing due, ...)."""
        raise NotImplementedError

    def setMediaRoot(self, path):
        """Point relative <img> sources at the profile's collection.media directory."""

    def replayAudio(self):
        """Play the sounds on the face-up side again; a no-op where audio is unsupported."""


class _DrillBridge(QObject):
    """The object exposed to the page over the QWebChannel as `drill`."""

    flipClicked = pyqtSignal()

    @pyqtSlot()
    def flip(self):
        """Called from JavaScript when the user clicks the card."""
        self.flipClicked.emit()


class _DrillPage(QWebEnginePage):
    """A page that keeps note-template links out of the card surface."""

    def acceptNavigationRequest(self, url, navigationType, isMainFrame):
        """Send real links to the system browser instead of navigating the card away."""
        if navigationType == QWebEnginePage.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return super(_DrillPage, self).acceptNavigationRequest(url, navigationType, isMainFrame)

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceId):
        """Swallow console noise from note templates that expect Anki's own JS environment."""


class WebCardView(CardViewBase):
    """Flip card rendered by QtWebEngine using the CSS shell in rendering.py."""

    def __init__(self, mediaRoot=None, parent=None):
        super(WebCardView, self).__init__(parent)
        self._view = QWebEngineView()
        self._page = _DrillPage(self._view)
        self._view.setPage(self._page)

        # Clicks on the card come back over this channel; see the note in the shell script.
        self._bridge = _DrillBridge()
        self._bridge.flipClicked.connect(self.flipRequested)
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("drill", self._bridge)
        self._page.setWebChannel(self._channel)
        self._channelScript = _channelScript()

        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.ShowScrollBars, False)
        # Card audio autoplays when a face comes up, which is not itself a user gesture.
        settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)

        self._ready = False
        self._pending = []
        self._mediaRoot = mediaRoot
        self._page.loadFinished.connect(self._onLoadFinished)
        self._loadShell()

    def widget(self):
        """Return the QWebEngineView."""
        return self._view

    def _loadShell(self):
        """(Re)load the static shell page, anchored at the current media directory."""
        self._ready = False
        baseUrl = QUrl()
        if self._mediaRoot:
            baseUrl = QUrl.fromLocalFile(self._mediaRoot.rstrip("/") + "/")
        self._page.setHtml(shellHtml(self._channelScript), baseUrl)

    def _onLoadFinished(self, ok):
        """Flush any JS that was requested before the shell finished loading."""
        self._ready = bool(ok)
        if not self._ready:
            return
        pending, self._pending = self._pending, []
        for script in pending:
            self._page.runJavaScript(script)

    def _run(self, script):
        """Run JS now, or queue it until the shell is ready."""
        if self._ready:
            self._page.runJavaScript(script)
        else:
            self._pending.append(script)

    def setMediaRoot(self, path):
        """Re-anchor relative media paths, reloading the shell so the base URL takes effect."""
        if path == self._mediaRoot:
            return
        self._mediaRoot = path
        self._pending = []
        self._loadShell()

    def setCard(self, term, definition, css, flipped, frontLabel, backLabel):
        """Push both faces into the shell in one call."""
        self._run(setCardScript(term, definition, css, flipped, frontLabel, backLabel))

    def setFlipped(self, flipped):
        """Animate the card to the requested face."""
        self._run("setFlipped(%s);" % ("true" if flipped else "false"))

    def setZoom(self, factor):
        """Apply a CSS zoom to both faces so deck styling scales as a whole."""
        self._run("setZoom(%s);" % round(float(factor), 3))

    def showMessage(self, text):
        """Hide the card and show a centred message instead."""
        self._run("showEmpty(%s);" % json.dumps(text))

    def replayAudio(self):
        """Play the face-up side's sounds again from the start."""
        self._run("playVisibleFace();")


class TextCardView(CardViewBase):
    """Fallback card surface for builds without QtWebEngine: one side at a time, no animation."""

    PALETTE = "background:#0f1225; color:#e9ecf8;"

    def __init__(self, mediaRoot=None, parent=None):
        super(TextCardView, self).__init__(parent)
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setAlignment(Qt.AlignCenter)
        self._mediaRoot = mediaRoot
        self._zoom = 1.0
        self._term = ""
        self._definition = ""
        self._labels = ("Term", "Definition")
        self._flipped = False
        self._applyPalette()

    def widget(self):
        """Return the QTextBrowser."""
        return self._browser

    def setMediaRoot(self, path):
        """Set the search root the browser uses to resolve relative image sources."""
        self._mediaRoot = path
        if path:
            self._browser.setSearchPaths([path])

    def _applyPalette(self):
        """Apply the dark stylesheet, at the current zoom, to the browser."""
        self._browser.setStyleSheet(
            "QTextBrowser { border:none; padding:24px; font-size:%dpx; %s }"
            % (max(8, int(round(20 * self._zoom))), self.PALETTE))

    def setZoom(self, factor):
        """Scale the browser's base font size, the nearest equivalent to a CSS zoom."""
        self._zoom = float(factor)
        self._applyPalette()

    def _repaint(self):
        """Redraw whichever side is currently face-up."""
        label = self._labels[1] if self._flipped else self._labels[0]
        body = self._definition if self._flipped else self._term
        self._browser.setHtml(
            "<div style='text-align:center'>"
            "<p style='font-size:11px; letter-spacing:2px; color:#8c93b4'>%s</p>"
            "<div style='font-size:26px'>%s</div></div>" % (label.upper(), body))

    def setCard(self, term, definition, css, flipped, frontLabel, backLabel):
        """Store both sides and show the requested one; notetype CSS is ignored here."""
        self._term, self._definition = term, definition
        self._labels = (frontLabel, backLabel)
        self._flipped = bool(flipped)
        self._repaint()

    def setFlipped(self, flipped):
        """Swap the visible side."""
        self._flipped = bool(flipped)
        self._repaint()

    def showMessage(self, text):
        """Show a centred plain message in place of the card."""
        self._browser.setHtml(
            "<div style='text-align:center; color:#8b93bd; font-size:18px; "
            "white-space:pre-wrap'>%s</div>" % text)


def createCardView(mediaRoot=None, parent=None):
    """Return the best available card view for this PyQt5 build."""
    if WEB_ENGINE_AVAILABLE:
        return WebCardView(mediaRoot, parent)
    return TextCardView(mediaRoot, parent)
