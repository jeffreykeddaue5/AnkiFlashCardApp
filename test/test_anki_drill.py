"""
test_anki_drill.py
Tests for the non-UI logic: day arithmetic, queue selection, template rendering, session flow.

These run without Qt and without a real Anki install; the collection tests build a miniature
schema-18 database in a temp directory.
"""

import json
import os
import sqlite3
import sys
import time
from collections import namedtuple
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anki_drill import persistence, rendering, scheduling  # noqa: E402
from anki_drill.collection import (  # noqa: E402
    CollectionSnapshot, Notetype, Profile, Template, _unicaseCollation)
from anki_drill.session import (  # noqa: E402
    FILTER_ALL, STATUS_KNOWN, STATUS_LEARNING, StudySession)

FakeRow = namedtuple("FakeRow", "cardId noteId deckId deckName notetypeId ord "
                                "queue type due ivl tags fields")


def makeRow(fields, ord_=0, cardId=1, queue=2, due=0, ivl=10, tags=""):
    """Build a CardRow-shaped stand-in for rendering and session tests."""
    return FakeRow(cardId=cardId, noteId=cardId, deckId=1, deckName="Korean",
                   notetypeId=99, ord=ord_, queue=queue, type=2, due=due, ivl=ivl,
                   tags=tags, fields=list(fields))


def makeNotetype(qfmt, afmt, fields, isCloze=False, css=".card { color: red; }"):
    """Build a Notetype with a single template."""
    return Notetype(id=99, name="Test", css=css, isCloze=isCloze, fields=list(fields),
                    templates=[Template(ord=0, name="Card 1", qfmt=qfmt, afmt=afmt)])


class TestSchedTimingToday(object):
    """Day-number arithmetic against Anki's rollover rule."""

    def test_dayCountsFromCreationWhenRolloverHasPassed(self):
        crt = int(datetime(2025, 1, 29, 4, 0).timestamp())
        now = int(datetime(2025, 2, 3, 11, 30).timestamp())
        today, _nextDay = scheduling.schedTimingToday(crt, now, rolloverHour=4)
        assert today == 5

    def test_currentDayDoesNotStartUntilRollover(self):
        crt = int(datetime(2025, 1, 29, 4, 0).timestamp())
        beforeRollover = int(datetime(2025, 2, 3, 2, 30).timestamp())
        today, nextDay = scheduling.schedTimingToday(crt, beforeRollover, rolloverHour=4)
        assert today == 4
        assert datetime.fromtimestamp(nextDay) == datetime(2025, 2, 3, 4, 0)

    def test_nextDayIsTomorrowsRolloverOnceTodaysHasPassed(self):
        crt = int(datetime(2025, 1, 29, 4, 0).timestamp())
        now = int(datetime(2025, 2, 3, 11, 30).timestamp())
        _today, nextDay = scheduling.schedTimingToday(crt, now, rolloverHour=4)
        assert datetime.fromtimestamp(nextDay) == datetime(2025, 2, 4, 4, 0)

    def test_dayNeverGoesNegativeOnCreationDay(self):
        crt = int(datetime(2025, 1, 29, 4, 0).timestamp())
        sameMorning = int(datetime(2025, 1, 29, 1, 0).timestamp())
        today, _nextDay = scheduling.schedTimingToday(crt, sameMorning, rolloverHour=4)
        assert today == 0


class TestQueueClause(object):
    """SQL fragment construction for the queue checkboxes."""

    def test_noQueueSelectedYieldsNoClause(self):
        clause, params = scheduling.queueClause(set(), today=10)
        assert clause is None
        assert params == []

    def test_dueSelectsReviewQueueUpToToday(self):
        clause, params = scheduling.queueClause({"due"}, today=10)
        assert clause == "(c.queue = ? AND c.due <= ?)"
        assert params == [scheduling.QUEUE_REVIEW, 10]

    def test_learningQueuesAreNotSelectable(self):
        assert "learn" not in scheduling.QUEUE_NAMES
        clause, params = scheduling.queueClause({"learn"}, today=10)
        assert clause is None
        assert params == []

    def test_multipleQueuesAreOrTogetheredInDisplayOrder(self):
        clause, params = scheduling.queueClause({"new", "due"}, today=10)
        assert clause == "(c.queue = ? AND c.due <= ?) OR (c.queue = ?)"
        assert params == [scheduling.QUEUE_REVIEW, 10, scheduling.QUEUE_NEW]


class TestProtoParsing(object):
    """The miniature protobuf scanner used for schema-18 config blobs."""

    def test_templateConfigExtractsQuestionAndAnswerFormats(self):
        blob = b"\n\x09{{Front}}\x12\x08{{Back}}"
        assert rendering.templateConfig(blob) == ("{{Front}}", "{{Back}}")

    def test_notetypeConfigExtractsCssAndNormalKind(self):
        css, isCloze = rendering.notetypeConfig(b"\x1a\x0b.card {a:b}")
        assert css == ".card {a:b}"
        assert isCloze is False

    def test_notetypeConfigDetectsClozeKind(self):
        _css, isCloze = rendering.notetypeConfig(b"\x08\x01\x1a\x03abc")
        assert isCloze is True

    def test_realWorldBlobFromSchema18Parses(self):
        # Captured from a live collection: field 1 = qfmt, 2 = afmt, 6/7 = browser font.
        blob = (b'\n\x0b{{Korean}}x\x12\x1a{{FrontSide}}<hr id=answer>'
                b'2\x06Batang8\x0f')
        qfmt, afmt = rendering.templateConfig(blob)
        assert qfmt == "{{Korean}}x"
        assert afmt.startswith("{{FrontSide}}")

    def test_truncatedBlobKeepsFieldsAlreadyRead(self):
        assert rendering.templateConfig(b"\n\x04abcd\x12\xff") == ("abcd", "")

    def test_emptyBlobIsHandled(self):
        assert rendering.parseProtoBlob(b"") == {}
        assert rendering.templateConfig(None) == ("", "")


class TestRenderCard(object):
    """Template rendering into the two card faces."""

    def test_basicNoteRendersFrontAndStrippedBack(self):
        notetype = makeNotetype("{{Front}}", "{{FrontSide}}<hr id=answer>{{Back}}",
                                ["Front", "Back"])
        term, definition = rendering.renderCard(makeRow(["가볍다", "to be light"]), notetype)
        assert term == "가볍다"
        # The back must not repeat the front, or a back-first session would give it away.
        assert definition == "to be light"
        assert "가볍다" not in definition

    def test_answerWithoutDividerIsUsedWhole(self):
        notetype = makeNotetype("{{Front}}", "{{Back}} only", ["Front", "Back"])
        _term, definition = rendering.renderCard(makeRow(["a", "b"]), notetype)
        assert definition == "b only"

    def test_conditionalSectionIsDroppedWhenFieldIsEmpty(self):
        notetype = makeNotetype(
            "{{Front}}", "{{Back}}{{#Example}}<br>{{Example}}{{/Example}}",
            ["Front", "Back", "Example"])
        _t, withExample = rendering.renderCard(makeRow(["a", "b", "ex"]), notetype)
        _t2, without = rendering.renderCard(makeRow(["a", "b", ""]), notetype)
        assert "ex" in withExample
        assert without == "b"

    def test_invertedConditionalShowsWhenFieldIsEmpty(self):
        notetype = makeNotetype("{{Front}}", "{{^Back}}no back{{/Back}}{{Back}}",
                                ["Front", "Back"])
        _t, definition = rendering.renderCard(makeRow(["a", ""]), notetype)
        assert "no back" in definition

    def test_textFilterStripsMarkupFromTheField(self):
        notetype = makeNotetype("{{Front}}", "{{text:Back}}", ["Front", "Back"])
        _t, definition = rendering.renderCard(makeRow(["a", "<b>bold</b>"]), notetype)
        assert definition == "bold"

    def test_soundReferencesBecomeAGlyphRatherThanRawText(self):
        notetype = makeNotetype("{{Front}}{{Audio}}", "{{Back}}",
                                ["Front", "Back", "Audio"])
        term, _d = rendering.renderCard(makeRow(["a", "b", "[sound:x.mp3]"]), notetype)
        assert "[sound:" not in term
        assert "av-tag" in term

    def test_soundGlyphCarriesTheFileToPlay(self):
        notetype = makeNotetype("{{Front}}", "{{Back}}", ["Front", "Back"])
        term, _d = rendering.renderCard(makeRow(['미루다 [sound:a "b".mp3]', "b"]), notetype)
        assert 'data-sound="a &quot;b&quot;.mp3"' in term

    def test_ankiTtsTagIsAGlyphWithNothingToPlay(self):
        assert "data-sound" not in rendering.replaceAvTags("[anki:play:q:0]")

    def test_unknownFieldIsFlaggedInsteadOfCrashing(self):
        notetype = makeNotetype("{{Nope}}", "{{Back}}", ["Front", "Back"])
        term, _d = rendering.renderCard(makeRow(["a", "b"]), notetype)
        assert "tmpl-error" in term

    def test_shortNoteIsPaddedToTheNotetypeFieldCount(self):
        notetype = makeNotetype("{{Front}}", "{{Back}}-{{Extra}}",
                                ["Front", "Back", "Extra"])
        _t, definition = rendering.renderCard(makeRow(["a", "b"]), notetype)
        assert definition == "b-"

    def test_emptyFaceFallsBackToAPlaceholder(self):
        notetype = makeNotetype("{{Front}}", "{{Back}}", ["Front", "Back"])
        term, definition = rendering.renderCard(makeRow(["", ""]), notetype)
        assert "empty front" in term
        assert "empty back" in definition

    def test_missingTemplateFallsBackToFieldDump(self):
        notetype = Notetype(id=99, name="T", css="", isCloze=False,
                            fields=["Front", "Back"], templates=[])
        term, definition = rendering.renderCard(makeRow(["a", "b"]), notetype)
        assert term == "a"
        assert "b" in definition


class TestClozeRendering(object):
    """Cloze deletions on the question and answer sides."""

    def test_targetDeletionIsBlankedOnTheQuestionSide(self):
        notetype = makeNotetype("{{cloze:Text}}", "{{cloze:Text}}", ["Text"],
                                isCloze=True)
        row = makeRow(["The {{c1::cat}} sat on the {{c2::mat}}"], ord_=0)
        term, definition = rendering.renderCard(row, notetype)
        assert "cloze-blank" in term
        assert "cat" not in term
        # Deletions belonging to other cards stay visible on both sides.
        assert "mat" in term
        assert "cat" in definition

    def test_hintIsShownInPlaceOfTheEllipsis(self):
        notetype = makeNotetype("{{cloze:Text}}", "{{cloze:Text}}", ["Text"], isCloze=True)
        term, _d = rendering.renderCard(makeRow(["a {{c1::cat::animal}} b"]), notetype)
        assert "[animal]" in term

    def test_secondOrdinalBlanksItsOwnDeletion(self):
        notetype = makeNotetype("{{cloze:Text}}", "{{cloze:Text}}", ["Text"], isCloze=True)
        row = makeRow(["The {{c1::cat}} sat on the {{c2::mat}}"], ord_=1)
        term, _d = rendering.renderCard(row, notetype)
        assert "cat" in term
        assert "mat" not in term


class TestStudySession(object):
    """Round flow, grading and navigation."""

    def cards(self, count=3):
        """Return `count` distinct rows."""
        return [makeRow(["f%d" % i, "b%d" % i], cardId=i) for i in range(count)]

    def test_emptyCardListReportsEmpty(self):
        session = StudySession([])
        assert session.isEmpty
        assert session.current() is None

    def test_navigationStopsAtBothEnds(self):
        session = StudySession(self.cards(3))
        assert session.goPrev() is False
        assert session.goNext() and session.goNext()
        assert session.atLastCard
        assert session.goNext() is False

    def test_positionTracksTheCursor(self):
        session = StudySession(self.cards(3))
        assert session.position == 1
        session.goNext()
        assert session.position == 2

    def test_nextRoundKeepsOnlyCardsNotMarkedKnown(self):
        session = StudySession(self.cards(3))
        session.mark(STATUS_KNOWN)
        session.goNext()
        session.mark(STATUS_LEARNING)
        session.goNext()
        assert session.startNextRound() is True
        assert session.roundNumber == 2
        # The known card is gone; the learning one and the ungraded one remain.
        assert session.roundSize == 2
        assert {c.cardId for c in session.unknownCards()} == {1, 2}

    def test_nextRoundResetsStatusesForTheNewRound(self):
        session = StudySession(self.cards(2))
        session.mark(STATUS_LEARNING)
        session.startNextRound()
        known, learning, unseen = session.counts()
        assert (known, learning, unseen) == (0, 0, 2)

    def test_sessionEndsWhenEverythingIsKnown(self):
        session = StudySession(self.cards(2))
        session.mark(STATUS_KNOWN)
        session.goNext()
        session.mark(STATUS_KNOWN)
        assert session.startNextRound() is False
        assert session.knownTotal == 2
        assert session.current() is None

    def test_replayRoundKeepsEveryCardAndClearsGrades(self):
        session = StudySession(self.cards(3))
        session.mark(STATUS_KNOWN)
        session.goNext()
        session.replayRound()
        assert session.roundSize == 3
        assert session.position == 1
        assert session.counts() == (0, 0, 3)

    def test_resetReturnsToRoundOne(self):
        session = StudySession(self.cards(2))
        session.mark(STATUS_KNOWN)
        session.startNextRound()
        session.reset()
        assert session.roundNumber == 1
        assert session.roundSize == 2
        assert session.knownTotal == 0

    def test_shuffleKeepsTheCurrentCardUnderTheCursor(self):
        session = StudySession(self.cards(8), seed=7)
        session.goNext()
        current = session.current()
        session.setShuffle(True)
        assert session.current().cardId == current.cardId

    def test_shufflingMidRoundDoesNotRewindThePositionCounter(self):
        session = StudySession(self.cards(8), seed=7)
        session.goNext()
        session.goNext()
        session.setShuffle(True)
        assert session.position == 3
        assert session.roundSize == 8

    def test_shufflingMidRoundLeavesVisitedCardsInPlace(self):
        session = StudySession(self.cards(8), seed=7)
        session.goNext()
        session.goNext()
        session.setShuffle(True)
        # Everything up to and including the cursor is untouched; only what is ahead moves.
        assert [c.cardId for c in session._queue[:3]] == [0, 1, 2]  # noqa: SLF001
        assert sorted(c.cardId for c in session._queue[3:]) == [3, 4, 5, 6, 7]  # noqa: SLF001

    def test_turningShuffleOffRestoresCollectionOrderAheadOfTheCursor(self):
        session = StudySession(self.cards(5), shuffle=True, seed=3)
        session.setShuffle(False)
        queue = session._queue  # noqa: SLF001
        # The current card stays put; the four still to come return to due order.
        assert [c.cardId for c in queue[1:]] == sorted(c.cardId for c in queue[1:])
        assert session.position == 1

    def test_gradingEveryCardLeavesNothingUnseen(self):
        session = StudySession(self.cards(6))
        while True:
            session.mark(STATUS_LEARNING)
            if not session.goNext():
                break
        known, learning, unseen = session.counts()
        assert (known, learning, unseen) == (0, 6, 0)


def encodeVarint(value):
    """Encode `value` as a protobuf base-128 varint."""
    out = bytearray()
    value = int(value)
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


class TestPersistenceHelpers(object):
    """The local on-disk state format and the keying of remembered sessions."""

    def test_studySetKeyChangesWhenTheStudySetChanges(self):
        key = persistence.studySetKey("/tmp/c.anki2", [1], {"due", "new"})
        assert key != persistence.studySetKey("/tmp/c.anki2", [2], {"due", "new"})
        assert key != persistence.studySetKey("/tmp/c.anki2", [1], {"due"})
        assert key != persistence.studySetKey("/tmp/other.anki2", [1], {"due", "new"})

    def test_studySetKeyIgnoresOrdering(self):
        a = persistence.studySetKey("/tmp/c.anki2", [2, 1], {"new", "due"})
        b = persistence.studySetKey("/tmp/c.anki2", [1, 2], {"due", "new"})
        assert a == b

    def test_serialiseSessionStoresStatusesFilterAndCurrentCard(self):
        session = StudySession([makeRow(["a", "b"], cardId=1),
                                makeRow(["c", "d"], cardId=2)])
        session.mark(STATUS_LEARNING)
        session.goNext()
        session.mark(STATUS_KNOWN)
        session.setStatusFilter(STATUS_KNOWN)
        session.setStatusFilter(FILTER_ALL)
        state = persistence.serialiseSession(session)
        assert state["statuses"] == {1: STATUS_LEARNING, 2: STATUS_KNOWN}
        assert state["filter"] == FILTER_ALL
        assert state["currentCardId"] == 2
        assert state["anchors"] == {FILTER_ALL: 2, STATUS_KNOWN: 2}

    def test_applySessionStateRestoresStatusesFilterAndCursor(self):
        session = StudySession([makeRow(["a", "b"], cardId=1),
                                makeRow(["c", "d"], cardId=2),
                                makeRow(["e", "f"], cardId=3)])
        persistence.applySessionState(session, {
            "statuses": {"1": STATUS_LEARNING, "2": STATUS_KNOWN},
            "anchors": {FILTER_ALL: 1, STATUS_KNOWN: 2},
            "filter": STATUS_KNOWN,
            "currentCardId": 2,
        })
        assert session.counts() == (1, 1, 1)
        assert session.statusFilter == STATUS_KNOWN
        assert session.current().cardId == 2
        session.setStatusFilter(FILTER_ALL)
        assert session.current().cardId == 1

    def test_applySessionStateIgnoresUnknownCardsAndBadStatuses(self):
        session = StudySession([makeRow(["a", "b"], cardId=1)])
        persistence.applySessionState(session, {
            "statuses": {"1": "bogus", "999": STATUS_KNOWN},
            "anchors": {"nope": 1, STATUS_KNOWN: 999},
            "filter": "nope",
            "currentCardId": 999,
        })
        assert session.counts() == (0, 0, 1)
        assert session.statusFilter == FILTER_ALL
        assert session.current().cardId == 1

    def test_loadStateFileSurvivesMissingAndCorruptFiles(self, tmp_path, monkeypatch):
        statePath = tmp_path / "state.json"
        monkeypatch.setattr(persistence, "STATE_PATH", str(statePath))
        monkeypatch.setattr(persistence, "APP_DIR", str(tmp_path))
        assert persistence.loadStateFile()["sessions"] == {}
        statePath.write_text("{ not json")
        assert persistence.loadStateFile()["sessions"] == {}

    def test_saveAndLoadSessionStateRoundTrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(persistence, "APP_DIR", str(tmp_path))
        monkeypatch.setattr(persistence, "STATE_PATH", str(tmp_path / "state.json"))
        state = {"statuses": {1: STATUS_KNOWN}, "filter": FILTER_ALL, "currentCardId": 1}
        persistence.saveSessionState("/tmp/c.anki2", [1], {"due"}, state, studyDay=610)
        loaded = persistence.loadSessionState("/tmp/c.anki2", [1], {"due"}, studyDay=610)
        assert loaded["statuses"] == {"1": STATUS_KNOWN}

    def test_clearSessionStateRemovesOnlyThatStudySet(self, tmp_path, monkeypatch):
        monkeypatch.setattr(persistence, "APP_DIR", str(tmp_path))
        monkeypatch.setattr(persistence, "STATE_PATH", str(tmp_path / "state.json"))
        persistence.saveSessionState("/tmp/a.anki2", [1], {"due"}, {"statuses": {}}, studyDay=610)
        persistence.saveSessionState("/tmp/b.anki2", [1], {"due"}, {"statuses": {}}, studyDay=610)
        persistence.clearSessionState("/tmp/a.anki2", [1], {"due"})
        assert persistence.loadSessionState("/tmp/a.anki2", [1], {"due"}, studyDay=610) is None
        assert persistence.loadSessionState("/tmp/b.anki2", [1], {"due"}, studyDay=610) is not None

    def test_savedStateFromAnotherDayIsIgnored(self, tmp_path, monkeypatch):
        monkeypatch.setattr(persistence, "APP_DIR", str(tmp_path))
        monkeypatch.setattr(persistence, "STATE_PATH", str(tmp_path / "state.json"))
        persistence.saveSessionState(
            "/tmp/c.anki2", [1], {"due"}, {"statuses": {1: STATUS_KNOWN}}, studyDay=610)
        assert persistence.loadSessionState("/tmp/c.anki2", [1], {"due"}, studyDay=611) is None
        assert persistence.loadSessionState("/tmp/c.anki2", [1], {"due"}, studyDay=610) is not None


class TestStatusFilter(object):
    """Narrowing the round to one grade."""

    def cards(self, count=5):
        """Return `count` distinct rows."""
        return [makeRow(["f%d" % i, "b%d" % i], cardId=i) for i in range(count)]

    def graded(self):
        """A 5-card session: 0 and 2 known, 1 learning, 3 and 4 ungraded."""
        session = StudySession(self.cards(5))
        session.mark(STATUS_KNOWN)
        session.goNext()
        session.mark(STATUS_LEARNING)
        session.goNext()
        session.mark(STATUS_KNOWN)
        return session

    def test_defaultFilterShowsEverything(self):
        session = self.graded()
        assert session.statusFilter == FILTER_ALL
        assert session.isFiltered is False
        assert session.visibleCount == 5

    def test_learningFilterShowsOnlyThoseCards(self):
        session = self.graded()
        session.setStatusFilter(STATUS_LEARNING)
        assert [c.cardId for c in session._view] == [1]  # noqa: SLF001
        assert session.visibleCount == 1

    def test_knownFilterShowsOnlyThoseCards(self):
        session = self.graded()
        session.setStatusFilter(STATUS_KNOWN)
        assert [c.cardId for c in session._view] == [0, 2]  # noqa: SLF001

    def test_roundSizeIgnoresTheFilter(self):
        session = self.graded()
        session.setStatusFilter(STATUS_LEARNING)
        assert session.roundSize == 5
        assert session.visibleCount == 1

    def test_countsIgnoreTheFilter(self):
        session = self.graded()
        session.setStatusFilter(STATUS_LEARNING)
        assert session.counts() == (2, 1, 2)

    def test_navigationStaysInsideTheFilteredSubset(self):
        session = self.graded()
        session.setStatusFilter(STATUS_KNOWN)
        assert session.current().cardId == 0
        assert session.goNext() is True
        assert session.current().cardId == 2
        assert session.goNext() is False

    def test_narrowingStartsAtTheTopOfTheSubset(self):
        session = self.graded()
        # The cursor is on card 2 at this point; filtering is a fresh pass, not a jump.
        session.setStatusFilter(STATUS_KNOWN)
        assert session.current().cardId == 0
        assert session.position == 1

    def test_wideningBackToAllKeepsTheCardOnScreen(self):
        session = self.graded()
        session.setStatusFilter(STATUS_KNOWN)
        session.goNext()
        assert session.current().cardId == 2
        session.setStatusFilter(FILTER_ALL)
        # Clearing the filter should not lose the user's place.
        assert session.current().cardId == 2

    def test_gradingOutOfTheFilterSlidesTheNextCardIn(self):
        session = StudySession(self.cards(4))
        for _ in range(4):
            session.mark(STATUS_LEARNING)
            session.goNext()
        session.setStatusFilter(STATUS_LEARNING)
        assert session.current().cardId == 0
        stillVisible = session.mark(STATUS_KNOWN)
        assert stillVisible is False
        assert session.visibleCount == 3
        assert session.current().cardId == 1

    def test_gradingTheLastFilteredCardStepsBack(self):
        session = StudySession(self.cards(2))
        session.mark(STATUS_LEARNING)
        session.goNext()
        session.mark(STATUS_LEARNING)
        session.setStatusFilter(STATUS_LEARNING)
        session.goNext()
        assert session.current().cardId == 1
        session.mark(STATUS_KNOWN)
        assert session.current().cardId == 0

    def test_emptyFilterIsReportedWithoutEmptyingTheRound(self):
        session = StudySession(self.cards(3))
        session.setStatusFilter(STATUS_KNOWN)
        assert session.filterIsEmpty is True
        assert session.isEmpty is False
        assert session.current() is None

    def test_anUnknownFilterValueFallsBackToAll(self):
        session = self.graded()
        session.setStatusFilter("nonsense")
        assert session.statusFilter == FILTER_ALL
        assert session.visibleCount == 5

    def test_nextRoundClearsTheFilter(self):
        session = self.graded()
        session.setStatusFilter(STATUS_LEARNING)
        session.startNextRound()
        assert session.statusFilter == FILTER_ALL
        assert session.visibleCount == 3

    def test_replayRoundClearsTheFilter(self):
        session = self.graded()
        session.setStatusFilter(STATUS_KNOWN)
        session.replayRound()
        assert session.statusFilter == FILTER_ALL
        assert session.visibleCount == 5

    def test_resetClearsTheFilter(self):
        session = self.graded()
        session.setStatusFilter(STATUS_KNOWN)
        session.reset()
        assert session.statusFilter == FILTER_ALL
        assert session.visibleCount == 5

    def test_shufflingWhileFilteredKeepsTheCurrentCard(self):
        session = StudySession(self.cards(8), seed=5)
        for _ in range(8):
            session.mark(STATUS_LEARNING)
            session.goNext()
        session.setStatusFilter(STATUS_LEARNING)
        session.goNext()
        current = session.current()
        session.setShuffle(True)
        assert session.current().cardId == current.cardId
        assert session.visibleCount == 8

    def test_eachFilterRemembersItsOwnLastCard(self):
        session = self.graded()
        session.setStatusFilter(STATUS_KNOWN)
        session.goNext()
        assert session.current().cardId == 2
        session.setStatusFilter(STATUS_LEARNING)
        assert session.current().cardId == 1
        session.setStatusFilter(STATUS_KNOWN)
        assert session.current().cardId == 2

    def test_allCardsRemembersItsPlaceToo(self):
        session = StudySession(self.cards(5))
        session.goNext()
        session.goNext()
        assert session.current().cardId == 2
        session.setStatusFilter(STATUS_KNOWN)
        session.setStatusFilter(FILTER_ALL)
        assert session.current().cardId == 2

    def test_firstTimeEnteringAFilterStillStartsAtTheTop(self):
        session = self.graded()
        session.goNext()
        session.goNext()
        session.setStatusFilter(STATUS_KNOWN)
        assert session.current().cardId == 0

    def test_filter_anchorTracksWhereTheUserLeftThatSubset(self):
        session = StudySession(self.cards(4))
        session.mark(STATUS_LEARNING)
        session.goNext()
        session.mark(STATUS_LEARNING)
        session.goNext()
        session.mark(STATUS_LEARNING)
        session.setStatusFilter(STATUS_LEARNING)
        session.goNext()
        assert session.current().cardId == 1
        session.setStatusFilter(FILTER_ALL)
        session.setStatusFilter(STATUS_LEARNING)
        assert session.current().cardId == 1

    def test_nextRoundClearsFilterAnchors(self):
        session = self.graded()
        session.setStatusFilter(STATUS_KNOWN)
        session.goNext()
        session.startNextRound()
        assert session._anchors == {FILTER_ALL: 1}  # noqa: SLF001

    def test_resetClearsFilterAnchors(self):
        session = self.graded()
        session.setStatusFilter(STATUS_KNOWN)
        session.reset()
        assert session._anchors == {FILTER_ALL: 0}  # noqa: SLF001


def buildFakeCollection(path, crt, cards, newPerDay=20, lastDayStudied=-1, newStudied=0,
                        reviewLog=(), noteTags=None):
    """Write a minimal schema-18 collection containing `cards` as (queue, due, deck) triples.

    `reviewLog` holds (cardId, epochMilliseconds, ease) rows to seed the revlog with, and
    `noteTags` a per-card tag string written to the matching note.
    """
    conn = sqlite3.connect(path)
    # The tags table is declared "collate unicase" exactly as Anki declares it, so the
    # builder needs the same collation the app registers when reading.
    conn.create_collation("unicase", _unicaseCollation)
    conn.executescript("""
        CREATE TABLE col (id integer primary key, crt integer, conf text,
                          models text, decks text, dconf text);
        CREATE TABLE config (key text primary key, val blob);
        CREATE TABLE deck_config (id integer primary key, name text, config blob);
        CREATE TABLE decks (id integer primary key, name text, common blob, kind blob);
        CREATE TABLE notetypes (id integer primary key, name text, config blob);
        CREATE TABLE fields (ntid integer, ord integer, name text, config blob);
        CREATE TABLE templates (ntid integer, ord integer, name text, config blob);
        CREATE TABLE notes (id integer primary key, mid integer, tags text, flds text);
        CREATE TABLE cards (id integer primary key, nid integer, did integer, odid integer,
                            ord integer, queue integer, type integer, due integer,
                            ivl integer, lapses integer default 0);
        CREATE TABLE revlog (id integer primary key, cid integer, ease integer, type integer);
        CREATE TABLE tags (tag text primary key collate unicase, usn integer);
    """)
    conn.execute("INSERT INTO col VALUES (1, ?, '{}', '{}', '{}', '{}')", (crt,))
    conn.execute("INSERT INTO config VALUES ('rollover', ?)", (b"4",))
    # new_per_day is DeckConfigInner field 9; every deck points at this one config.
    conn.execute("INSERT INTO deck_config VALUES (1, 'Default', ?)",
                 (b"\x48" + encodeVarint(newPerDay),))
    deckCommon = (b"\x18" + encodeVarint(max(0, lastDayStudied))
                  + b"\x20" + encodeVarint(newStudied))
    deckKind = b"\x0a\x02\x08\x01"  # Deck.kind -> Normal{config_id = 1}
    conn.executemany("INSERT INTO decks VALUES (?, ?, ?, ?)", [
        (1, "Korean", deckCommon, deckKind),
        (2, "Korean\x1fVerbs", deckCommon, deckKind),
    ])
    conn.execute("INSERT INTO notetypes VALUES (99, 'Basic', ?)", (b"\x1a\x07.card{}",))
    conn.executemany("INSERT INTO fields VALUES (99, ?, ?, x'')",
                     [(0, "Front"), (1, "Back")])
    conn.execute("INSERT INTO templates VALUES (99, 0, 'Card 1', ?)",
                 (b"\n\x09{{Front}}\x12\x08{{Back}}",))
    for index, (queue, due, deckId) in enumerate(cards):
        # Anki pads the tag string with a space on each side; matching relies on that.
        tags = (noteTags[index] if noteTags else "").strip()
        tags = " %s " % tags if tags else ""
        conn.execute("INSERT INTO notes VALUES (?, 99, ?, ?)",
                     (index + 1, tags, "front%d\x1fback%d" % (index, index)))
        conn.execute("INSERT INTO cards VALUES (?, ?, ?, 0, 0, ?, 2, ?, 10, 0)",
                     (index + 1, index + 1, deckId, queue, due))
    for tag in sorted({t for row in (noteTags or []) for t in row.split()}):
        conn.execute("INSERT INTO tags VALUES (?, 0)", (tag,))
    for cardId, stampMs, ease in reviewLog:
        conn.execute("INSERT INTO revlog VALUES (?, ?, ?, 1)", (stampMs, cardId, ease))
    conn.commit()
    conn.close()


@pytest.fixture(name="fakeProfile")
def fakeProfileFixture(tmp_path):
    """A Profile pointing at a purpose-built collection: 2 due, 1 future, 1 new, 1 subdeck."""
    crt = int(time.time()) - 100 * 86400
    today, _next = scheduling.schedTimingToday(crt, int(time.time()), 4)
    base = tmp_path / "basic"
    base.mkdir()
    collectionPath = str(base / "collection.anki2")
    buildFakeCollection(collectionPath, crt, [
        (2, today - 1, 1),      # due, a day late
        (2, today, 1),          # due today
        (2, today + 5, 1),      # not due yet
        (0, 0, 1),              # new
        (2, today, 2),          # due today, in the subdeck
    ])
    return Profile(name="Test", root=str(base), collectionPath=collectionPath,
                   mediaPath=str(base / "collection.media"))


@pytest.fixture(name="cappedProfile")
def cappedProfileFixture(tmp_path):
    """A collection with 6 new cards, a limit of 2/day, and 1 already started today."""
    crt = int(time.time()) - 100 * 86400
    today, _next = scheduling.schedTimingToday(crt, int(time.time()), 4)
    base = tmp_path / "capped"
    base.mkdir()
    collectionPath = str(base / "collection.anki2")
    buildFakeCollection(
        collectionPath, crt,
        [(2, today, 1)] + [(0, position, 1) for position in range(6)],
        newPerDay=2, lastDayStudied=today, newStudied=1)
    return Profile(name="Capped", root=str(base), collectionPath=collectionPath,
                   mediaPath=str(base / "collection.media"))


@pytest.fixture(name="reviewedProfile")
def reviewedProfileFixture(tmp_path):
    """A collection where some cards were answered today and rescheduled into the future.

    Cards 1 and 2 were answered today, card 3 is still due today and untouched, card 4 was
    answered before the rollover, and card 5 has only a manual (ease 0) revlog row.
    """
    crt = int(time.time()) - 100 * 86400
    today, nextDayAt = scheduling.schedTimingToday(crt, int(time.time()), 4)
    dayStartMs = scheduling.dayStartMilliseconds(nextDayAt)
    base = tmp_path / "reviewed"
    base.mkdir()
    collectionPath = str(base / "collection.anki2")
    buildFakeCollection(collectionPath, crt, [
        (2, today + 3, 1),
        (2, today + 1, 1),
        (2, today, 1),
        (2, today + 9, 1),
        (2, today + 2, 1),
    ], reviewLog=[
        (1, dayStartMs + 60000, 3),
        (2, dayStartMs + 120000, 2),
        (4, dayStartMs - 7200000, 3),   # before today's rollover
        (5, dayStartMs + 180000, 0),    # ease 0 is a manual entry, not an answer
    ])
    return Profile(name="Reviewed", root=str(base), collectionPath=collectionPath,
                   mediaPath=str(base / "collection.media"))


class TestProfileDiscovery(object):
    """Finding collections, including on machines the app was not developed on."""

    def test_windowsAndMacRootsAreProbedToo(self):
        from anki_drill import collection as collectionModule
        joined = " ".join(collectionModule.PROFILE_ROOTS)
        assert "AppData/Roaming/Anki2" in joined
        assert "Library/Application Support/Anki2" in joined

    def test_candidateRootsOnlyReturnsDirectoriesThatExist(self, tmp_path, monkeypatch):
        from anki_drill import collection as collectionModule
        real = tmp_path / "Anki2"
        real.mkdir()
        monkeypatch.setattr(collectionModule, "PROFILE_ROOTS",
                            (str(real), str(tmp_path / "nope")))
        monkeypatch.delenv("APPDATA", raising=False)
        assert collectionModule.candidateRoots() == [str(real)]

    def test_candidateRootsDeduplicates(self, tmp_path, monkeypatch):
        from anki_drill import collection as collectionModule
        real = tmp_path / "Anki2"
        real.mkdir()
        monkeypatch.setattr(collectionModule, "PROFILE_ROOTS", (str(real), str(real)))
        monkeypatch.delenv("APPDATA", raising=False)
        assert collectionModule.candidateRoots() == [str(real)]

    def test_appDataEnvironmentRootIsProbedFirst(self, tmp_path, monkeypatch):
        from anki_drill import collection as collectionModule
        appData = tmp_path / "Roaming"
        (appData / "Anki2").mkdir(parents=True)
        monkeypatch.setenv("APPDATA", str(appData))
        monkeypatch.setattr(collectionModule, "PROFILE_ROOTS", ())
        assert collectionModule.candidateRoots() == [str(appData / "Anki2")]

    def test_profileForCollectionNamesItselfAfterItsFolder(self, tmp_path):
        from anki_drill.collection import profileForCollection
        path = tmp_path / "User 1" / "collection.anki2"
        path.parent.mkdir()
        path.write_bytes(b"")
        profile = profileForCollection(str(path))
        assert profile.name == "User 1"
        assert profile.collectionPath == str(path)
        assert profile.mediaPath == str(tmp_path / "User 1" / "collection.media")

    def test_anOpenedCollectionCanActuallyBeRead(self, fakeProfile):
        from anki_drill.collection import profileForCollection
        profile = profileForCollection(fakeProfile.collectionPath)
        with CollectionSnapshot(profile) as snapshot:
            assert snapshot.schema == 18
            assert dict(snapshot.decks())[1] == "Korean"


class TestReviewedQueue(object):
    """The 'Reviewed today' selection keeps today's finished work drillable."""

    def timing(self, snapshot):
        """Return (today, dayStartMs) for the open snapshot."""
        today, nextDayAt = scheduling.schedTimingToday(
            snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())
        return today, scheduling.dayStartMilliseconds(nextDayAt)

    def test_clauseQueriesTheRevlogFromTheDayStart(self):
        clause, params = scheduling.queueClause({"reviewed"}, today=10, dayStartMs=5000)
        assert "revlog" in clause
        assert params == [5000]

    def test_dayStartIsExactlyOneDayBeforeTheNextRollover(self):
        assert scheduling.dayStartMilliseconds(1000 * 86400) == (999 * 86400) * 1000

    def test_cardsAnsweredTodayAreReturned(self, reviewedProfile):
        with CollectionSnapshot(reviewedProfile) as snapshot:
            today, dayStartMs = self.timing(snapshot)
            rows = snapshot.fetchCards(None, {"reviewed"}, today, None, dayStartMs)
            assert sorted(r.cardId for r in rows) == [1, 2]

    def test_cardsAnsweredBeforeTheRolloverAreExcluded(self, reviewedProfile):
        with CollectionSnapshot(reviewedProfile) as snapshot:
            today, dayStartMs = self.timing(snapshot)
            rows = snapshot.fetchCards(None, {"reviewed"}, today, None, dayStartMs)
            assert 4 not in {r.cardId for r in rows}

    def test_manualRevlogEntriesDoNotCountAsAnswered(self, reviewedProfile):
        with CollectionSnapshot(reviewedProfile) as snapshot:
            today, dayStartMs = self.timing(snapshot)
            rows = snapshot.fetchCards(None, {"reviewed"}, today, None, dayStartMs)
            assert 5 not in {r.cardId for r in rows}

    def test_reviewedCombinesWithDueWithoutDuplicating(self, reviewedProfile):
        with CollectionSnapshot(reviewedProfile) as snapshot:
            today, dayStartMs = self.timing(snapshot)
            rows = snapshot.fetchCards(None, {"due", "reviewed"}, today, None, dayStartMs)
            ids = [r.cardId for r in rows]
            assert sorted(ids) == [1, 2, 3]
            assert len(ids) == len(set(ids))

    def test_reviewedCardsAreLabelledDoneRatherThanOverdue(self, reviewedProfile):
        with CollectionSnapshot(reviewedProfile) as snapshot:
            today, dayStartMs = self.timing(snapshot)
            rows = snapshot.fetchCards(None, {"reviewed"}, today, None, dayStartMs)
            labels = {r.cardId: scheduling.describeCard(r, today) for r in rows}
            assert labels[1] == "Done · next due in 3d"
            assert labels[2] == "Done · next due in 1d"

    def test_countsReportTheReviewedQueue(self, reviewedProfile):
        with CollectionSnapshot(reviewedProfile) as snapshot:
            today, dayStartMs = self.timing(snapshot)
            counts = snapshot.queueCounts(None, today, None, dayStartMs)
            assert counts == {"due": 1, "new": 0, "reviewed": 2, "pinned": 0}


@pytest.fixture(name="pinnedProfile")
def pinnedProfileFixture(tmp_path):
    """A collection exercising every pin state the add-on can leave behind.

    Card 1 is pinned today, card 2 was pinned yesterday, card 3 carries a stale marker the
    add-on treats as expired, card 4 has the marker tag but no day tag, card 5 is pinned
    today but suspended, and card 6 is untagged.
    """
    crt = int(time.time()) - 100 * 86400
    today, _next = scheduling.schedTimingToday(crt, int(time.time()), 4)
    base = tmp_path / "pinned"
    base.mkdir()
    collectionPath = str(base / "collection.anki2")
    live = scheduling.pinDayTag(today)
    yesterday = scheduling.pinDayTag(today - 1)
    stale = scheduling.pinDayTag(today - 9)
    buildFakeCollection(collectionPath, crt, [
        (2, today + 4, 1),
        (2, today + 4, 1),
        (2, today + 4, 1),
        (2, today + 4, 1),
        (-1, today + 4, 1),
        (2, today, 1),
    ], noteTags=[
        "review_pinned %s" % live,
        "review_pinned %s" % yesterday,
        "review_pinned %s" % stale,
        "review_pinned",
        "review_pinned %s" % live,
        "",
    ])
    return Profile(name="Pinned", root=str(base), collectionPath=collectionPath,
                   mediaPath=str(base / "collection.media"))


class TestPinnedQueue(object):
    """The Pinned selection, backed by the Today's Review Words add-on's tags."""

    def today(self, snapshot):
        """Return the current study day for the snapshot."""
        return scheduling.schedTimingToday(
            snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())[0]

    def test_tagTokenPadsWithSpacesLikeAnkiDoes(self):
        assert scheduling.tagToken("review_pinned") == " review_pinned "

    def test_pinDayTagMatchesTheAddonFormat(self):
        assert scheduling.pinDayTag(610) == "review_pinned_day_610"

    def test_clauseMatchesTodayAndYesterdayAndExcludesBuriedCards(self):
        clause, params = scheduling.queueClause({"pinned"}, today=610)
        assert "c.queue >= 0" in clause
        assert params == [" review_pinned ",
                          " review_pinned_day_610 ",
                          " review_pinned_day_609 "]

    def test_pinnedTodayAndYesterdayAreReturned(self, pinnedProfile):
        with CollectionSnapshot(pinnedProfile) as snapshot:
            rows = snapshot.fetchCards(None, {"pinned"}, self.today(snapshot))
            assert sorted(r.cardId for r in rows) == [1, 2]

    def test_expiredPinsAreIgnored(self, pinnedProfile):
        with CollectionSnapshot(pinnedProfile) as snapshot:
            rows = snapshot.fetchCards(None, {"pinned"}, self.today(snapshot))
            # Card 3 keeps a day marker from over a week ago; the add-on treats it as gone.
            assert 3 not in {r.cardId for r in rows}

    def test_markerTagWithoutADayTagIsNotAPin(self, pinnedProfile):
        with CollectionSnapshot(pinnedProfile) as snapshot:
            rows = snapshot.fetchCards(None, {"pinned"}, self.today(snapshot))
            assert 4 not in {r.cardId for r in rows}

    def test_suspendedPinnedCardsStayOut(self, pinnedProfile):
        with CollectionSnapshot(pinnedProfile) as snapshot:
            rows = snapshot.fetchCards(None, {"pinned"}, self.today(snapshot))
            assert 5 not in {r.cardId for r in rows}

    def test_pinnedCombinesWithDueWithoutDuplicating(self, pinnedProfile):
        with CollectionSnapshot(pinnedProfile) as snapshot:
            today = self.today(snapshot)
            rows = snapshot.fetchCards(None, {"due", "pinned"}, today)
            ids = [r.cardId for r in rows]
            assert sorted(ids) == [1, 2, 6]
            assert len(ids) == len(set(ids))

    def test_underscoresInTagsAreNotTreatedAsWildcards(self, tmp_path):
        # LIKE would let "review_pinned_day_610" match "review-pinned-day-610"; instr does not.
        crt = int(time.time()) - 100 * 86400
        today, _n = scheduling.schedTimingToday(crt, int(time.time()), 4)
        path = str(tmp_path / "collection.anki2")
        buildFakeCollection(path, crt, [(2, today + 4, 1)],
                            noteTags=["reviewXpinned reviewXpinnedXdayX%d" % today])
        profile = Profile(name="P", root=str(tmp_path), collectionPath=path,
                          mediaPath=str(tmp_path / "collection.media"))
        with CollectionSnapshot(profile) as snapshot:
            assert snapshot.fetchCards(None, {"pinned"}, today) == []

    def test_countsIncludeThePinnedQueue(self, pinnedProfile):
        with CollectionSnapshot(pinnedProfile) as snapshot:
            today = self.today(snapshot)
            counts = snapshot.queueCounts(None, today, snapshot.deckNewAllowances(today))
            assert counts == {"due": 1, "new": 0, "reviewed": 0, "pinned": 2}

    def test_hasPinTagsSpotsTheMarker(self, pinnedProfile, fakeProfile):
        with CollectionSnapshot(pinnedProfile) as snapshot:
            assert snapshot.hasPinTags() is True
        with CollectionSnapshot(fakeProfile) as snapshot:
            assert snapshot.hasPinTags() is False

    def test_tagsTableIsQueryableDespiteAnkisUnicaseCollation(self, pinnedProfile):
        # Without a registered "unicase" collation sqlite3 refuses these queries outright.
        with CollectionSnapshot(pinnedProfile) as snapshot:
            rows = snapshot._conn.execute(  # noqa: SLF001
                "SELECT tag FROM tags ORDER BY tag").fetchall()
            assert ("review_pinned",) in rows


class TestAddonDetection(object):
    """Deciding whether the Pinned selection should be offered at all."""

    def writeAddon(self, profile, meta=None, package="todays_review_words"):
        """Create the add-on folder for `profile`, optionally with a meta.json."""
        addonDir = os.path.join(profile.root, "addons21", package)
        os.makedirs(addonDir)
        if meta is not None:
            with open(os.path.join(addonDir, "meta.json"), "w") as handle:
                json.dump(meta, handle)
        return addonDir

    def test_absentWhenTheFolderDoesNotExist(self, pinnedProfile):
        from anki_drill.collection import ADDON_ABSENT, addonState
        assert addonState(pinnedProfile) == ADDON_ABSENT

    def test_folderWithoutMetaCountsAsEnabled(self, pinnedProfile):
        from anki_drill.collection import ADDON_ENABLED, addonState
        self.writeAddon(pinnedProfile)
        assert addonState(pinnedProfile) == ADDON_ENABLED

    def test_metaCanDisableTheAddon(self, pinnedProfile):
        from anki_drill.collection import ADDON_DISABLED, addonState
        self.writeAddon(pinnedProfile, meta={"name": "Today's Review Words",
                                             "disabled": True})
        assert addonState(pinnedProfile) == ADDON_DISABLED

    def test_metaThatDoesNotDisableCountsAsEnabled(self, pinnedProfile):
        from anki_drill.collection import ADDON_ENABLED, addonState
        self.writeAddon(pinnedProfile, meta={"disabled": False})
        assert addonState(pinnedProfile) == ADDON_ENABLED

    def test_corruptMetaDoesNotHideTheAddon(self, pinnedProfile):
        from anki_drill.collection import ADDON_ENABLED, addonState
        addonDir = self.writeAddon(pinnedProfile)
        with open(os.path.join(addonDir, "meta.json"), "w") as handle:
            handle.write("{ not json")
        assert addonState(pinnedProfile) == ADDON_ENABLED

    def test_pinningOfferedWhenTheAddonIsEnabled(self, pinnedProfile):
        from anki_drill.collection import pinningAvailable
        self.writeAddon(pinnedProfile)
        with CollectionSnapshot(pinnedProfile) as snapshot:
            assert pinningAvailable(pinnedProfile, snapshot) is True

    def test_pinningHiddenWhenTheAddonIsDisabledEvenWithTags(self, pinnedProfile):
        from anki_drill.collection import pinningAvailable
        self.writeAddon(pinnedProfile, meta={"disabled": True})
        with CollectionSnapshot(pinnedProfile) as snapshot:
            assert pinningAvailable(pinnedProfile, snapshot) is False

    def test_pinningOfferedFromTagsWhenTheAddonIsNotInstalled(self, pinnedProfile):
        from anki_drill.collection import pinningAvailable
        with CollectionSnapshot(pinnedProfile) as snapshot:
            assert pinningAvailable(pinnedProfile, snapshot) is True

    def test_pinningHiddenWithNoAddonAndNoTags(self, fakeProfile):
        from anki_drill.collection import pinningAvailable
        with CollectionSnapshot(fakeProfile) as snapshot:
            assert pinningAvailable(fakeProfile, snapshot) is False


class TestNewAllowance(object):
    """The per-deck daily new-card allowance."""

    def test_fullLimitIsAvailableWhenNothingStudiedToday(self):
        assert scheduling.newAllowance(10, lastDayStudied=608, newStudied=10, today=609) == 10

    def test_todaysTallyIsSubtractedFromTheLimit(self):
        assert scheduling.newAllowance(10, lastDayStudied=609, newStudied=4, today=609) == 6

    def test_allowanceNeverGoesNegative(self):
        assert scheduling.newAllowance(10, lastDayStudied=609, newStudied=25, today=609) == 0

    def test_exhaustedLimitYieldsZero(self):
        assert scheduling.newAllowance(10, lastDayStudied=609, newStudied=10, today=609) == 0


class TestCapNewCards(object):
    """Trimming new cards to each deck's allowance."""

    def rows(self, spec):
        """Build rows from (cardId, queue, deckId) triples."""
        return [makeRow(["f", "b"], cardId=cid, queue=q)._replace(deckId=did)
                for cid, q, did in spec]

    def test_reviewCardsAreNeverTrimmed(self):
        rows = self.rows([(1, 2, 1), (2, 2, 1), (3, 2, 1)])
        assert len(scheduling.capNewCards(rows, {1: 0})) == 3

    def test_newCardsAreTrimmedToTheAllowance(self):
        rows = self.rows([(1, 0, 1), (2, 0, 1), (3, 0, 1)])
        kept = scheduling.capNewCards(rows, {1: 2})
        assert [r.cardId for r in kept] == [1, 2]

    def test_allowancesAreTrackedPerDeck(self):
        rows = self.rows([(1, 0, 1), (2, 0, 1), (3, 0, 2), (4, 0, 2)])
        kept = scheduling.capNewCards(rows, {1: 1, 2: 2})
        assert [r.cardId for r in kept] == [1, 3, 4]

    def test_deckWithNoAllowanceEntryContributesNothing(self):
        rows = self.rows([(1, 0, 7)])
        assert scheduling.capNewCards(rows, {1: 5}) == []

    def test_noAllowancesMeansNoTrimming(self):
        rows = self.rows([(1, 0, 1), (2, 0, 1)])
        assert len(scheduling.capNewCards(rows, None)) == 2


class TestCollectionSnapshot(object):
    """Reading a collection through the temporary snapshot copy."""

    def test_newCardsAreCappedAtTheRemainingDailyAllowance(self, cappedProfile):
        with CollectionSnapshot(cappedProfile) as snapshot:
            today, _n = scheduling.schedTimingToday(
                snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())
            allowances = snapshot.deckNewAllowances(today)
            # Limit 2/day with 1 already started leaves room for exactly 1 more.
            assert allowances[1] == 1
            counts = snapshot.queueCounts(None, today, allowances)
            assert counts == {"due": 1, "new": 1, "reviewed": 0, "pinned": 0}

    def test_uncappedFetchStillReturnsEveryNewCard(self, cappedProfile):
        with CollectionSnapshot(cappedProfile) as snapshot:
            today, _n = scheduling.schedTimingToday(
                snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())
            assert len(snapshot.fetchCards(None, {"new"}, today)) == 6

    def test_cappedNewCardsKeepTheirIntroductionOrder(self, cappedProfile):
        with CollectionSnapshot(cappedProfile) as snapshot:
            today, _n = scheduling.schedTimingToday(
                snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())
            rows = snapshot.fetchCards(None, {"new"}, today, {1: 3})
            assert [r.due for r in rows] == [0, 1, 2]

    def test_reviewsAreOrderedBeforeNewCards(self, cappedProfile):
        with CollectionSnapshot(cappedProfile) as snapshot:
            today, _n = scheduling.schedTimingToday(
                snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())
            rows = snapshot.fetchCards(None, {"due", "new"}, today, {1: 2})
            assert [r.queue for r in rows] == [2, 0, 0]

    def test_snapshotDoesNotOpenTheOriginalFile(self, fakeProfile):
        with CollectionSnapshot(fakeProfile) as snapshot:
            originalMtime = os.path.getmtime(fakeProfile.collectionPath)
            snapshot.fetchCards(None, {"due"}, 10 ** 6)
            assert os.path.getmtime(fakeProfile.collectionPath) == originalMtime

    def test_snapshotIsReadOnly(self, fakeProfile):
        with CollectionSnapshot(fakeProfile) as snapshot:
            with pytest.raises(sqlite3.OperationalError):
                snapshot._conn.execute("DELETE FROM cards")  # noqa: SLF001

    def test_temporaryCopyIsRemovedOnClose(self, fakeProfile):
        snapshot = CollectionSnapshot(fakeProfile)
        snapshot.open()
        tempDir = snapshot._tempDir  # noqa: SLF001
        assert os.path.isdir(tempDir)
        snapshot.close()
        assert not os.path.isdir(tempDir)

    def test_nestedDeckNamesUseDoubleColons(self, fakeProfile):
        with CollectionSnapshot(fakeProfile) as snapshot:
            assert dict(snapshot.decks())[2] == "Korean::Verbs"

    def test_dueQueueExcludesFutureAndNewCards(self, fakeProfile):
        with CollectionSnapshot(fakeProfile) as snapshot:
            today, _n = scheduling.schedTimingToday(
                snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())
            rows = snapshot.fetchCards(None, {"due"}, today)
            assert len(rows) == 3

    def test_deckFilterIncludesSubdecksOfTheChosenDeck(self, fakeProfile):
        with CollectionSnapshot(fakeProfile) as snapshot:
            today, _n = scheduling.schedTimingToday(
                snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())
            subdeckOnly = snapshot.fetchCards([2], {"due"}, today)
            assert [r.deckName for r in subdeckOnly] == ["Korean::Verbs"]

    def test_queueCountsReportEachQueueSeparately(self, fakeProfile):
        with CollectionSnapshot(fakeProfile) as snapshot:
            today, _n = scheduling.schedTimingToday(
                snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())
            counts = snapshot.queueCounts(None, today, snapshot.deckNewAllowances(today))
            assert counts == {"due": 3, "new": 1, "reviewed": 0, "pinned": 0}

    def test_notetypeTemplatesAreDecodedFromTheProtobufBlob(self, fakeProfile):
        with CollectionSnapshot(fakeProfile) as snapshot:
            notetype = snapshot.notetypes()[99]
            assert notetype.fields == ["Front", "Back"]
            assert notetype.templates[0].qfmt == "{{Front}}"
            assert notetype.css == ".card{}"

    def test_fetchedRowsRenderIntoBothFaces(self, fakeProfile):
        with CollectionSnapshot(fakeProfile) as snapshot:
            today, _n = scheduling.schedTimingToday(
                snapshot.creationStamp(), int(time.time()), snapshot.rolloverHour())
            row = snapshot.fetchCards(None, {"due"}, today)[0]
            term, definition = rendering.renderCard(row, snapshot.notetypes()[99])
            assert term.startswith("front")
            assert definition.startswith("back")
