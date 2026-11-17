"""
test_production.py
Typing mode and the recommended list: gap finding, answer checking, hints, typing stats,
grouped shuffle, and building the list from a purpose-built collection.
"""

import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from anki_drill import persistence, scheduling  # noqa: E402
from anki_drill.collection import CollectionSnapshot, Profile  # noqa: E402
from anki_drill.production import (  # noqa: E402
    MODE_GAP, MODE_WORD, RESULT_ALMOST, RESULT_CORRECT, RESULT_WRONG, buildTypingSpec,
    checkAnswer, conjugationsFromNotes, diffHtml, findGap, hintText, letterNotes,
    matchesForRetype, normalise, promptHtml, resultHtml, toJamo)
from anki_drill.recommend import (  # noqa: E402
    REASON_DUE, REASON_LEECH, REASON_MISSED, REASON_NEW, REASON_PINNED, REASON_REVIEWED,
    REASON_TROUBLE, REASON_TYPED, buildRecommended, summaryText)
from anki_drill.session import StudySession  # noqa: E402

from test_anki_drill import buildFakeCollection, makeRow  # noqa: E402

BASIC = {
    "Front": "개운하다",
    "Back": "refreshed, feeling clean and light",
    "Example": "푹 자고 일어나서 몸이 개운해요. [sound:kaeun.mp3]",
    "Translation": "I slept deeply and woke up, so my body feels refreshed.",
    "Note": "뜻: 상쾌하다<br>Conj: 개운해요 · 개운했어요 · 개운한 N · 개운해서",
}


class TestHangul(object):

    def test_syllablesSplitIntoLetters(self):
        assert toJamo("개운") == "ㄱㅐㅇㅜㄴ"
        assert toJamo("a한") == "aㅎㅏㄴ"

    def test_normaliseIgnoresSpacingPunctuationAndMarkup(self):
        assert normalise(" 말도  안 돼요! ") == normalise("<b>말도안돼요</b>")


class TestFindGap(object):

    def gap(self, word, sentence):
        span = findGap(word, sentence)
        return sentence[span[0]:span[1]] if span else None

    def test_conjugatedHadaVerb(self):
        assert self.gap("개운하다", "푹 자고 일어나서 몸이 개운해요.") == "개운해요"

    def test_irregularVerbMatchesOnLetters(self):
        assert self.gap("들뜨다", "여행 갈 생각에 마음이 들떠요.") == "들떠요"

    def test_nounKeepsItsParticle(self):
        assert self.gap("연차", "내일 연차를 냈어요.") == "연차를"

    def test_multiWordExpression(self):
        assert self.gap("말도 안 되다", "벌써 11월이라니 말도 안 돼요!") == "말도 안 돼요"

    def test_wordAtTheStart(self):
        assert self.gap("웬만하면", "웬만하면 주말에는 일 안 해요.") == "웬만하면"

    def test_oneSyllableStemWithEnding(self):
        assert self.gap("가다", "내일 학교에 가요.") == "가요"
        assert self.gap("가다", "어제 학교에 갔어요.") == "갔어요"

    def test_noMatchReturnsNone(self):
        assert self.gap("개운하다", "오늘은 날씨가 좋아요.") is None

    def test_oneSyllableCoincidenceIsNotAMatch(self):
        assert self.gap("가다", "가방을 샀어요.") is None


class TestBuildTypingSpec(object):

    def test_basicNoteBecomesASentenceGap(self):
        spec = buildTypingSpec(BASIC)
        assert spec.mode == MODE_GAP
        assert spec.answer == "개운해요"
        assert "tp-gap" in spec.sentenceHtml and "개운해요" not in spec.sentenceHtml
        assert spec.fullSentence == "푹 자고 일어나서 몸이 개운해요."
        assert spec.sounds == ["[sound:kaeun.mp3]"]

    def test_dictionaryFormSkipsTheGap(self):
        spec = buildTypingSpec(BASIC, dictionaryForm=True)
        assert spec.mode != MODE_GAP
        assert spec.answer == "개운하다"
        assert "개운해요" not in promptHtml(spec)

    def test_dictionaryModeAcceptsTheSentenceFormAndShowsIt(self):
        spec = buildTypingSpec(BASIC, dictionaryForm=True)
        assert checkAnswer("개운해요", spec).verdict == "correct"
        result = checkAnswer("개운하다", spec)
        assert "<b>개운해요</b>" in resultHtml(spec, result)

    def test_sentenceModeDictionaryFormIsAlmost(self):
        result = checkAnswer("개운하다", buildTypingSpec(BASIC))
        assert result.verdict == "almost" and result.formOnly

    def test_englishSpecAcceptsAnyListedMeaning(self):
        spec = buildTypingSpec(BASIC, answerForm="english")
        assert spec.mode == "english" and spec.prompt == "개운하다"
        assert checkAnswer("Refreshed", spec).verdict == "correct"
        assert checkAnswer("feeling clean and light", spec).verdict == "correct"
        assert checkAnswer("refershed", spec).verdict == "almost"
        assert checkAnswer("tired", spec).verdict == "wrong"
        assert "type the English" in promptHtml(spec)
        assert hintText(spec.answer, 2).startswith("r○○○")

    def test_englishMeaningsDropFillerWords(self):
        from anki_drill.production import englishMeanings, normaliseEnglish
        assert normaliseEnglish("to get motion sick") == "getmotionsick"
        assert "carsick" in englishMeanings("get motion sick, carsick")

    def test_typingSummary(self):
        from anki_drill.production import typingSummary
        assert typingSummary({}) == ""
        text = typingSummary({
            1: {"word": "개운하다", "tries": 3, "misses": 2, "peeks": 0, "firstTry": "wrong"},
            2: {"word": "멀미하다", "tries": 1, "misses": 0, "peeks": 0, "firstTry": "correct"},
            3: {"word": "들뜨다", "tries": 2, "misses": 0, "peeks": 1, "firstTry": "peek"}})
        assert "1 of 3 right first try (33%)" in text
        assert "Most missed: 개운하다 ×2, 들뜨다 ×1" in text
        assert "Peeked at the answer 1 time" in text

    def test_promptCarriesNoAudio(self):
        html = promptHtml(buildTypingSpec(BASIC))
        assert "sound" not in html and "개운해요" not in html

    def test_resultFaceCarriesTheAudio(self):
        spec = buildTypingSpec(BASIC)
        html = resultHtml(spec, checkAnswer("개운해요", spec))
        assert 'data-sound="kaeun.mp3"' in html

    def test_noSentenceFallsBackToTheWordWithConjugations(self):
        fields = dict(BASIC, Example="", Translation="")
        spec = buildTypingSpec(fields)
        assert spec.mode == MODE_WORD
        assert checkAnswer("개운하다", spec).verdict == RESULT_CORRECT
        assert checkAnswer("개운했어요", spec).verdict == RESULT_CORRECT

    def test_koreanTypeNoteUsesItsMarkedGapAndChunk(self):
        spec = buildTypingSpec({
            "Prompt": "If I can help it, I try not to work on weekends.",
            "Sentence": '<span class="gap">&nbsp;</span> 주말에는 일 안 하려고 해요.',
            "Chunk": "웬만하면",
            "Full": "<b>웬만하면</b> 주말에는 일 안 하려고 해요.",
        })
        assert spec.mode == MODE_GAP and spec.answer == "웬만하면"
        assert spec.fullSentence == "웬만하면 주말에는 일 안 하려고 해요."

    def test_boldPartOfTheSentenceIsTheGap(self):
        spec = buildTypingSpec({"Front": "들고 다니다", "Back": "carry around",
                                "Example": "항상 노트북 <b>들고 다니죠</b>."})
        assert spec.answer == "들고 다니죠"

    def test_swappedFrontAndBackStillAskForKorean(self):
        spec = buildTypingSpec({"Front": "refreshed", "Back": "개운하다"})
        assert spec.answer == "개운하다" and spec.prompt == "refreshed"

    def test_noteWithoutKoreanIsNotTypeable(self):
        assert buildTypingSpec({"Front": "front0", "Back": "back0"}) is None

    def test_conjugationLineParsing(self):
        assert conjugationsFromNotes(BASIC["Note"]) == ["개운해요", "개운했어요", "개운한", "개운해서"]


class TestCheckAnswer(object):

    def setup_method(self):
        self.spec = buildTypingSpec(BASIC)

    def test_correctIgnoresSpacingAndFullStop(self):
        assert checkAnswer(" 개운해요. ", self.spec).verdict == RESULT_CORRECT

    def test_oneVowelOffIsAlmost(self):
        result = checkAnswer("개운헤요", self.spec)
        assert result.verdict == RESULT_ALMOST and result.distance == 1

    def test_missingFinalConsonantIsAlmost(self):
        assert checkAnswer("개우해요", self.spec).verdict == RESULT_ALMOST

    def test_differentFormIsWrong(self):
        # Another form of the right word is "almost", flagged as a form slip.
        result = checkAnswer("개운했어요", self.spec)
        assert result.verdict == "almost" and result.formOnly
        assert checkAnswer("좋았어요", self.spec).verdict == RESULT_WRONG

    def test_blankIsWrong(self):
        assert checkAnswer("", self.spec).verdict == RESULT_WRONG

    def test_retypeMustBeExact(self):
        assert matchesForRetype("개운해요", self.spec)
        assert not matchesForRetype("개운헤요", self.spec)

    def test_letterNotesNameTheLetter(self):
        assert letterNotes("개운헤요", "개운해요") == ["헤 → 해 (vowel ㅔ → ㅐ)"]

    def test_diffMarksOnlyTheWrongSyllable(self):
        typed, answer = diffHtml("개운헤요", "개운해요")
        assert typed == '개운<span class="tp-bad">헤</span>요'
        assert answer == '개운<span class="tp-fix">해</span>요'


class TestHints(object):

    def test_ladder(self):
        assert hintText("개운해요", 0) == ""
        assert hintText("개운해요", 1) == "○○○○"
        assert hintText("개운해요", 2) == "ㄱㅇㅎㅇ"
        assert hintText("개운해요", 3) == "개○○○"

    def test_spacesAreKept(self):
        assert hintText("말도 안 돼요", 2) == "ㅁㄷ ㅇ ㄷㅇ"


class TestTypingStats(object):

    def test_peeksAreKeptApartFromMisses(self):
        stats = {}
        entry = persistence.recordTypingResult(stats, 7, 100, False, peeked=True)
        assert entry["missDays"] == [] and entry["peekDays"] == [100]
        assert entry["peeks"] == 1 and entry["streak"] == 0
        assert persistence.notesMissedSince(stats, 95) == [7]
        persistence.recordTypingResult(stats, 7, 101, False, peeked=True)
        assert persistence.notesWithMissDays(stats, 90, 2) == []

    def test_missesAreRecordedOncePerDay(self):
        stats = {}
        persistence.recordTypingResult(stats, 7, 100, False)
        persistence.recordTypingResult(stats, 7, 100, False)
        assert stats["7"]["missDays"] == [100] and stats["7"]["streak"] == 0

    def test_correctExtendsTheStreak(self):
        stats = {}
        persistence.recordTypingResult(stats, 7, 100, True)
        persistence.recordTypingResult(stats, 7, 101, True)
        assert stats["7"]["streak"] == 2

    def test_queries(self):
        stats = {}
        persistence.recordTypingResult(stats, 1, 90, False)
        persistence.recordTypingResult(stats, 1, 99, False)
        persistence.recordTypingResult(stats, 2, 99, False)
        persistence.recordTypingResult(stats, 3, 80, False)
        assert sorted(persistence.notesMissedSince(stats, 94)) == [1, 2]
        assert persistence.notesWithMissDays(stats, 87, 2) == [1]

    def test_saveAndLoadRoundTrip(self):
        stats = {}
        persistence.recordTypingResult(stats, 5, 10, False, word="개운해요")
        persistence.saveTypingStats(stats)
        assert persistence.loadTypingStats()["5"]["word"] == "개운해요"


class TestGroupedShuffle(object):

    def test_shuffleStaysInsideEachGroup(self):
        cards = [makeRow(["x"], cardId=i) for i in range(1, 21)]
        group = {c.cardId: (0 if c.cardId <= 10 else 1) for c in cards}
        session = StudySession(cards, shuffle=True, seed=4,
                               groupOf=lambda c: group[c.cardId])
        played = [session.current().cardId]
        while session.goNext():
            played.append(session.current().cardId)
        assert sorted(played[:10]) == list(range(1, 11))
        assert played[:10] != list(range(1, 11))


@pytest.fixture(name="recommendSetup")
def recommendSetupFixture(tmp_path):
    """A collection where each card qualifies for the list in a different way.

    1 pinned (not due) · 2 tagged use_trouble · 3 Again twice this week · 4 Again today
    5 due · 6 lapsed 4 times (not due) · 7 new · 8 answered today · 9 nothing · 10 typed wrong
    """
    crt = int(time.time()) - 100 * 86400
    today, nextDayAt = scheduling.schedTimingToday(crt, int(time.time()), 4)
    dayStartMs = scheduling.dayStartMilliseconds(nextDayAt)
    path = str(tmp_path / "collection.anki2")
    buildFakeCollection(path, crt, [
        (2, today + 5, 1), (2, today + 5, 1), (2, today + 5, 1), (1, today, 1),
        (2, today, 1), (2, today + 5, 1), (0, 0, 1), (2, today + 3, 1), (2, today + 9, 1),
        (2, today + 9, 1),
    ], noteTags=["review_pinned %s" % scheduling.pinDayTag(today), "use_trouble",
                 "", "", "", "", "", "", "", ""],
        reviewLog=[
            (3, dayStartMs - 3 * 86400000, 1), (3, dayStartMs - 86400000, 1),
            (4, dayStartMs + 60000, 1),
            (8, dayStartMs + 120000, 3),
        ])
    conn = sqlite3.connect(path)
    conn.execute("UPDATE cards SET lapses = 4 WHERE id = 6")
    conn.commit()
    conn.close()
    profile = Profile(name="R", root=str(tmp_path), collectionPath=path,
                      mediaPath=str(tmp_path / "collection.media"))
    stats = {}
    persistence.recordTypingResult(stats, 10, today - 1, False)
    return profile, today, dayStartMs, stats


class TestRecommendedList(object):

    def build(self, setup, **kwargs):
        profile, today, dayStartMs, stats = setup
        with CollectionSnapshot(profile) as snap:
            return buildRecommended(snap, None, today, dayStartMs,
                                    snap.deckNewAllowances(today), stats, **kwargs)

    def test_orderFollowsPriority(self, recommendSetup):
        rec = self.build(recommendSetup)
        assert [c.cardId for c in rec.cards] == [1, 2, 3, 4, 10, 5, 6, 7, 8]
        assert rec.reasons[1] == [REASON_PINNED]
        assert rec.reasons[3] == [REASON_TROUBLE]
        # Pressing Again today also counts as answered today; the urgent reason leads.
        assert rec.reasons[4] == [REASON_MISSED, REASON_REVIEWED]
        assert rec.reasons[10] == [REASON_TYPED]
        assert rec.reasons[5] == [REASON_DUE]
        assert rec.reasons[6] == [REASON_LEECH]
        assert rec.reasons[7] == [REASON_NEW]
        assert rec.reasons[8] == [REASON_REVIEWED]

    def test_cardWithNoReasonIsLeftOut(self, recommendSetup):
        assert 9 not in [c.cardId for c in self.build(recommendSetup).cards]

    def test_capTrimsTheLeastUrgent(self, recommendSetup):
        rec = self.build(recommendSetup, limit=3)
        assert [c.cardId for c in rec.cards] == [1, 2, 3]
        assert rec.total == 9
        assert "(top 3 of 9)" in summaryText(rec)

    def test_pinsCanBeLeftOut(self, recommendSetup):
        rec = self.build(recommendSetup, includePinned=False)
        assert 1 not in [c.cardId for c in rec.cards]

    def test_typedTroubleNeedsTwoDays(self, recommendSetup):
        profile, today, dayStartMs, stats = recommendSetup
        persistence.recordTypingResult(stats, 10, today, False)
        rec = self.build((profile, today, dayStartMs, stats))
        assert rec.reasons[10][0] == REASON_TROUBLE

    def test_againDuringFirstLearningStepsIsNotTrouble(self, recommendSetup):
        profile, today, dayStartMs, stats = recommendSetup
        conn = sqlite3.connect(profile.collectionPath)
        for offset in (2, 3):
            # revlog.type 0 = a new card's learning steps.
            conn.execute("INSERT INTO revlog VALUES (?, 9, 1, 0)",
                         (dayStartMs - offset * 86400000 + 5000,))
        conn.commit()
        conn.close()
        assert 9 not in [c.cardId for c in self.build(recommendSetup).cards]
