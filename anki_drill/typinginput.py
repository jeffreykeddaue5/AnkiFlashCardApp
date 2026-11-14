"""
typinginput.py
The answer box for typing mode: a QLineEdit that survives Korean input methods.

With ibus/fcitx Hangul, the syllable being composed lives in the IME's preedit, not in the
box's text. Pressing Enter mid-syllable can deliver the Return key before (or instead of)
committing it, so a plain QLineEdit would grade "개운해" when the user typed "개운해요".
This box remembers the preedit and includes it, and defers the submit one event-loop turn
so a commit that arrives right after the key press is counted too.
"""

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QLineEdit


class TypingLineEdit(QLineEdit):
    """Answer box. Emits `submitted(text)` on Enter and `accepted()` on Ctrl+Enter."""

    submitted = pyqtSignal(str)
    accepted = pyqtSignal()

    def __init__(self, parent=None):
        super(TypingLineEdit, self).__init__(parent)
        self._preedit = ""
        font = QFont(self.font())
        font.setPointSize(18)
        self.setFont(font)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setObjectName("typingInput")

    def inputMethodEvent(self, event):
        """Track the syllable still being composed."""
        self._preedit = event.preeditString()
        super(TypingLineEdit, self).inputMethodEvent(event)

    def answerText(self):
        """Everything typed so far, including a syllable the IME has not committed yet."""
        return self.text() + (self._preedit if self._preedit else "")

    def clearAnswer(self):
        """Empty the box and forget any half-composed syllable."""
        self._preedit = ""
        self.clear()

    def keyPressEvent(self, event):
        """Enter submits; Ctrl+Enter accepts an "almost" answer as a typo."""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ControlModifier:
                self.accepted.emit()
                return
            # Let any commit triggered by this key land first.
            QTimer.singleShot(0, self._emitSubmit)
            return
        super(TypingLineEdit, self).keyPressEvent(event)

    def _emitSubmit(self):
        text = self.answerText()
        self._preedit = ""
        self.submitted.emit(text)
