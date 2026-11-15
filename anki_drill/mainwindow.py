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
    applySessionState, clearSessionState, loadSessionState, loadTypingStats,
    recordTypingResult, saveSessionState, saveTypingStats, serialiseSession)
from .collection import (
    CollectionSnapshot, candidateRoots, findProfiles, pinningAvailable,
    profileForCollection)
from .production import (
    MAX_HINT_LEVEL, RESULT_ALMOST, RESULT_CORRECT, RESULT_WRONG, buildTypingSpec,
    checkAnswer, matchesForRetype, promptHtml, resultHtml)
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
PHASE_RETYPE = "retype"     # wrong: copy the answer once to move on

# Single-key shortcuts that would steal keystrokes from the answer box.
TYPING_CONFLICTS = frozenset(
    ["Space", "Right", "Return", "Enter", "Left", "1", "2", "B", "S", "F", "R", "T"])

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

        for button in (self.hintButton, self.typoButton):
            button.setFocusPolicy(Qt.NoFocus)
        self.typoButton.setVisible(False)
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
            spec = buildTypingSpec(dict(zip(notetype.fields, values)))
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
        self.flipButton.setText("Show answer" if visible else "Flip")

    def _setTypingPhase(self, phase):
        """Move the typing card to `phase` and restyle the box to match."""
        self.typing["phase"] = phase
        self.typingInput.setReadOnly(phase == PHASE_CHECKED)
        self.typingInput.setProperty("phase", phase)
        self.typingInput.style().unpolish(self.typingInput)
        self.typingInput.style().polish(self.typingInput)
        result = self.typing.get("result")
        self.typoButton.setVisible(
            phase == PHASE_CHECKED and result is not None and result.verdict == RESULT_ALMOST)
        self.hintButton.setEnabled(
            phase == PHASE_ANSWER and self.typing["hint"] < MAX_HINT_LEVEL)
        placeholders = {
            PHASE_ANSWER: "Type the Korean, then Enter",
            PHASE_CHECKED: "Enter: next card",
            PHASE_RETYPE: "Type the answer shown above, then Enter",
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

    def _recordTyping(self, correct):
        """Save one typed result to the app's own stats file (never to Anki)."""
        card = self.session.current()
        if card is None or self.typing is None or self.typing.get("recorded"):
            return
        self.typing["recorded"] = True
        recordTypingResult(self.typingStats, card.noteId, self.today, correct,
                           word=self.typing["spec"].answer)
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
            if result.verdict == RESULT_WRONG:
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
            clean = result.verdict == RESULT_CORRECT and not self.typing["hint"]
            self.gradeCurrent(STATUS_KNOWN if clean else STATUS_LEARNING)
            return

        if phase == PHASE_RETYPE:
            if matchesForRetype(text, spec):
                self.gradeCurrent(STATUS_LEARNING)
            else:
                self.typingInput.clearAnswer()
                self.typingInput.setPlaceholderText(
                    "Not quite. Copy the answer above exactly, then Enter")

    def _acceptAsTypo(self):
        """Count an "almost" answer as correct: it was a slip of the finger."""
        if self.typing is None or self.typing["phase"] != PHASE_CHECKED:
            return
        result = self.typing["result"]
        if result is None or result.verdict != RESULT_ALMOST:
            return
        if not self.typing["hint"]:
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
        self.cardView.showMessage(
            "Session complete.\n\nYou marked all %d cards as known."
            % self.session.knownTotal)
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
            # On a typing card the Flip button means "I don't know": show the answer and
            # ask for it to be copied once, like a wrong answer.
            if self.typing["phase"] == PHASE_ANSWER:
                self._onTypingSubmitted("", giveUp=True)
            return
        self.isFlipped = not self.isFlipped
        self.cardView.setFlipped(self.isFlipped)

    def _onCardClicked(self):
        """A click on the card flips it, except on a typing card, where it must not give
        the answer away: it just puts the cursor back in the answer box."""
        if self.typing is not None and not self._onSummary():
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
