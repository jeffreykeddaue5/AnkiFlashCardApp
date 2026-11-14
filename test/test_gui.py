"""
test_gui.py
Regression tests for the window behaviours that pure session logic cannot cover.

Each of these guards a bug that reached the user: clicking the card only ever flipping once,
the end-of-round screen trapping you with no way back to the cards, and the card's text going
unreadable against a deck's own styling.
"""

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anki_drill import persistence, scheduling  # noqa: E402
from anki_drill.collection import Profile  # noqa: E402

from test_anki_drill import buildFakeCollection  # noqa: E402

pytestmark = pytest.mark.usefixtures("qtApp")


def pump(milliseconds):
    """Run the Qt event loop for a while so the page can load and scripts can settle."""
    from PyQt5.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec_()


def evaluate(window, expression):
    """Evaluate `expression` in the card page and return its result synchronously."""
    from PyQt5.QtCore import QEventLoop, QTimer
    result = {}
    loop = QEventLoop()
    window.cardView._page.runJavaScript(  # noqa: SLF001
        expression, lambda value: (result.__setitem__("value", value), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    loop.exec_()
    return result.get("value")


def useCustomQueues(window):
    """Switch the window from the recommended list to the hand-picked queue checkboxes."""
    from anki_drill.mainwindow import LIST_CUSTOM
    window.listCombo.setCurrentIndex(window.listCombo.findData(LIST_CUSTOM))
    pump(200)


@pytest.fixture(name="window")
def windowFixture(qtApp, tmp_path, monkeypatch):
    """A DrillWindow over a purpose-built 3-card collection, with its page fully loaded."""
    crt = int(time.time()) - 100 * 86400
    today, _next = scheduling.schedTimingToday(crt, int(time.time()), 4)
    collectionPath = str(tmp_path / "collection.anki2")
    buildFakeCollection(collectionPath, crt, [
        (2, today, 1), (2, today, 1), (2, today, 1),
    ])
    profile = Profile(name="Test", root=str(tmp_path), collectionPath=collectionPath,
                      mediaPath=str(tmp_path / "collection.media"))

    monkeypatch.setattr(persistence, "APP_DIR", str(tmp_path / "app-state"))
    monkeypatch.setattr(persistence, "STATE_PATH",
                        str(tmp_path / "app-state" / "session-state.json"))

    from anki_drill import mainwindow as mainwindowModule
    monkeypatch.setattr(mainwindowModule, "findProfiles", lambda: [profile])

    window = mainwindowModule.DrillWindow()
    window.show()
    pump(2500)
    yield window
    window.close()


class TestClickToFlip(object):
    """The card must keep responding to clicks, not just the first one."""

    def test_clickingTheCardFlipsItEveryTime(self, window):
        seen = []
        window.cardView._bridge.flipClicked.connect(  # noqa: SLF001
            lambda: seen.append(1))
        states = []
        for _ in range(6):
            evaluate(window, "document.getElementById('flipper').click()")
            pump(250)
            states.append(window.isFlipped)
        assert len(seen) == 6
        assert states == [True, False, True, False, True, False]

    def test_flippedClassTracksTheFlipState(self, window):
        evaluate(window, "document.getElementById('flipper').click()")
        pump(250)
        assert evaluate(window, "document.getElementById('flipper').className") == "flipped"
        evaluate(window, "document.getElementById('flipper').click()")
        pump(250)
        assert evaluate(window, "document.getElementById('flipper').className") == ""

    def test_onlyOneFaceIsVisibleAtATime(self, window):
        def opacities():
            return (evaluate(window, "getComputedStyle(front).opacity"),
                    evaluate(window, "getComputedStyle(back).opacity"))
        assert opacities() == ("1", "0")
        window.flipCard()
        pump(600)
        assert opacities() == ("0", "1")


class TestRoundSummaryNavigation(object):
    """Reaching the end of a round must not trap the user on the summary screen."""

    def advanceToSummary(self, window):
        """Press Next until the round summary appears."""
        for _ in range(window.session.roundSize):
            window.goNext()
        pump(200)
        assert window.controlStack.currentIndex() == 1

    def test_prevReturnsFromTheSummaryToTheLastCard(self, window):
        self.advanceToSummary(window)
        lastPosition = window.session.position
        window.goPrev()
        pump(200)
        assert window.controlStack.currentIndex() == 0
        assert window.session.position == lastPosition

    def test_backToCardsButtonLeavesTheSummary(self, window):
        self.advanceToSummary(window)
        window.backToCardsButton.click()
        pump(200)
        assert window.controlStack.currentIndex() == 0

    def test_cardIsFlippableAgainAfterReturningFromTheSummary(self, window):
        self.advanceToSummary(window)
        window.goPrev()
        pump(200)
        window.flipCard()
        assert window.isFlipped is True

    def test_nextOnTheSummaryStartsTheFollowingRound(self, window):
        self.advanceToSummary(window)
        window.goNext()
        pump(200)
        assert window.session.roundNumber == 2
        assert window.controlStack.currentIndex() == 0

    def test_summaryStatusLineReflectsTheFinishedRound(self, window):
        self.advanceToSummary(window)
        assert "complete" in window.statusLabel.text().lower()
        assert "3 to repeat" in window.statusLabel.text()


class TestTheme(object):
    """The app is dark-only; card text must be readable and no deck can undo that."""

    def colours(self, window):
        """Return (textColour, cardBackground) as computed by the page."""
        return (evaluate(window, "getComputedStyle(document.getElementById("
                                 "'front-content')).color"),
                evaluate(window, "getComputedStyle(front).backgroundColor"))

    def test_cardUsesLightTextOnADarkFace(self, window):
        assert self.colours(window) == ("rgb(233, 236, 248)", "rgb(26, 31, 61)")

    def test_pageBackgroundIsDark(self, window):
        assert evaluate(
            window, "getComputedStyle(document.body).backgroundColor") == "rgb(15, 18, 37)"

    def test_colorSchemeIsDeclaredDarkSoChromiumDoesNotInvert(self, window):
        assert evaluate(
            window, "getComputedStyle(document.documentElement).colorScheme") == "dark"

    def test_thereIsNoThemeToggle(self, window):
        assert not hasattr(window, "darkCheck")

    def test_colourSurvivesMovingBetweenCards(self, window):
        for _ in range(3):
            window.goNext()
            pump(250)
            assert self.colours(window)[0] == "rgb(233, 236, 248)"

    def test_deckCssCannotOverrideTheCardTextColour(self, window):
        # A deck declaring white text must not win: the theme sheet is applied after it.
        evaluate(window, "document.getElementById('notetype-css').textContent = "
                         "'.card { color: white; background-color: white; }'")
        pump(200)
        assert self.colours(window)[0] == "rgb(233, 236, 248)"

    def test_deckCssStillControlsNonColourStyling(self, window):
        evaluate(window, "document.getElementById('notetype-css').textContent = "
                         "'.card { font-family: Courier; }'")
        pump(200)
        assert evaluate(window, "getComputedStyle(document.getElementById("
                                "'front-content')).fontFamily") == "Courier"


class TestStatusFilterControl(object):
    """The Show control, which narrows the round to one grade."""

    def gradeAll(self, window, statuses):
        """Grade the round's cards in order with the given statuses."""
        for status in statuses:
            window.gradeCurrent(status)
        pump(250)

    def test_showControlOffersAllThreeChoices(self, window):
        values = [window.filterCombo.itemData(i)
                  for i in range(window.filterCombo.count())]
        assert values == ["all", "learning", "known"]

    def test_countsAppearOnTheShowEntries(self, window):
        self.gradeAll(window, ["learning", "known", "learning"])
        window.filterCombo.setCurrentIndex(0)
        pump(250)
        assert window.filterCombo.itemText(0) == "All cards (3)"
        assert window.filterCombo.itemText(1) == "✕ Still learning (2)"
        assert window.filterCombo.itemText(2) == "✓ Got it (1)"

    def test_filteringToStillLearningShowsOnlyThoseCards(self, window):
        self.gradeAll(window, ["learning", "known", "learning"])
        window.filterCombo.setCurrentIndex(1)
        pump(300)
        assert window.session.visibleCount == 2
        assert window.session.roundSize == 3
        assert window.controlStack.currentIndex() == 0

    def test_filteredProgressLineNamesTheWiderRound(self, window):
        self.gradeAll(window, ["learning", "known", "learning"])
        window.filterCombo.setCurrentIndex(1)
        pump(300)
        assert "1 / 2 (of 3)" in window.statusLabel.text()

    def test_gradingOutOfTheFilterAdvancesWithoutSkipping(self, window):
        self.gradeAll(window, ["learning", "learning", "learning"])
        window.filterCombo.setCurrentIndex(1)
        pump(300)
        first = window.session.current().cardId
        window.gradeCurrent("known")
        pump(300)
        assert window.session.visibleCount == 2
        assert window.session.current().cardId != first

    def test_emptyFilterExplainsItselfWithoutEmptyingTheRound(self, window):
        window.filterCombo.setCurrentIndex(2)
        pump(300)
        message = evaluate(window, "document.getElementById('empty').textContent") or ""
        assert "No cards marked Got it yet" in message
        assert window.session.roundSize == 3
        assert window.nextButton.isEnabled() is False

    def test_fKeyCyclesTheFilter(self, window):
        start = window.filterCombo.currentIndex()
        window.cycleStatusFilter()
        assert window.filterCombo.currentIndex() == (start + 1) % 3
        window.cycleStatusFilter()
        window.cycleStatusFilter()
        assert window.filterCombo.currentIndex() == start

    def test_filterResetsWhenTheNextRoundStarts(self, window):
        self.gradeAll(window, ["learning", "learning", "learning"])
        window.filterCombo.setCurrentIndex(1)
        pump(300)
        window.startNextRound()
        pump(300)
        assert window.filterCombo.currentIndex() == 0
        assert window.session.statusFilter == "all"
        assert window.session.visibleCount == 3

    def test_filterResetsOnRestart(self, window):
        self.gradeAll(window, ["known", "known", "known"])
        window.filterCombo.setCurrentIndex(2)
        pump(300)
        window.restartSession()
        pump(300)
        assert window.filterCombo.currentIndex() == 0
        assert window.session.visibleCount == 3

    def test_choosingAFilterLeavesTheSummaryScreen(self, window):
        self.gradeAll(window, ["learning", "learning", "learning"])
        pump(250)
        assert window.controlStack.currentIndex() == 1
        window.filterCombo.setCurrentIndex(1)
        pump(300)
        assert window.controlStack.currentIndex() == 0
        assert window.session.current() is not None


@pytest.fixture(name="pinnedWindow")
def pinnedWindowFixture(qtApp, tmp_path, monkeypatch):
    """Build a window over a collection with pins, with the add-on folder controllable.

    Yields (makeWindow, profileRoot): call makeWindow() after arranging the add-on folder.
    """
    crt = int(time.time()) - 100 * 86400
    today, _next = scheduling.schedTimingToday(crt, int(time.time()), 4)
    base = tmp_path / "profile"
    base.mkdir()
    collectionPath = str(base / "collection.anki2")
    buildFakeCollection(collectionPath, crt, [
        (2, today, 1), (2, today + 5, 1),
    ], noteTags=["", "review_pinned %s" % scheduling.pinDayTag(today)])
    profile = Profile(name="Test", root=str(base), collectionPath=collectionPath,
                      mediaPath=str(base / "collection.media"))

    from anki_drill import mainwindow as mainwindowModule
    monkeypatch.setattr(mainwindowModule, "findProfiles", lambda: [profile])

    built = []

    def makeWindow():
        """Construct and show the window, loading the collection."""
        window = mainwindowModule.DrillWindow()
        window.show()
        pump(2500)
        built.append(window)
        return window

    yield makeWindow, base
    for window in built:
        window.close()


def writeAddon(profileRoot, meta=None):
    """Create the pin add-on folder under `profileRoot`, optionally with a meta.json."""
    addonDir = os.path.join(str(profileRoot), "addons21", "todays_review_words")
    os.makedirs(addonDir)
    if meta is not None:
        with open(os.path.join(addonDir, "meta.json"), "w") as handle:
            json.dump(meta, handle)


class TestPersistentGrades(object):
    """Local grades survive closing and reopening the app."""

    def test_learningAndKnownComeBackAfterReopen(self, qtApp, tmp_path, monkeypatch):
        crt = int(time.time()) - 100 * 86400
        today, _next = scheduling.schedTimingToday(crt, int(time.time()), 4)
        collectionPath = str(tmp_path / "collection.anki2")
        buildFakeCollection(collectionPath, crt, [
            (2, today, 1), (2, today, 1), (2, today, 1),
        ])
        profile = Profile(name="Persist", root=str(tmp_path), collectionPath=collectionPath,
                          mediaPath=str(tmp_path / "collection.media"))

        monkeypatch.setattr(persistence, "APP_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(persistence, "STATE_PATH",
                            str(tmp_path / "state" / "session-state.json"))
        from anki_drill import mainwindow as mainwindowModule
        monkeypatch.setattr(mainwindowModule, "findProfiles", lambda: [profile])

        first = mainwindowModule.DrillWindow()
        first.show(); pump(2500)
        first.gradeCurrent("learning")
        first.gradeCurrent("known")
        pump(300)
        first.close()

        second = mainwindowModule.DrillWindow()
        second.show(); pump(2500)
        assert second.session.counts() == (1, 1, 1)
        assert second.filterCombo.itemText(1) == "✕ Still learning (1)"
        assert second.filterCombo.itemText(2) == "✓ Got it (1)"
        second.close()

    def test_restartSessionClearsTheSavedGrades(self, window):
        window.gradeCurrent("learning")
        window.gradeCurrent("known")
        pump(300)
        window.restartSession()
        pump(300)
        window.close()

        from anki_drill import mainwindow as mainwindowModule
        reopened = mainwindowModule.DrillWindow()
        reopened.show(); pump(2500)
        assert reopened.session.counts() == (0, 0, 3)
        reopened.close()


class TestPinnedCheckbox(object):
    """The Pinned checkbox only appears when the add-on is in play."""

    def test_pinnedIsOfferedWhenTheAddonIsInstalled(self, pinnedWindow):
        makeWindow, root = pinnedWindow
        writeAddon(root)
        window = makeWindow()
        assert window.queueChecks["pinned"].isHidden() is False
        assert "pinned" in window._selectedQueues()  # noqa: SLF001

    def test_pinnedIsHiddenWhenTheAddonIsDisabled(self, pinnedWindow):
        makeWindow, root = pinnedWindow
        writeAddon(root, meta={"disabled": True})
        window = makeWindow()
        assert window.queueChecks["pinned"].isHidden() is True
        # A hidden queue must not quietly keep contributing cards.
        assert "pinned" not in window._selectedQueues()  # noqa: SLF001
        assert window.queueChecks["pinned"].isChecked() is False

    def test_pinnedSurvivesWithoutTheAddonWhenTagsExist(self, pinnedWindow):
        makeWindow, _root = pinnedWindow
        window = makeWindow()
        assert window.queueChecks["pinned"].isHidden() is False

    def test_pinnedCountAppearsOnTheLabel(self, pinnedWindow):
        makeWindow, root = pinnedWindow
        writeAddon(root)
        window = makeWindow()
        assert window.queueChecks["pinned"].text() == "Pinned (1)"

    def test_pinnedCardIsPulledIntoTheSession(self, pinnedWindow):
        makeWindow, root = pinnedWindow
        writeAddon(root)
        window = makeWindow()
        # One card is due, a different one is pinned but not due: both should be studied.
        assert window.session.roundSize == 2

    def test_unpinnedOnlySessionDropsThePinnedCard(self, pinnedWindow):
        makeWindow, root = pinnedWindow
        writeAddon(root)
        window = makeWindow()
        useCustomQueues(window)
        window.queueChecks["pinned"].setChecked(False)
        pump(400)
        assert window.session.roundSize == 1


class TestFilterPlacementMemory(object):
    """Each Show mode remembers where the user last left that subset."""

    def test_stillLearningAndGotItRememberSeparatePlaces(self, window):
        window.gradeCurrent("learning")
        window.gradeCurrent("learning")
        window.gradeCurrent("known")
        pump(300)

        window.filterCombo.setCurrentIndex(1)
        pump(300)
        firstLearning = window.session.current().cardId
        window.goNext(); pump(200)
        secondLearning = window.session.current().cardId
        assert secondLearning != firstLearning

        window.filterCombo.setCurrentIndex(2)
        pump(300)
        knownCard = window.session.current().cardId

        window.filterCombo.setCurrentIndex(1)
        pump(300)
        assert window.session.current().cardId == secondLearning
        window.filterCombo.setCurrentIndex(2)
        pump(300)
        assert window.session.current().cardId == knownCard

    def test_allCardsRemembersItsOwnPlaceToo(self, window):
        window.goNext(); window.goNext(); pump(200)
        current = window.session.current().cardId
        window.gradeCurrent("known")
        pump(200)
        window.filterCombo.setCurrentIndex(2)
        pump(300)
        window.filterCombo.setCurrentIndex(0)
        pump(300)
        assert window.session.current().cardId == current

    def test_filterPlacesSurviveReopen(self, qtApp, tmp_path, monkeypatch):
        crt = int(time.time()) - 100 * 86400
        today, _next = scheduling.schedTimingToday(crt, int(time.time()), 4)
        collectionPath = str(tmp_path / "collection.anki2")
        buildFakeCollection(collectionPath, crt, [
            (2, today, 1), (2, today, 1), (2, today, 1), (2, today, 1),
        ])
        profile = Profile(name="Persist", root=str(tmp_path), collectionPath=collectionPath,
                          mediaPath=str(tmp_path / "collection.media"))
        monkeypatch.setattr(persistence, "APP_DIR", str(tmp_path / "state"))
        monkeypatch.setattr(persistence, "STATE_PATH",
                            str(tmp_path / "state" / "session-state.json"))
        from anki_drill import mainwindow as mainwindowModule
        monkeypatch.setattr(mainwindowModule, "findProfiles", lambda: [profile])

        first = mainwindowModule.DrillWindow()
        first.show(); pump(2500)
        first.gradeCurrent("learning")
        first.gradeCurrent("learning")
        first.gradeCurrent("known")
        pump(300)
        first.filterCombo.setCurrentIndex(1)
        pump(300)
        first.goNext(); pump(200)
        remembered = first.session.current().cardId
        first.close()

        second = mainwindowModule.DrillWindow()
        second.show(); pump(2500)
        second.filterCombo.setCurrentIndex(1)
        pump(300)
        assert second.session.current().cardId == remembered
        second.close()


class TestQueueSelection(object):
    """The header exposes exactly the two queues the app studies."""

    def test_theThreeDailyQueuesAreAlwaysShown(self, window):
        shown = sorted(name for name, check in window.queueChecks.items()
                       if not check.isHidden())
        assert shown == ["due", "new", "reviewed"]

    def test_pinnedIsHiddenWithoutTheAddonOrAnyPinTags(self, window):
        assert window.queueChecks["pinned"].isHidden() is True

    def test_theShownQueuesStartChecked(self, window):
        assert all(check.isChecked() for name, check in window.queueChecks.items()
                   if not check.isHidden())

    def test_uncheckingEverythingShowsTheEmptyState(self, window):
        useCustomQueues(window)
        for check in window.queueChecks.values():
            check.setChecked(False)
        pump(400)
        assert window.session.roundSize == 0
        assert "Nothing selected" in (evaluate(
            window, "document.getElementById('empty').textContent") or "")


# ---------------------------------------------------------------- typing mode

KOREAN_NOTES = [
    ("개운하다", "refreshed", "푹 자고 일어나서 몸이 개운해요.", "I feel refreshed."),
    ("들뜨다", "excited", "여행 갈 생각에 마음이 들떠요.", "I'm excited about the trip."),
]


@pytest.fixture(name="koreanWindow")
def koreanWindowFixture(qtApp, tmp_path, monkeypatch):
    """A window over two due Korean Basic notes (Front/Back/Example/Translation)."""
    import sqlite3
    crt = int(time.time()) - 100 * 86400
    today, _next = scheduling.schedTimingToday(crt, int(time.time()), 4)
    collectionPath = str(tmp_path / "collection.anki2")
    buildFakeCollection(collectionPath, crt, [(2, today, 1), (2, today, 1)])
    conn = sqlite3.connect(collectionPath)
    conn.executemany("INSERT INTO fields VALUES (99, ?, ?, x'')",
                     [(2, "Example"), (3, "Translation")])
    for noteId, fields in enumerate(KOREAN_NOTES, 1):
        conn.execute("UPDATE notes SET flds = ? WHERE id = ?", ("\x1f".join(fields), noteId))
    conn.commit()
    conn.close()
    profile = Profile(name="Test", root=str(tmp_path), collectionPath=collectionPath,
                      mediaPath=str(tmp_path / "collection.media"))

    from anki_drill import mainwindow as mainwindowModule
    monkeypatch.setattr(mainwindowModule, "findProfiles", lambda: [profile])
    window = mainwindowModule.DrillWindow()
    window.show()
    pump(2500)
    yield window
    window.close()


def typeAll(window):
    """Switch Mode to Type all and let the card redraw."""
    from anki_drill.mainwindow import MODE_TYPE
    window.modeCombo.setCurrentIndex(window.modeCombo.findData(MODE_TYPE))
    pump(300)


def submit(window, text):
    """Type `text` into the answer box and press Enter."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    window.typingInput.setText(text)
    QTest.keyClick(window.typingInput, Qt.Key_Return)
    pump(300)


def pageText(window):
    return evaluate(window, "document.body.innerText") or ""


class TestRecommendedListWindow(object):

    def test_recommendedIsTheDefaultList(self, koreanWindow):
        from anki_drill.mainwindow import LIST_RECOMMENDED
        assert koreanWindow.listCombo.currentData() == LIST_RECOMMENDED
        assert koreanWindow.queueBox.isHidden()
        assert koreanWindow.session.roundSize == 2

    def test_badgeSaysWhyTheCardIsThere(self, koreanWindow):
        assert "↻ Due" in koreanWindow.cardInfoLabel.text()

    def test_summaryCountsTheList(self, koreanWindow):
        assert "↻ 2" in koreanWindow.recommendLabel.text()


class TestTypingMode(object):

    def test_autoModeFlipsADueCardInRoundOne(self, koreanWindow):
        assert koreanWindow.typingRow.isHidden()

    def test_typeAllShowsTheGapSentence(self, koreanWindow):
        typeAll(koreanWindow)
        assert not koreanWindow.typingRow.isHidden()
        text = pageText(koreanWindow)
        assert "푹 자고 일어나서 몸이" in text and "개운해요" not in text

    def test_correctAnswerThenEnterRetiresTheCard(self, koreanWindow):
        from anki_drill.session import STATUS_KNOWN
        typeAll(koreanWindow)
        first = koreanWindow.session.current()
        submit(koreanWindow, "개운해요")
        assert koreanWindow.typing["phase"] == "checked"
        assert "Correct" in pageText(koreanWindow)
        submit(koreanWindow, "")
        assert koreanWindow.session.statusOf(first) == STATUS_KNOWN
        assert koreanWindow.session.current().cardId != first.cardId

    def test_wrongAnswerMustBeRetypedAndIsRecorded(self, koreanWindow):
        from anki_drill import persistence as persistenceModule
        from anki_drill.session import STATUS_LEARNING
        typeAll(koreanWindow)
        first = koreanWindow.session.current()
        submit(koreanWindow, "좋아요")
        assert koreanWindow.typing["phase"] == "retype"
        submit(koreanWindow, "개운헤요")      # still not exact: stays put
        assert koreanWindow.session.current().cardId == first.cardId
        submit(koreanWindow, "개운해요")
        assert koreanWindow.session.statusOf(first) == STATUS_LEARNING
        stats = persistenceModule.loadTypingStats()
        assert stats[str(first.noteId)]["missDays"] == [koreanWindow.today]

    def test_almostCanBeAcceptedAsATypo(self, koreanWindow):
        from anki_drill.session import STATUS_KNOWN
        typeAll(koreanWindow)
        first = koreanWindow.session.current()
        submit(koreanWindow, "개운헤요")
        assert koreanWindow.typing["result"].verdict == "almost"
        assert not koreanWindow.typoButton.isHidden()
        koreanWindow.typoButton.click()
        pump(200)
        assert koreanWindow.session.statusOf(first) == STATUS_KNOWN

    def test_hintsClimbTheLadder(self, koreanWindow):
        typeAll(koreanWindow)
        koreanWindow.showTypingHint()
        koreanWindow.showTypingHint()
        pump(300)
        assert "ㄱㅇㅎㅇ" in pageText(koreanWindow)

    def test_correctWithAHintComesBack(self, koreanWindow):
        from anki_drill.session import STATUS_LEARNING
        typeAll(koreanWindow)
        first = koreanWindow.session.current()
        koreanWindow.showTypingHint()
        submit(koreanWindow, "개운해요")
        submit(koreanWindow, "")
        assert koreanWindow.session.statusOf(first) == STATUS_LEARNING

    def test_digitKeysTypeInsteadOfGrading(self, koreanWindow):
        from PyQt5.QtTest import QTest
        typeAll(koreanWindow)
        first = koreanWindow.session.current()
        koreanWindow.typingInput.setFocus()
        QTest.keyClicks(koreanWindow.typingInput, "12")
        pump(100)
        assert koreanWindow.typingInput.text() == "12"
        assert koreanWindow.session.current().cardId == first.cardId

    def test_ctrlHShowsAHintWhileTyping(self, koreanWindow):
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
        typeAll(koreanWindow)
        koreanWindow.typingInput.setFocus()
        QTest.keyClick(koreanWindow.typingInput, Qt.Key_H, Qt.ControlModifier)
        pump(200)
        assert koreanWindow.typing["hint"] == 1

    def test_unfinishedImeSyllableIsIncluded(self, koreanWindow):
        from PyQt5.QtCore import Qt
        from PyQt5.QtGui import QInputMethodEvent
        from PyQt5.QtTest import QTest
        from PyQt5.QtWidgets import QApplication
        typeAll(koreanWindow)
        box = koreanWindow.typingInput
        box.setText("개운해")
        QApplication.sendEvent(box, QInputMethodEvent("요", []))
        QTest.keyClick(box, Qt.Key_Return)
        pump(300)
        assert koreanWindow.typing["result"].verdict == "correct"

    def test_showAnswerButtonCountsAsNotKnowing(self, koreanWindow):
        typeAll(koreanWindow)
        koreanWindow.flipButton.click()
        pump(300)
        assert koreanWindow.typing["phase"] == "retype"

    def test_roundTwoIsTypedInAutoMode(self, koreanWindow):
        from anki_drill.session import STATUS_LEARNING
        koreanWindow.gradeCurrent(STATUS_LEARNING)
        koreanWindow.gradeCurrent(STATUS_LEARNING)
        pump(200)
        koreanWindow.startNextRound()
        pump(300)
        assert not koreanWindow.typingRow.isHidden()
