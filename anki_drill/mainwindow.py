"""
mainwindow.py
The Quizlet-style drill window: filters on top, a flip card in the middle, controls below.

The window owns the open collection snapshot and the current StudySession, renders cards on
demand, and translates clicks and keystrokes into session moves. It never writes to Anki.
"""

import time

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QShortcut, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget)

from .cardview import createCardView
from .persistence import (
    applySessionState, clearSessionState, loadSessionState, loadSettings, loadTypingStats,
    recordTypingResult, saveSessionState, saveSettings, saveTypingStats, serialiseSession)
from .collection import (
    CollectionSnapshot, candidateRoots, findProfiles, pinningAvailable,
    profileForCollection)
from .production import (
    ANSWER_DICTIONARY, ANSWER_ENGLISH, ANSWER_FORMS, ANSWER_SENTENCE, MAX_HINT_LEVEL,
    MODE_ENGLISH, RESULT_ALMOST, RESULT_CORRECT, RESULT_WRONG, buildTypingSpec,
    checkAnswer, matchesForRetype, promptHtml, resultHtml, typingSummary)
from .recommend import (
    DEFAULT_LIMIT, REASON_LEECH, REASON_MISSED, REASON_PINNED, REASON_TROUBLE, REASON_TYPED,
    badgeText, buildRecommended, primaryReason, summaryText)
from .rendering import renderCard
from .scheduling import (
    QUEUE_LABELS, QUEUE_NAMES, dayStartMilliseconds, describeCard, schedTimingToday)
from .session import FILTER_ALL, STATUS_KNOWN, STATUS_LEARNING, StudySession
from .typinginput import TypingLineEdit

ALL_DECKS = "All decks"

# Which cards to study: the app's own recommended list, or the hand-picked Anki queues.
LIST_RECOMMENDED = "recommended"
LIST_CUSTOM = "custom"

# Recommended-list size choices; None means no cap.
LIMIT_CHOICES = (20, 30, 40, 60, None)

# How a card is studied. Auto: flip it the first time, type it after that, and type the
# cards already known to be weak (pinned, trouble, missed) straight away.
MODE_AUTO = "auto"
MODE_FLIP = "flip"
MODE_TYPE = "type"
MODE_LABELS = ((MODE_AUTO, "Auto"), (MODE_FLIP, "Flip only"), (MODE_TYPE, "Type all"))
TYPE_FIRST_REASONS = frozenset(
    [REASON_PINNED, REASON_TROUBLE, REASON_MISSED, REASON_TYPED, REASON_LEECH])

# Typing phases for the card on screen.
PHASE_ANSWER = "answer"     # waiting for the first attempt
PHASE_CHECKED = "checked"   # verdict shown, Enter moves on
PHASE_RETYPE = "retype"     # wrong or revealed: optionally copy the answer, Enter moves on

# The Answer control: what a typing card asks for.
ANSWER_LABELS = (
    (ANSWER_DICTIONARY, "Korean: dictionary form"),
    (ANSWER_SENTENCE, "Korean: sentence form"),
    (ANSWER_ENGLISH, "English meaning"),
)

# Clean answers in a row that "Drill this card" asks for before letting the card go.
DRILL_TARGET = 3

# The ? / F1 overlay.
KEYBOARD_HELP = """
<h3>Keyboard</h3>
<table cellspacing="0" cellpadding="3">
<tr><td colspan="2"><b>Flip cards</b></td></tr>
<tr><td>Space / click</td><td>Flip</td></tr>
<tr><td>← / →, Enter</td><td>Previous / next card</td></tr>
<tr><td>1 / 2</td><td>Still learning / Got it</td></tr>
<tr><td>Esc</td><td>Back to the front</td></tr>
<tr><td colspan="2"><br><b>Typing cards</b></td></tr>
<tr><td>Enter</td><td>Check · then next card</td></tr>
<tr><td>Show answer</td><td>Just look (a peek); Enter on an empty box moves on</td></tr>
<tr><td>↑ / ↓, Flip, click</td><td>Once revealed: switch between prompt and answer</td></tr>
<tr><td>Esc</td><td>Try this card again</td></tr>
<tr><td>Ctrl+D</td><td>Drill this card until you get it %d times in a row</td></tr>
<tr><td>Ctrl+H</td><td>Next hint</td></tr>
<tr><td>Ctrl+Enter</td><td>That was a typo / I was right</td></tr>
<tr><td>← / →</td><td>Previous / next card when the box is empty or checked</td></tr>
<tr><td>Alt+← / Alt+→</td><td>Previous / next card, always</td></tr>
<tr><td colspan="2"><br><b>Anywhere</b> (not while typing an answer)</td></tr>
<tr><td>T</td><td>Cycle Mode (Auto / Flip only / Type all)</td></tr>
<tr><td>A</td><td>Cycle Answer (dictionary / sentence / English)</td></tr>
<tr><td>B</td><td>Show the back first</td></tr>
<tr><td>S</td><td>Shuffle</td></tr>
<tr><td>F</td><td>Cycle the Show filter</td></tr>
<tr><td>R</td><td>Replay audio</td></tr>
<tr><td>F5 / Ctrl+R</td><td>Reload the collection</td></tr>
<tr><td>Ctrl+ + / Ctrl+ −</td><td>Text size</td></tr>
<tr><td>? / F1</td><td>This help</td></tr>
</table>
""" % DRILL_TARGET

# Single-key shortcuts that would steal keystrokes from the answer box.
TYPING_CONFLICTS = frozenset(
    ["Space", "Right", "Return", "Enter", "Left", "1", "2", "B", "S", "F", "R", "T", "A",
     "?"])

# Human names for the grade filter, used in the "nothing matches" message.
QUEUE_GRADE_LABELS = {
    STATUS_LEARNING: "Still learning",
    STATUS_KNOWN: "Got it",
}

# Note content starts slightly enlarged: most decks set a body-text size that reads small on
# a full-window flash card. The A−/A+ buttons move it between these bounds.
DEFAULT_ZOOM = 1.2
ZOOM_STEP = 1.15
ZOOM_MIN = 0.5
ZOOM_MAX = 3.0

# The app is dark-only, so every colour is pinned here rather than inherited from the
# desktop theme; a light system palette must not leak into the chrome.
STYLE_SHEET = """
QMainWindow, QWidget#chrome { background: #0f1225; }
QLabel { color: #e9ecf8; }
QLabel#status { color: #8b93bd; font-size: 12px; }
QLabel#cardInfo { color: #6d76a0; font-size: 12px; }
QCheckBox { color: #e9ecf8; font-size: 13px; spacing: 6px; }
QCheckBox::indicator {
    width: 14px; height: 14px; border-radius: 4px;
    border: 1px solid #3a4173; background: #171b35;
}
QCheckBox::indicator:checked { background: #4d8dfb; border-color: #4d8dfb; }
QToolTip { background: #1a1f3d; color: #e9ecf8; border: 1px solid #2b3160; padding: 4px; }
QComboBox {
    color: #e9ecf8; font-size: 13px; background: #171b35;
    border: 1px solid #2b3160; border-radius: 7px; padding: 4px 8px; min-height: 22px;
}
QComboBox:hover { border-color: #3a4173; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: #1a1f3d; color: #e9ecf8;
    border: 1px solid #2b3160; outline: none;
    selection-background-color: #4d8dfb; selection-color: #ffffff;
}
QPushButton {
    border: 1px solid #2b3160; border-radius: 8px; padding: 7px 14px;
    background: #1a1f3d; color: #e9ecf8; font-size: 13px;
}
QPushButton:hover { background: #242a52; border-color: #3a4173; }
QPushButton:disabled { color: #545b87; border-color: #232849; background: #151931; }
QPushButton#segLeft  { border-top-right-radius: 0; border-bottom-right-radius: 0; }
QPushButton#segRight { border-top-left-radius: 0; border-bottom-left-radius: 0;
                       border-left: none; }
QPushButton:checked { background: #4d8dfb; color: #ffffff; border-color: #4d8dfb; }
QPushButton#learning { color: #ff8a6b; border-color: #5c3a35; }
QPushButton#learning:hover { background: #33222a; border-color: #7d4b42; }
QPushButton#known { color: #5cd39b; border-color: #2f5348; }
QPushButton#known:hover { background: #1d3a33; border-color: #3f6f5f; }
QPushButton#flip { font-weight: 600; }
QProgressBar { border: none; background: #1c2142; height: 6px; border-radius: 3px; }
QProgressBar::chunk { background: #4d8dfb; border-radius: 3px; }
QFrame#rule { color: #232849; }
QLabel#recommend { color: #8b93bd; font-size: 12px; }
QLineEdit#typingInput {
    background: #171b35; color: #e9ecf8; border: 2px solid #2b3160; border-radius: 10px;
    padding: 8px 12px; selection-background-color: #4d8dfb;
}
QLineEdit#typingInput:focus { border-color: #4d8dfb; }
QLineEdit#typingInput[phase="retype"] { border-color: #ff8a6b; }
QLineEdit#typingInput[phase="checked"] { color: #8b93bd; }
"""


class DrillWindow(QMainWindow):
    """Main application window."""

    def __init__(self, parent=None):
        super(DrillWindow, self).__init__(parent)
        self.setWindowTitle("Anki Drill")
        self.resize(940, 700)
        self.setStyleSheet(STYLE_SHEET)

        self.profiles = findProfiles()
        self.snapshot = None
        self.notetypes = {}
        self.today = 0
        self.newAllowances = {}
        self.dayStartMs = 0
        self.session = StudySession([])
        self.showBackFirst = False
        self.isFlipped = False
        self.zoom = DEFAULT_ZOOM
        self._renderCache = {}
        self._loading = False
        self._sessionKey = None
        self.recommendation = None
        self.typingStats = loadTypingStats()
        self._specCache = {}
        self.typing = None
        self._shortcuts = {}
        self.settings = loadSettings()
        self.answerForm = self.settings.get("answerForm", ANSWER_DICTIONARY)
        if self.answerForm not in ANSWER_FORMS:
            self.answerForm = ANSWER_DICTIONARY
        # card id -> what happened when it was typed this session (for the summary).
        self.sessionLog = {}
        # "Drill this card": the card being drilled and its clean answers in a row.
        self.drillCardId = None
        self.drillStreak = 0

        self._buildUi()
        self._buildShortcuts()
        self.cardView.setZoom(self.zoom)

        if not self.profiles:
            self._showNoProfiles()
        else:
            self.reloadCollection()

    # ---------------------------------------------------------------- construction

    def _buildUi(self):
        """Assemble the window chrome, card surface and control rows."""
        central = QWidget()
        central.setObjectName("chrome")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        layout.addLayout(self._buildSourceRow())
        layout.addLayout(self._buildOptionRow())
        layout.addWidget(self._rule())
        layout.addLayout(self._buildProgressRow())

        self.cardView = createCardView()
        self.cardView.flipRequested.connect(self._onCardClicked)
        self.cardView.widget().setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.cardView.widget(), 1)

        layout.addWidget(self._buildTypingRow())

        self.controlStack = QStackedWidget()
        self.controlStack.addWidget(self._buildStudyControls())
        self.controlStack.addWidget(self._buildSummaryControls())
        layout.addWidget(self.controlStack)

        self.setCentralWidget(central)

    def _rule(self):
        """Return a thin horizontal separator."""
        rule = QFrame()
        rule.setObjectName("rule")
        rule.setFrameShape(QFrame.HLine)
        rule.setFrameShadow(QFrame.Plain)
        return rule

    def _buildSourceRow(self):
        """Row one: which profile, which deck, and which scheduler queues to pull from."""
        row = QHBoxLayout()
        row.setSpacing(8)

        self.profileCombo = QComboBox()
        for profile in self.profiles:
            self.profileCombo.addItem(profile.name, profile)
        self.profileCombo.currentIndexChanged.connect(self._onProfileChanged)
        row.addWidget(QLabel("Profile"))
        row.addWidget(self.profileCombo)

        self.openButton = QPushButton("Open…")
        self.openButton.setToolTip("Pick a collection.anki2 that was not found automatically")
        self.openButton.setFocusPolicy(Qt.NoFocus)
        self.openButton.clicked.connect(self.openCollectionDialog)
        row.addWidget(self.openButton)

        self.deckCombo = QComboBox()
        self.deckCombo.setMinimumWidth(190)
        self.deckCombo.currentIndexChanged.connect(self._onFilterChanged)
        row.addWidget(QLabel("Deck"))
        row.addWidget(self.deckCombo)

        row.addSpacing(10)
        self.listCombo = QComboBox()
        self.listCombo.addItem("Today's recommended", LIST_RECOMMENDED)
        self.listCombo.addItem("Custom queues", LIST_CUSTOM)
        self.listCombo.setToolTip(
            "Today's recommended: one list built from your synced collection —\n"
            "pinned, trouble words, today's misses, typing misses, due, new.\n"
            "Custom queues: pick Anki's queues by hand.")
        self.listCombo.currentIndexChanged.connect(self._onListModeChanged)
        row.addWidget(self.listCombo)

        self.limitCombo = QComboBox()
        for limit in LIMIT_CHOICES:
            self.limitCombo.addItem("All" if limit is None else "Top %d" % limit, limit)
        self.limitCombo.setCurrentIndex(LIMIT_CHOICES.index(DEFAULT_LIMIT))
        self.limitCombo.setToolTip("How many cards the recommended list holds")
        self.limitCombo.currentIndexChanged.connect(self._onFilterChanged)
        row.addWidget(self.limitCombo)

        self.recommendLabel = QLabel("")
        self.recommendLabel.setObjectName("recommend")
        self.recommendLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self.recommendLabel, 1)

        self.queueBox = QWidget()
        queueRow = QHBoxLayout(self.queueBox)
        queueRow.setContentsMargins(0, 0, 0, 0)
        queueRow.setSpacing(8)
        self.queueChecks = {}
        for name in QUEUE_NAMES:
            check = QCheckBox(QUEUE_LABELS[name])
            # Both halves of today's workload are on by default.
            check.setChecked(True)
            check.stateChanged.connect(self._onFilterChanged)
            self.queueChecks[name] = check
            queueRow.addWidget(check)
        row.addWidget(self.queueBox)
        self.queueBox.setVisible(False)
        self.queueChecks["new"].setToolTip(
            "New cards Anki would introduce today: your deck's daily limit,\n"
            "less however many you have already started today.")
        self.queueChecks["pinned"].setToolTip(
            "Cards pinned with the Today's Review Words add-on, today or yesterday.\n"
            "Hidden when that add-on is not in use.")
        self.queueChecks["reviewed"].setToolTip(
            "Cards you have already answered today, in Anki or here.\n"
            "Keeps today's work drillable after Anki has scheduled it into the future.")

        row.addStretch(1)
        self.reloadButton = QPushButton("Reload")
        self.reloadButton.setToolTip("Re-read the collection from disk (F5)")
        self.reloadButton.clicked.connect(self.reloadCollection)
        row.addWidget(self.reloadButton)
        return row

    def _buildOptionRow(self):
        """Row two: which side leads, shuffle, dark mode and session restart."""
        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(QLabel("Show first"))
        self.frontFirstButton = QPushButton("Front")
        self.frontFirstButton.setObjectName("segLeft")
        self.backFirstButton = QPushButton("Back")
        self.backFirstButton.setObjectName("segRight")
        self.sideGroup = QButtonGroup(self)
        for index, button in enumerate((self.frontFirstButton, self.backFirstButton)):
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)
            self.sideGroup.addButton(button, index)
            row.addWidget(button)
        self.frontFirstButton.setChecked(True)
        self.sideGroup.buttonClicked.connect(lambda _button: self._onSideToggled())
        self.backFirstButton.setToolTip("Start each card on its back and recall the front (B)")

        row.addSpacing(12)
        # Narrows the round to one grade so the hard cards can be drilled on their own.
        row.addWidget(QLabel("Show"))
        self.filterCombo = QComboBox()
        self.filterCombo.setMinimumWidth(130)
        for label, value in (("All cards", FILTER_ALL),
                             ("✕ Still learning", STATUS_LEARNING),
                             ("✓ Got it", STATUS_KNOWN)):
            self.filterCombo.addItem(label, value)
        self.filterCombo.setToolTip("Drill only the cards you graded one way (F to cycle)")
        self.filterCombo.currentIndexChanged.connect(self._onStatusFilterChanged)
        row.addWidget(self.filterCombo)

        row.addSpacing(12)
        row.addWidget(QLabel("Mode"))
        self.modeCombo = QComboBox()
        for value, label in MODE_LABELS:
            self.modeCombo.addItem(label, value)
        self.modeCombo.setToolTip(
            "Auto: flip a card the first time, type the Korean after that\n"
            "(pinned, trouble and missed cards are typed straight away).\n"
            "Flip only / Type all: one way for every card.  (T to cycle)")
        self.modeCombo.currentIndexChanged.connect(self._onModeChanged)
        row.addWidget(self.modeCombo)

        row.addSpacing(12)
        row.addWidget(QLabel("Answer"))
        self.answerCombo = QComboBox()
        for value, label in ANSWER_LABELS:
            self.answerCombo.addItem(label, value)
        self.answerCombo.setCurrentIndex(self.answerCombo.findData(self.answerForm))
        self.answerCombo.setToolTip(
            "What a typing card asks for  (A to cycle)\n"
            "Dictionary form: 개운하다 (the sentence's 개운해요 also counts)\n"
            "Sentence form: the conjugated word that fills the example's gap\n"
            "English meaning: see the Korean, type what it means")
        self.answerCombo.currentIndexChanged.connect(self._onAnswerFormChanged)
        row.addWidget(self.answerCombo)

        row.addSpacing(12)
        self.shuffleCheck = QCheckBox("Shuffle")
        self.shuffleCheck.stateChanged.connect(self._onShuffleToggled)
        row.addWidget(self.shuffleCheck)

        row.addSpacing(12)
        # Deck CSS sets its own font size, so scaling is a zoom over whatever it produced
        # rather than a font-size we would have to fight the deck stylesheet for.
        self.smallerButton = QPushButton("A−")
        self.smallerButton.setToolTip("Smaller text (Ctrl+−)")
        self.smallerButton.clicked.connect(lambda: self.adjustZoom(1 / ZOOM_STEP))
        self.largerButton = QPushButton("A+")
        self.largerButton.setToolTip("Larger text (Ctrl++)")
        self.largerButton.clicked.connect(lambda: self.adjustZoom(ZOOM_STEP))
        for button in (self.smallerButton, self.largerButton):
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedWidth(40)
            row.addWidget(button)

        row.addStretch(1)
        self.helpButton = QPushButton("?")
        self.helpButton.setToolTip("Keyboard shortcuts (? or F1)")
        self.helpButton.setFocusPolicy(Qt.NoFocus)
        self.helpButton.setFixedWidth(34)
        self.helpButton.clicked.connect(self.showKeyboardHelp)
        row.addWidget(self.helpButton)
        self.restartButton = QPushButton("Restart session")
        self.restartButton.clicked.connect(self.restartSession)
        row.addWidget(self.restartButton)
        return row

    def _buildProgressRow(self):
        """The progress bar and the round/position readout beneath it."""
        column = QVBoxLayout()
        column.setSpacing(4)
        self.progressBar = QProgressBar()
        self.progressBar.setTextVisible(False)
        column.addWidget(self.progressBar)
        line = QHBoxLayout()
        self.statusLabel = QLabel("")
        self.statusLabel.setObjectName("status")
        line.addWidget(self.statusLabel)
        line.addStretch(1)
        # Badges can run long; the label may be clipped rather than widen the window.
        self.cardInfoLabel = QLabel("")
        self.cardInfoLabel.setObjectName("cardInfo")
        self.cardInfoLabel.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.cardInfoLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        line.addWidget(self.cardInfoLabel, 1)
        column.addLayout(line)
        return column

    def _buildTypingRow(self):
        """The answer box with its hint and typo buttons; shown only on typing cards."""
        self.typingRow = QWidget()
        row = QHBoxLayout(self.typingRow)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.typingInput = TypingLineEdit()
        self.typingInput.setPlaceholderText("Type the Korean, then Enter")
        self.typingInput.submitted.connect(self._onTypingSubmitted)
        self.typingInput.accepted.connect(self._acceptAsTypo)
        self.typingInput.navigate.connect(
            lambda step: self.goPrev() if step < 0 else self.goNext())
        # Up/Down only swap faces once the answer is out; they never reveal it.
        self.typingInput.flipRequested.connect(
            lambda: self.typing and self.typing["phase"] != PHASE_ANSWER and self.flipCard())
        row.addWidget(self.typingInput, 1)

        self.hintButton = QPushButton("Hint")
        self.hintButton.setToolTip("Syllables → first consonants (초성) → first syllable (Ctrl+H)")
        self.hintButton.clicked.connect(self.showTypingHint)
        row.addWidget(self.hintButton)

        self.typoButton = QPushButton("That was a typo ✓")
        self.typoButton.setObjectName("known")
        self.typoButton.setToolTip("Count this answer as correct (Ctrl+Enter)")
        self.typoButton.clicked.connect(self._acceptAsTypo)
        row.addWidget(self.typoButton)

        self.drillButton = QPushButton("🎯 Drill this card")
        self.drillButton.setCheckable(True)
        self.drillButton.setToolTip(
            "Keep this card until you type it right %d times in a row (Ctrl+D)" % DRILL_TARGET)
        self.drillButton.clicked.connect(self.toggleDrill)
        row.addWidget(self.drillButton)

        self.retryButton = QPushButton("↻  Try again")
        self.retryButton.setToolTip("Hide the answer and type this card again (Esc)")
        self.retryButton.clicked.connect(self.retryCard)
        row.addWidget(self.retryButton)

        for button in (self.hintButton, self.typoButton, self.drillButton, self.retryButton):
            button.setFocusPolicy(Qt.NoFocus)
        self.typoButton.setVisible(False)
        self.retryButton.setVisible(False)
        self.typingRow.setVisible(False)
        return self.typingRow

    def _buildStudyControls(self):
        """The main button row: navigate, flip and grade."""
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self.prevButton = QPushButton("◀  Prev")
        self.prevButton.clicked.connect(self.goPrev)
        row.addWidget(self.prevButton)
        row.addStretch(1)

        self.learningButton = QPushButton("✕  Still learning")
        self.learningButton.setObjectName("learning")
        self.learningButton.setToolTip("Keep this card for the next round (1)")
        self.learningButton.clicked.connect(lambda: self.gradeCurrent(STATUS_LEARNING))
        row.addWidget(self.learningButton)

        self.flipButton = QPushButton("Flip")
        self.flipButton.setObjectName("flip")
        self.flipButton.setToolTip("Show the other side (Space)")
        self.flipButton.clicked.connect(self.flipCard)
        row.addWidget(self.flipButton)

        self.knownButton = QPushButton("✓  Got it")
        self.knownButton.setObjectName("known")
        self.knownButton.setToolTip("Retire this card for the rest of the session (2)")
        self.knownButton.clicked.connect(lambda: self.gradeCurrent(STATUS_KNOWN))
        row.addWidget(self.knownButton)

        row.addStretch(1)
        self.nextButton = QPushButton("Next  ▶")
        self.nextButton.clicked.connect(self.goNext)
        row.addWidget(self.nextButton)

        for button in (self.prevButton, self.learningButton, self.flipButton,
                       self.knownButton, self.nextButton):
            button.setFocusPolicy(Qt.NoFocus)
        return page

    def _buildSummaryControls(self):
        """The button row shown once a round finishes."""
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addStretch(1)

        # Without this the summary is a dead end: the study row's Prev button is hidden here.
        self.backToCardsButton = QPushButton("◀  Back to cards")
        self.backToCardsButton.setToolTip("Return to the last card of the round (←)")
        self.backToCardsButton.clicked.connect(self._returnToCards)
        row.addWidget(self.backToCardsButton)

        self.nextRoundButton = QPushButton("Study the ones you missed")
        self.nextRoundButton.clicked.connect(self.startNextRound)
        row.addWidget(self.nextRoundButton)

        self.replayButton = QPushButton("Replay this round")
        self.replayButton.clicked.connect(self.replayRound)
        row.addWidget(self.replayButton)

        self.finishButton = QPushButton("Start over")
        self.finishButton.clicked.connect(self.restartSession)
        row.addWidget(self.finishButton)

        row.addStretch(1)
        for button in (self.backToCardsButton, self.nextRoundButton,
                       self.replayButton, self.finishButton):
            button.setFocusPolicy(Qt.NoFocus)
        return page

    def _buildShortcuts(self):
        """Wire the keyboard: Quizlet's space-to-flip and arrow navigation, plus grading keys."""
        bindings = (
            ("Space", self.flipCard),
            ("Right", self.goNext),
            ("Return", self.goNext),
            ("Enter", self.goNext),
            ("Left", self.goPrev),
            # Always free, even mid-word in the answer box (plain arrows move its cursor).
            ("Alt+Left", self.goPrev),
            ("Alt+Right", self.goNext),
            ("1", lambda: self.gradeCurrent(STATUS_LEARNING)),
            ("2", lambda: self.gradeCurrent(STATUS_KNOWN)),
            ("B", self.toggleBackFirst),
            ("S", lambda: self.shuffleCheck.toggle()),
            ("F", self.cycleStatusFilter),
            ("R", self.replayAudio),
            ("T", self.cycleMode),
            ("A", self.cycleAnswerForm),
            ("?", self.showKeyboardHelp),
            ("F1", self.showKeyboardHelp),
            ("Ctrl+D", self.toggleDrill),
            ("Ctrl+H", self.showTypingHint),
            ("F5", self.reloadCollection),
            ("Ctrl+R", self.reloadCollection),
            ("Ctrl++", lambda: self.adjustZoom(ZOOM_STEP)),
            ("Ctrl+=", lambda: self.adjustZoom(ZOOM_STEP)),
            ("Ctrl+-", lambda: self.adjustZoom(1 / ZOOM_STEP)),
        )
        for sequence, handler in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts[sequence] = shortcut
        # Window-only, so Esc still closes dialogs opened from here.
        retry = QShortcut(QKeySequence("Escape"), self)
        retry.activated.connect(self.retryCard)
        self._shortcuts["Escape"] = retry

    # ---------------------------------------------------------------- collection

    def _currentProfile(self):
        """Return the selected Profile, or None when the combo is empty.

        This reads the combo rather than the auto-detected list, so a collection added by
        the Open… button works even when nothing was found automatically.
        """
        return self.profileCombo.currentData()

    def _showNoProfiles(self):
        """Explain that no Anki collection could be found and disable the controls."""
        self.cardView.showMessage(
            "No Anki collection found automatically.\n\n"
            "Use the Open… button to pick your collection.anki2.\n\n"
            "It usually lives in one of:\n"
            "  Linux    ~/.local/share/Anki2  or  ~/.var/app/net.ankiweb.Anki/data/Anki2\n"
            "  Windows  %APPDATA%\\Anki2\n"
            "  macOS    ~/Library/Application Support/Anki2")
        self.controlStack.setCurrentIndex(0)
        for widget in (self.prevButton, self.nextButton, self.flipButton,
                       self.learningButton, self.knownButton, self.restartButton):
            widget.setEnabled(False)
        self.statusLabel.setText("No collection")

    def openCollectionDialog(self):
        """Let the user point at a collection.anki2 directly and switch to it."""
        startDir = ""
        roots = candidateRoots()
        if roots:
            startDir = roots[0]
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "Open an Anki collection", startDir,
            "Anki collection (collection.anki2 *.anki2);;All files (*)")
        if not chosen:
            return

        profile = profileForCollection(chosen)
        self._loading = True
        # Reuse the entry if this collection is already listed, so it cannot pile up.
        existing = next((i for i in range(self.profileCombo.count())
                         if self.profileCombo.itemData(i).collectionPath
                         == profile.collectionPath), None)
        if existing is None:
            self.profileCombo.addItem("%s (opened)" % profile.name, profile)
            existing = self.profileCombo.count() - 1
        self.profileCombo.setCurrentIndex(existing)
        self._loading = False

        self._setStudyControlsEnabled(True)
        self.restartButton.setEnabled(True)
        self.reloadCollection()

    def reloadCollection(self):
        """Re-open the selected profile from disk and rebuild the session."""
        profile = self._currentProfile()
        if profile is None:
            return

        if self.snapshot is not None:
            self.snapshot.close()
            self.snapshot = None

        try:
            self.snapshot = CollectionSnapshot(profile)
            self.snapshot.open()
        except Exception as error:  # noqa: BLE001 - surfaced to the user, not swallowed
            self.snapshot = None
            QMessageBox.critical(self, "Could not open collection",
                                 "%s\n\n%s" % (profile.collectionPath, error))
            return

        self.notetypes = self.snapshot.notetypes()
        self.today, nextDayAt = schedTimingToday(
            self.snapshot.creationStamp(), int(time.time()), self.snapshot.rolloverHour())
        self.dayStartMs = dayStartMilliseconds(nextDayAt)
        self.newAllowances = self.snapshot.deckNewAllowances(self.today)
        self._setPinnedAvailable(pinningAvailable(profile, self.snapshot))
        self._renderCache = {}
        self._specCache = {}
        self.typingStats = loadTypingStats()
        # A profile switch reloads the shell page, so the view-level settings are reapplied.
        self.cardView.setMediaRoot(profile.mediaPath)
        self.cardView.setZoom(self.zoom)

        self._populateDecks()
        self._refreshCounts()
        self.rebuildSession()

    def _populateDecks(self):
        """Refill the deck combo, keeping the previous selection where it still exists."""
        previous = self.deckCombo.currentText()
        self._loading = True
        self.deckCombo.clear()
        self.deckCombo.addItem(ALL_DECKS)
        for _deckId, name in self.snapshot.decks():
            self.deckCombo.addItem(name)
        index = self.deckCombo.findText(previous)
        self.deckCombo.setCurrentIndex(index if index >= 0 else 0)
        self._loading = False

    def _selectedDeckIds(self):
        """Return the deck ids to study, or None for every deck.

        Selecting a parent deck includes its subdecks, matching how Anki treats a deck click.
        """
        chosen = self.deckCombo.currentText()
        if not chosen or chosen == ALL_DECKS:
            return None
        prefix = chosen + "::"
        return [deckId for deckId, name in self.snapshot.decks()
                if name == chosen or name.startswith(prefix)]

    def _selectedQueues(self):
        """Return the set of queue names whose checkbox is both ticked and on offer.

        isHidden is used rather than isVisible because the latter is false for every widget
        until the window itself is shown.
        """
        return {name for name, check in self.queueChecks.items()
                if check.isChecked() and not check.isHidden()}

    def _listMode(self):
        """LIST_RECOMMENDED or LIST_CUSTOM."""
        return self.listCombo.currentData() or LIST_RECOMMENDED

    def _limit(self):
        """The recommended list's cap, or None for no cap."""
        return self.limitCombo.currentData()

    def _sessionScope(self):
        """Return (collectionPath, deckIds, queues) for the currently selected study set.

        The recommended list is keyed by a marker plus its cap, so its saved grades never
        mix with a hand-picked queue selection.
        """
        profile = self._currentProfile()
        if profile is None:
            return None, None, None
        if self._listMode() == LIST_RECOMMENDED:
            queues = {LIST_RECOMMENDED, "limit=%s" % self._limit()}
        else:
            queues = self._selectedQueues()
        return profile.collectionPath, self._selectedDeckIds(), queues

    def _loadPersistedSession(self):
        """Apply any remembered local grades for the current study set."""
        collectionPath, deckIds, queues = self._sessionScope()
        if collectionPath is None:
            return
        state = loadSessionState(collectionPath, deckIds, queues, self.today)
        if state is not None:
            applySessionState(self.session, state)

    def _persistSession(self):
        """Save the current local grades for the current study set."""
        collectionPath, deckIds, queues = self._sessionScope()
        if collectionPath is None:
            return
        saveSessionState(collectionPath, deckIds, queues, serialiseSession(self.session), self.today)

    def _clearPersistedSession(self):
        """Forget the current study set's saved grades."""
        collectionPath, deckIds, queues = self._sessionScope()
        if collectionPath is None:
            return
        clearSessionState(collectionPath, deckIds, queues)

    def _setPinnedAvailable(self, available):
        """Show or hide the Pinned checkbox, clearing it on the way out."""
        check = self.queueChecks["pinned"]
        if not available and check.isChecked():
            self._loading = True
            check.setChecked(False)
            self._loading = False
        check.setVisible(available)

    def _refreshCounts(self):
        """Put live per-queue counts on the checkbox labels."""
        if self.snapshot is None:
            return
        counts = self.snapshot.queueCounts(
            self._selectedDeckIds(), self.today, self.newAllowances, self.dayStartMs)
        for name, check in self.queueChecks.items():
            check.setText("%s (%d)" % (QUEUE_LABELS[name], counts.get(name, 0)))

    # ---------------------------------------------------------------- session

    def rebuildSession(self):
        """Fetch cards for the current filters and start a fresh session over them."""
        if self.snapshot is None:
            return
        groupOf = None
        if self._listMode() == LIST_RECOMMENDED:
            self.recommendation = buildRecommended(
                self.snapshot, self._selectedDeckIds(), self.today, self.dayStartMs,
                self.newAllowances, self.typingStats,
                includePinned=not self.queueChecks["pinned"].isHidden(),
                limit=self._limit())
            cards = self.recommendation.cards
            recommendation = self.recommendation
            groupOf = lambda card: primaryReason(recommendation, card.cardId)  # noqa: E731
            self.recommendLabel.setText(summaryText(self.recommendation))
            self.recommendLabel.setToolTip(
                "📌 pinned · ⚠ trouble word · ✕ missed today · ⌨ typed wrong\n"
                "↻ due · 🩹 keeps lapsing · ★ new · ✓ done today")
        else:
            self.recommendation = None
            self.recommendLabel.setText("")
            cards = self.snapshot.fetchCards(
                self._selectedDeckIds(), self._selectedQueues(),
                self.today, self.newAllowances, self.dayStartMs)
        self.typing = None
        self.sessionLog = {}
        self._stopDrill()
        self.session = StudySession(cards, shuffle=self.shuffleCheck.isChecked(),
                                    groupOf=groupOf)
        self._loadPersistedSession()
        self.resetStatusFilter()
        self.isFlipped = False
        self.controlStack.setCurrentIndex(0)
        self.showCurrentCard()

    def restartSession(self):
        """Start the current card set again from round one."""
        self.typing = None
        self.sessionLog = {}
        self._stopDrill()
        self.session.reset()
        self._clearPersistedSession()
        self.resetStatusFilter()
        self.isFlipped = False
        self.controlStack.setCurrentIndex(0)
        self.showCurrentCard()

    def startNextRound(self):
        """Move on to the cards that were not marked known."""
        self.typing = None
        if self.session.startNextRound():
            self._persistSession()
            self.resetStatusFilter()
            self.isFlipped = False
            self.controlStack.setCurrentIndex(0)
            self.showCurrentCard()
        else:
            self._showSessionComplete()

    def replayRound(self):
        """Replay every card in the round just finished."""
        self.typing = None
        self.session.replayRound()
        self._persistSession()
        self.resetStatusFilter()
        self.isFlipped = False
        self.controlStack.setCurrentIndex(0)
        self.showCurrentCard()

    # ---------------------------------------------------------------- card display

    def _renderedFaces(self, card):
        """Return (term, definition, css) for `card`, rendering and caching on first use."""
        cached = self._renderCache.get(card.cardId)
        if cached is not None:
            return cached
        notetype = self.notetypes.get(card.notetypeId)
        if notetype is None:
            faces = ("(unknown note type)", "(unknown note type)", "")
        else:
            term, definition = renderCard(card, notetype)
            faces = (term, definition, notetype.css)
        self._renderCache[card.cardId] = faces
        return faces

    def showCurrentCard(self):
        """Render the card under the cursor onto the flip surface and refresh the chrome."""
        card = self.session.current()
        if card is None:
            self._setTypingVisible(False)
            self._showEmptyState()
            return

        if self.drillCardId is not None and self.drillCardId != card.cardId:
            self._stopDrill()
        if self._shouldType(card):
            if self.typing is None or self.typing["cardId"] != card.cardId:
                self.typing = {"cardId": card.cardId, "spec": self._typingSpec(card),
                               "hint": 0, "phase": PHASE_ANSWER, "result": None}
                self.typingInput.clearAnswer()
            self._renderTyping(animate=False)
            self._setTypingVisible(True)
            self._refreshCardInfo(card)
            self._refreshProgress()
            return

        self.typing = None
        self._setTypingVisible(False)
        term, definition, css = self._renderedFaces(card)
        # The leading face is whichever side the "Show first" toggle selects.
        if self.showBackFirst:
            faceA, faceB, labelA, labelB = definition, term, "Back", "Front"
        else:
            faceA, faceB, labelA, labelB = term, definition, "Front", "Back"

        self.cardView.setCard(faceA, faceB, css, self.isFlipped, labelA, labelB)
        self._refreshCardInfo(card)
        self._refreshProgress()

    def _refreshCardInfo(self, card):
        """Deck, scheduling state and, on the recommended list, why the card is there."""
        bits = [card.deckName, describeCard(card, self.today)]
        if self.recommendation is not None:
            badges = badgeText(self.recommendation.reasons.get(card.cardId))
            if badges:
                bits.insert(0, badges)
        if self.typing is not None:
            bits.insert(0, "⌨ typing")
        if self.drillCardId == card.cardId:
            bits.insert(0, "🎯 drill %d/%d" % (self.drillStreak, DRILL_TARGET))
        self.cardInfoLabel.setText("  ·  ".join(bits))

    # ---------------------------------------------------------------- typing mode

    def _typingSpec(self, card):
        """What to ask for `card` in typing mode (cached), or None if nothing is typeable."""
        if card.cardId in self._specCache:
            return self._specCache[card.cardId]
        notetype = self.notetypes.get(card.notetypeId)
        spec = None
        if notetype is not None:
            values = list(card.fields) + [""] * max(0, len(notetype.fields) - len(card.fields))
            spec = buildTypingSpec(dict(zip(notetype.fields, values)),
                                   answerForm=self.answerForm)
        self._specCache[card.cardId] = spec
        return spec

    def _shouldType(self, card):
        """Whether `card` is studied by typing right now, given the Mode setting."""
        mode = self.modeCombo.currentData()
        if mode == MODE_FLIP or self._typingSpec(card) is None:
            return False
        if mode == MODE_TYPE:
            return True
        # Auto: a card seen once this session is typed; known-weak cards from the start.
        if self.session.roundNumber >= 2:
            return True
        reasons = set(self.recommendation.reasons.get(card.cardId, [])
                      if self.recommendation is not None else [])
        if self.recommendation is None and "review_pinned" in card.tags.split():
            reasons.add(REASON_PINNED)
        return bool(reasons & TYPE_FIRST_REASONS)

    def _setTypingVisible(self, visible):
        """Show or hide the answer box, handing the keyboard to it while it is up."""
        self.typingRow.setVisible(visible)
        for sequence, shortcut in self._shortcuts.items():
            if sequence in TYPING_CONFLICTS:
                shortcut.setEnabled(not visible)
        if visible:
            self.typingInput.setFocus()
        else:
            self.flipButton.setText("Flip")

    def _setTypingPhase(self, phase):
        """Move the typing card to `phase` and restyle the box to match."""
        self.typing["phase"] = phase
        self.typingInput.setReadOnly(phase == PHASE_CHECKED)
        self.typingInput.setProperty("phase", phase)
        self.typingInput.style().unpolish(self.typingInput)
        self.typingInput.style().polish(self.typingInput)
        result = self.typing.get("result")
        self.typoButton.setVisible(self._canAcceptAsRight())
        self.typoButton.setText("I was right ✓" if self.typing["spec"].mode == MODE_ENGLISH
                                else "That was a typo ✓")
        self.hintButton.setEnabled(
            phase == PHASE_ANSWER and self.typing["hint"] < MAX_HINT_LEVEL)
        self.retryButton.setVisible(phase != PHASE_ANSWER)
        self.drillButton.setChecked(self.drillCardId == self.typing["cardId"])
        self.flipButton.setText("Show answer" if phase == PHASE_ANSWER else "Flip")
        english = self.typing["spec"].mode == MODE_ENGLISH
        placeholders = {
            PHASE_ANSWER: "Type the %s, then Enter" % ("English" if english else "Korean"),
            PHASE_CHECKED: "Enter: next card  ·  Esc: try again  ·  ↑↓: flip",
            PHASE_RETYPE: "Copy the answer to practise, or Enter to move on  ·  Esc: try again",
        }
        self.typingInput.setPlaceholderText(placeholders[phase])

    def _renderTyping(self, animate):
        """Draw the typing card for its current phase."""
        spec = self.typing["spec"]
        front = promptHtml(spec, self.typing["hint"])
        result = self.typing["result"]
        back = resultHtml(spec, result, self.typing["hint"]) if result else ""
        revealed = self.typing["phase"] != PHASE_ANSWER
        if revealed and animate:
            self.cardView.setCard(front, back, "", False, "Type", "Answer")
            self.cardView.setFlipped(True)
        else:
            self.cardView.setCard(front, back, "", revealed, "Type", "Answer")
        self.isFlipped = revealed
        self._setTypingPhase(self.typing["phase"])

    def showTypingHint(self):
        """Reveal the next hint step for the card being typed."""
        if self.typing is None or self.typing["phase"] != PHASE_ANSWER:
            return
        if self.typing["hint"] >= MAX_HINT_LEVEL:
            return
        self.typing["hint"] += 1
        self._renderTyping(animate=False)
        self.typingInput.setFocus()

    def _recordTyping(self, correct, peeked=False):
        """Save one typed result to the app's own stats file (never to Anki)."""
        card = self.session.current()
        if card is None or self.typing is None or self.typing.get("recorded"):
            return
        self.typing["recorded"] = True
        # Korean → English answers are a different skill; they stay out of the Korean
        # typing record that feeds the recommended list.
        if self.typing["spec"].mode == MODE_ENGLISH:
            return
        recordTypingResult(self.typingStats, card.noteId, self.today, correct,
                           word=self.typing["spec"].answer, peeked=peeked)
        try:
            saveTypingStats(self.typingStats)
        except (IOError, OSError):
            pass

    def _onTypingSubmitted(self, text, giveUp=False):
        """Enter in the answer box: check, move on, or check the retyped answer."""
        if self.typing is None or self._onSummary():
            return
        phase = self.typing["phase"]
        spec = self.typing["spec"]

        if phase == PHASE_ANSWER:
            if not text.strip() and not giveUp:
                return
            result = checkAnswer(text, spec)
            self.typing["result"] = result
            self.typing["peeked"] = giveUp
            self._logAttempt(result, giveUp)
            if giveUp:
                self._recordTyping(False, peeked=True)
                self.typingInput.clearAnswer()
                self.typing["phase"] = PHASE_RETYPE
            elif result.verdict == RESULT_WRONG:
                self._recordTyping(False)
                self.typingInput.clearAnswer()
                self.typing["phase"] = PHASE_RETYPE
            else:
                if result.verdict == RESULT_CORRECT and not self.typing["hint"]:
                    self._recordTyping(True)
                self.typing["phase"] = PHASE_CHECKED
            self._renderTyping(animate=True)
            return

        if phase == PHASE_CHECKED:
            result = self.typing["result"]
            if result.verdict == RESULT_ALMOST:
                self._recordTyping(False)
            right = result.verdict == RESULT_CORRECT and not self.typing["hint"]
            if self._drillStep(right):
                return
            clean = right and not self.typing.get("missed")
            self.gradeCurrent(STATUS_KNOWN if clean else STATUS_LEARNING)
            return

        if phase == PHASE_RETYPE:
            # Copying the answer is optional practice; an empty Enter just moves on.
            if not text.strip() or matchesForRetype(text, spec):
                if self._drillStep(False):
                    return
                self.gradeCurrent(STATUS_LEARNING)
            else:
                self.typingInput.clearAnswer()
                self.typingInput.setPlaceholderText(
                    "Not quite. Copy the answer above exactly, then Enter")

    def _canAcceptAsRight(self):
        """Whether "That was a typo" / "I was right" applies to the answer on screen: an
        "almost", or any real attempt at an English meaning (there are many ways to say
        it, so the user is the judge)."""
        if self.typing is None or self.typing["phase"] == PHASE_ANSWER:
            return False
        result = self.typing.get("result")
        if result is None or self.typing.get("peeked"):
            return False
        if self.typing["phase"] == PHASE_CHECKED and result.verdict == RESULT_ALMOST:
            # A wrong form of the right word is not a slip of the finger.
            return not result.formOnly
        return self.typing["spec"].mode == MODE_ENGLISH and result.verdict == RESULT_WRONG

    def _logAttempt(self, result, giveUp):
        """Add one answer to this session's typing log, for the end-of-round stats."""
        card = self.session.current()
        if card is None:
            return
        entry = self.sessionLog.setdefault(card.cardId, {
            "word": self.typing["spec"].answer if self.typing["spec"].mode != MODE_ENGLISH
            else self.typing["spec"].prompt,
            "tries": 0, "misses": 0, "peeks": 0, "firstTry": None})
        entry["tries"] += 1
        if giveUp:
            entry["peeks"] += 1
            outcome = "peek"
        elif result.verdict != RESULT_CORRECT:
            entry["misses"] += 1
            outcome = result.verdict
        else:
            outcome = "hint" if self.typing["hint"] else "correct"
        if entry["firstTry"] is None:
            entry["firstTry"] = outcome

    def _acceptAsTypo(self):
        """Count an "almost" (or a disputed English) answer as correct."""
        if not self._canAcceptAsRight():
            return
        card = self.session.current()
        entry = self.sessionLog.get(card.cardId) if card is not None else None
        if entry is not None and entry["misses"]:
            # Undo the miss this attempt logged; if it was the first try, it was right.
            entry["misses"] -= 1
            if entry["tries"] == 1:
                entry["firstTry"] = "hint" if self.typing["hint"] else "correct"
        if self._drillStep(not self.typing["hint"]):
            return
        if not self.typing["hint"] and not self.typing.get("missed"):
            self._recordTyping(True)
            self.gradeCurrent(STATUS_KNOWN)
        else:
            self.gradeCurrent(STATUS_LEARNING)

    def _showEmptyState(self):
        """Explain that the current filters select nothing to study."""
        if self.session.filterIsEmpty:
            # The round has cards; this grade just has none of them yet.
            label = QUEUE_GRADE_LABELS[self.session.statusFilter]
            self.cardView.showMessage(
                "No cards marked %s yet.\n\n"
                "Grade some cards, or set Show back to All cards." % label)
            self.cardInfoLabel.setText("")
            self.progressBar.setRange(0, 1)
            self.progressBar.setValue(0)
            known, learning, _unseen = self.session.counts()
            self.statusLabel.setText(
                "Round %d  ·  0 shown of %d  ·  ✓ %d known  ·  ✕ %d still learning"
                % (self.session.roundNumber, self.session.roundSize, known, learning))
            self._setStudyControlsEnabled(False)
            self._refreshFilterCounts()
            return

        queues = self._selectedQueues()
        if self._listMode() == LIST_RECOMMENDED:
            message = ("Nothing recommended right now. You're all caught up.\n\n"
                       "Sync Anki, then press Reload (F5) to pick up new work.")
        elif not queues:
            message = "Nothing selected.\n\nTick Due today, Learn today or New above."
        else:
            message = ("Nothing to study.\n\nNo cards match %s in this deck right now."
                       % " or ".join(sorted(QUEUE_LABELS[q].lower() for q in queues)))
        self.cardView.showMessage(message)
        self.cardInfoLabel.setText("")
        self.controlStack.setCurrentIndex(0)
        self.progressBar.setRange(0, 1)
        self.progressBar.setValue(0)
        self.statusLabel.setText("0 cards")
        self._setStudyControlsEnabled(False)

    def _setStudyControlsEnabled(self, enabled):
        """Enable or disable the study row as a unit."""
        for button in (self.prevButton, self.nextButton, self.flipButton,
                       self.learningButton, self.knownButton):
            button.setEnabled(enabled)

    def _refreshProgress(self):
        """Update the progress bar, the round readout and the prev/next enabled states."""
        known, learning, _unseen = self.session.counts()
        visible = self.session.visibleCount
        self.progressBar.setRange(0, max(1, visible))
        self.progressBar.setValue(self.session.position)

        scope = ""
        if self.session.isFiltered:
            scope = " (of %d)" % self.session.roundSize
        self.statusLabel.setText(
            "Round %d  ·  %d / %d%s  ·  ✓ %d known  ·  ✕ %d still learning"
            % (self.session.roundNumber, self.session.position, visible, scope,
               known, learning))
        self._setStudyControlsEnabled(True)
        self.prevButton.setEnabled(self.session.position > 1)
        self.nextButton.setEnabled(
            not self.session.isFiltered or not self.session.atLastCard)
        self._refreshFilterCounts()

    def _showRoundSummary(self):
        """Swap in the end-of-round message and its button row."""
        self.typing = None
        self._setTypingVisible(False)
        known, learning, unseen = self.session.counts()
        missed = learning + unseen
        size = self.session.roundSize
        if missed:
            message = ("Round %d done.\n\nYou knew %d of %d. %d still to go."
                       % (self.session.roundNumber, known, size, missed))
        else:
            message = ("Round %d done.\n\nYou knew all %d cards. Nice."
                       % (self.session.roundNumber, size))
        stats = typingSummary(self.sessionLog)
        if stats:
            message += "\n\n" + stats
        self.cardView.showMessage(message)
        self.statusLabel.setText(
            "Round %d complete  ·  ✓ %d known  ·  ✕ %d to repeat"
            % (self.session.roundNumber, known, missed))
        # The last grade of the round happens on the way here, so the Show counts would
        # otherwise be one card out of date.
        self._refreshFilterCounts()
        self.nextRoundButton.setEnabled(bool(missed))
        self.nextRoundButton.setText("Study the %d you missed" % missed if missed
                                     else "Nothing left to review")
        self.replayButton.setText("Replay all %d" % size)
        self.replayButton.setEnabled(True)
        self.controlStack.setCurrentIndex(1)
        self.cardInfoLabel.setText("")
        self.progressBar.setValue(self.progressBar.maximum())

    def _showSessionComplete(self):
        """Every card has been marked known — offer only a fresh start."""
        self.typing = None
        self._setTypingVisible(False)
        message = ("Session complete.\n\nYou marked all %d cards as known."
                   % self.session.knownTotal)
        stats = typingSummary(self.sessionLog)
        if stats:
            message += "\n\n" + stats
        self.cardView.showMessage(message)
        self.nextRoundButton.setEnabled(False)
        self.nextRoundButton.setText("Nothing left to review")
        self.replayButton.setEnabled(False)
        self.controlStack.setCurrentIndex(1)
        self.cardInfoLabel.setText("")
        self.statusLabel.setText("Session complete  ·  %d known" % self.session.knownTotal)
        self.progressBar.setValue(self.progressBar.maximum())

    # ---------------------------------------------------------------- actions

    def flipCard(self):
        """Show the other side of the current card. Flipping back and forth is unrestricted."""
        if self.session.current() is None or self._onSummary():
            return
        if self.typing is not None:
            # Before answering, Flip means "I don't know": reveal the answer (counted as a
            # miss). Once it is revealed, Flip swaps between the prompt and the answer.
            if self.typing["phase"] == PHASE_ANSWER:
                self._onTypingSubmitted("", giveUp=True)
            else:
                self.isFlipped = not self.isFlipped
                self.cardView.setFlipped(self.isFlipped)
                self.typingInput.setFocus()
            return
        self.isFlipped = not self.isFlipped
        self.cardView.setFlipped(self.isFlipped)

    def retryCard(self):
        """Practise the current card again: hide the answer and start a fresh attempt.

        A miss earlier on this card still counts, so the card comes back next round even
        if the retry is clean. Its result is only recorded to the typing stats once.
        """
        if self.session.current() is None or self._onSummary():
            return
        if self.typing is None:
            if self.isFlipped:
                self.isFlipped = False
                self.cardView.setFlipped(False)
            return
        if self.typing["phase"] == PHASE_ANSWER:
            return
        result = self.typing["result"]
        if self.typing["hint"] or result is None or result.verdict != RESULT_CORRECT:
            self.typing["missed"] = True
        self.typing.update({"hint": 0, "phase": PHASE_ANSWER, "result": None})
        self.typingInput.clearAnswer()
        self._renderTyping(animate=False)
        self.typingInput.setFocus()

    def toggleDrill(self):
        """Start or stop drilling the current typing card until it is right DRILL_TARGET
        times in a row."""
        card = self.session.current()
        if card is None or self._onSummary() or self.typing is None:
            self.drillButton.setChecked(False)
            return
        if self.drillCardId == card.cardId:
            self._stopDrill()
        else:
            self.drillCardId = card.cardId
            self.drillStreak = 0
        self.drillButton.setChecked(self.drillCardId is not None)
        self._refreshCardInfo(card)
        self.typingInput.setFocus()

    def _stopDrill(self):
        """Leave drill mode."""
        self.drillCardId = None
        self.drillStreak = 0
        if hasattr(self, "drillButton"):
            self.drillButton.setChecked(False)

    def _drillStep(self, right):
        """While drilling, count an answer and go round again. Returns True when the
        card should stay on screen; False once the drill is over (or not running)."""
        card = self.session.current()
        if card is None or self.drillCardId != card.cardId:
            return False
        self.drillStreak = self.drillStreak + 1 if right else 0
        if self.drillStreak >= DRILL_TARGET:
            # Drilled: it goes on with the normal grade (still "learning" if it was
            # ever missed, so it comes back next round).
            self._stopDrill()
            return False
        self.retryCard()
        self._refreshCardInfo(card)
        return True

    def showKeyboardHelp(self):
        """The keyboard cheat sheet."""
        box = QMessageBox(self)
        box.setWindowTitle("Keyboard shortcuts")
        box.setTextFormat(Qt.RichText)
        box.setText(KEYBOARD_HELP)
        box.setStandardButtons(QMessageBox.Close)
        box.open()
        self._helpBox = box

    def cycleAnswerForm(self):
        """Step the Answer control: dictionary → sentence → English → dictionary."""
        self.answerCombo.setCurrentIndex(
            (self.answerCombo.currentIndex() + 1) % self.answerCombo.count())

    def _onAnswerFormChanged(self, *_args):
        """Remember the new Answer setting and re-ask the current card in that form."""
        value = self.answerCombo.currentData()
        if value == self.answerForm:
            return
        self.answerForm = value
        self.settings["answerForm"] = value
        try:
            saveSettings(self.settings)
        except (IOError, OSError):
            pass
        self._specCache = {}
        self._stopDrill()
        self.typing = None
        self.isFlipped = False
        if self.session.current() is not None and not self._onSummary():
            self.showCurrentCard()

    def _onCardClicked(self):
        """A click on the card flips it. On a typing card it must not give the answer away
        before it is revealed, so it just puts the cursor back in the answer box."""
        if (self.typing is not None and not self._onSummary()
                and self.typing["phase"] == PHASE_ANSWER):
            self.typingInput.setFocus()
            return
        self.flipCard()

    def replayAudio(self):
        """Replay the audio on the side of the current card that is showing."""
        if self.session.current() is None or self._onSummary():
            return
        self.cardView.replayAudio()

    def _onSummary(self):
        """True while the end-of-round screen is showing instead of a card."""
        return self.controlStack.currentIndex() == 1

    def _returnToCards(self):
        """Leave the summary screen and show the card the cursor is still parked on."""
        self.controlStack.setCurrentIndex(0)
        self.isFlipped = False
        self.showCurrentCard()

    def goNext(self):
        """Advance to the next card, or end the round when already on the last one."""
        if self._onSummary():
            # On the summary, Next means "carry on" — start the next round if there is one.
            if self.nextRoundButton.isEnabled():
                self.startNextRound()
            return
        if self.session.current() is None:
            return
        if self.session.goNext():
            self.typing = None
            self.isFlipped = False
            self.showCurrentCard()
        elif not self.session.isFiltered:
            self._showRoundSummary()

    def goPrev(self):
        """Step back to the previous card, including back off the summary screen."""
        if self.session.current() is None:
            return
        if self._onSummary():
            # The cursor never left the last card, so stepping back just re-shows it.
            self._returnToCards()
            return
        if self.session.goPrev():
            self.typing = None
            self.isFlipped = False
            self.showCurrentCard()

    def gradeCurrent(self, status):
        """Mark the current card known or still-learning, then move on."""
        if self.session.current() is None or self._onSummary():
            return
        # The next card (or this one again, next round) starts a fresh typing attempt.
        self.typing = None
        if self.session.mark(status):
            self._persistSession()
            self.goNext()
            return
        # A filter is active and the new grade pushed this card out of view, so the next
        # card has already slid into the cursor's slot. Redraw instead of advancing.
        self._persistSession()
        self.isFlipped = False
        self.showCurrentCard()
        self._refreshFilterCounts()

    def cycleStatusFilter(self):
        """Step the Show control through All → Still learning → Got it → All."""
        nextIndex = (self.filterCombo.currentIndex() + 1) % self.filterCombo.count()
        self.filterCombo.setCurrentIndex(nextIndex)

    def resetStatusFilter(self):
        """Put the Show control back to All without re-triggering a rebuild."""
        if self.filterCombo.currentIndex() == 0:
            return
        self._loading = True
        self.filterCombo.setCurrentIndex(0)
        self._loading = False

    def _refreshFilterCounts(self):
        """Put live per-grade counts on the Show control's entries."""
        known, learning, _unseen = self.session.counts()
        self.filterCombo.setItemText(0, "All cards (%d)" % self.session.roundSize)
        self.filterCombo.setItemText(1, "✕ Still learning (%d)" % learning)
        self.filterCombo.setItemText(2, "✓ Got it (%d)" % known)

    def adjustZoom(self, multiplier):
        """Scale the note content by `multiplier`, clamped to the zoom bounds."""
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * multiplier))
        self.cardView.setZoom(self.zoom)

    def cycleMode(self):
        """Step Mode through Auto → Flip only → Type all."""
        self.modeCombo.setCurrentIndex(
            (self.modeCombo.currentIndex() + 1) % self.modeCombo.count())

    def toggleBackFirst(self):
        """Flip the leading side between front-first and back-first."""
        self.backFirstButton.setChecked(not self.showBackFirst)
        self.frontFirstButton.setChecked(self.showBackFirst)
        self._onSideToggled()

    # ---------------------------------------------------------------- slots

    def _onProfileChanged(self, *_args):
        """Reload everything when the user picks a different Anki profile."""
        if not self._loading:
            self.reloadCollection()

    def _onFilterChanged(self, *_args):
        """Rebuild the session when the deck or queue selection changes."""
        if self._loading or self.snapshot is None:
            return
        self._refreshCounts()
        self.rebuildSession()

    def _onListModeChanged(self, *_args):
        """Switch between the recommended list and hand-picked queues."""
        recommended = self._listMode() == LIST_RECOMMENDED
        self.queueBox.setVisible(not recommended)
        self.limitCombo.setVisible(recommended)
        self.recommendLabel.setVisible(recommended)
        if self._loading or self.snapshot is None:
            return
        self._refreshCounts()
        self.rebuildSession()

    def _onModeChanged(self, *_args):
        """Redraw the current card under the new Mode."""
        self.typing = None
        self.isFlipped = False
        if self.session.current() is not None and not self._onSummary():
            self.showCurrentCard()

    def _onStatusFilterChanged(self, *_args):
        """Narrow the round to the chosen grade and redraw."""
        if self._loading:
            return
        self.session.setStatusFilter(self.filterCombo.currentData())
        self.isFlipped = False
        # Leaving the summary screen is implied by choosing something to look at.
        if self._onSummary() and not self.session.filterIsEmpty:
            self.controlStack.setCurrentIndex(0)
        self.showCurrentCard()

    def _onShuffleToggled(self, state):
        """Reorder the current round in place rather than restarting it."""
        self.session.setShuffle(state == Qt.Checked)
        self.isFlipped = False
        self.showCurrentCard()

    def _onSideToggled(self):
        """Re-render the current card so the newly chosen side leads."""
        self.showBackFirst = self.backFirstButton.isChecked()
        self.isFlipped = False
        self.showCurrentCard()

    def closeEvent(self, event):
        """Delete the temporary collection copy on the way out."""
        if self.snapshot is not None:
            self._persistSession()
            self.snapshot.close()
            self.snapshot = None
        super(DrillWindow, self).closeEvent(event)
