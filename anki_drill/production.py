"""
production.py
Typed-answer ("production") drilling: build a prompt that asks for Korean, then check what
the user typed.

Everything here is pure logic with no Qt, so it is fully unit-testable:

- `buildTypingSpec` reads a note's fields and decides what to ask. The preferred shape is a
  sentence gap: the card's example sentence with the target word (plus its ending or
  particle) blanked out, so the answer tests conjugation as well as the word itself.
- `checkAnswer` compares at the level of Hangul letters (jamo), so one wrong vowel or final
  consonant reads as "almost" instead of plain wrong.
- `hintText` gives the three-step hint ladder: syllable count, initial consonants, first
  syllable.
"""

import difflib
import html
import re
import unicodedata
from collections import namedtuple

from .rendering import AV_TAG_RE, replaceAvTags, stripHtml

# ---------------------------------------------------------------- Hangul

_SYLLABLE_BASE = 0xAC00
_SYLLABLE_LAST = 0xD7A3
_INITIALS = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_MEDIALS = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_FINALS = ("", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ",
           "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ")

HANGUL_RE = re.compile("[가-힣ㄱ-ㆎ]")

# Punctuation and spacing that never decide whether an answer is right.
_IGNORED_RE = re.compile(r"[\s.,!?~…·'\"“”‘’()\[\]{}:;\-–—/]+")


def isSyllable(char):
    """True for a precomposed Hangul syllable such as 한."""
    return _SYLLABLE_BASE <= ord(char) <= _SYLLABLE_LAST


def syllableParts(char):
    """Return (initial, medial, final) for a syllable, or None for anything else."""
    if not isSyllable(char):
        return None
    offset = ord(char) - _SYLLABLE_BASE
    return (_INITIALS[offset // 588], _MEDIALS[(offset % 588) // 28], _FINALS[offset % 28])


def toJamo(text):
    """Spell `text` out letter by letter: 개운 -> ㄱㅐㅇㅜㄴ. Non-Hangul passes through."""
    out = []
    for char in text:
        parts = syllableParts(char)
        out.append("".join(parts) if parts else char)
    return "".join(out)


def hasHangul(text):
    """True when `text` contains any Korean letter."""
    return bool(HANGUL_RE.search(text or ""))


def normalise(text):
    """Canonical form for comparison: NFC, no HTML, no spacing or punctuation."""
    text = unicodedata.normalize("NFC", stripHtml(text or ""))
    return _IGNORED_RE.sub("", text)


def editDistance(left, right):
    """Plain Levenshtein distance between two strings."""
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


# ---------------------------------------------------------------- reading a note

TypingSpec = namedtuple(
    "TypingSpec",
    "mode prompt sentenceHtml translation answer accepted fullSentence notes sounds "
    "gapAnswer nearMiss")
TypingSpec.__new__.__defaults__ = ("", ())
# mode: "gap" (type the blanked part of a sentence), "word" (type the word itself) or
#   "english" (see the Korean, type the meaning).
# accepted: every normalised string that counts as correct.
# gapAnswer: the form that fills the example sentence, when one was found.
# nearMiss: normalised answers that are the right word in the wrong form ("almost").

MODE_GAP = "gap"
MODE_WORD = "word"
MODE_ENGLISH = "english"

# What a typing card asks for.
ANSWER_DICTIONARY = "dictionary"   # the Korean word as written on the card
ANSWER_SENTENCE = "sentence"       # the conjugated form that fills the example's gap
ANSWER_ENGLISH = "english"         # the meaning, from the Korean
ANSWER_FORMS = (ANSWER_DICTIONARY, ANSWER_SENTENCE, ANSWER_ENGLISH)

# Field names tried for each role, most specific first. Matching ignores case.
_ANSWER_FIELDS = ("chunk", "front", "korean", "vocab", "korean word", "한국어", "word")
_PROMPT_FIELDS = ("prompt", "back", "english", "vocab-english", "english meaning",
                  "meaning")
_SENTENCE_FIELDS = ("sentence", "example", "example korean", "spoken example",
                    "written example")
_TRANSLATION_FIELDS = ("translation", "example english", "sentence-english")
_NOTES_FIELDS = ("note", "notes", "context/usage notes")
_FULL_FIELDS = ("full",)

# A gap someone already marked up in the sentence (the "Korean Type" notetype does this).
_MARKED_GAP_RE = re.compile(r'<span[^>]*class="gap"[^>]*>.*?</span>', re.S | re.I)
_BOLD_RE = re.compile(r"<b>(.*?)</b>", re.S | re.I)
_CONJ_RE = re.compile(r"Conj:\s*(.*?)(?:<br\s*/?>|\n|$)", re.I)
_GAP_HTML = '<span class="tp-gap">&nbsp;</span>'


def _field(fields, names):
    """Return the first non-empty field whose name is in `names` (case-insensitive)."""
    lowered = {name.lower(): value for name, value in fields.items()}
    for name in names:
        value = lowered.get(name)
        if value is not None and stripHtml(value):
            return value
    return ""


def _soundsIn(values):
    """Every [sound:...] reference across `values`, in order, without duplicates."""
    seen = []
    for value in values:
        for match in AV_TAG_RE.finditer(value or ""):
            if match.group(0) not in seen:
                seen.append(match.group(0))
    return seen


def _plain(text):
    """Text with sounds and markup stripped, keeping inner spacing tidy."""
    return re.sub(r"\s+", " ", stripHtml(text or "")).strip()


def conjugationsFromNotes(notes):
    """Forms listed on a 'Conj: 개운해요 · 개운했어요 · 개운한 N' line, as plain strings."""
    match = _CONJ_RE.search(notes or "")
    if not match:
        return []
    forms = []
    for piece in re.split(r"[·,/]", stripHtml(match.group(1))):
        piece = re.sub(r"\(.*?\)", "", piece)
        # "개운한 N" means "개운한 + a noun"; the placeholder is not part of the answer.
        piece = re.sub(r"\s+(N|V|A|NOUN)\b", "", piece).strip()
        if hasHangul(piece):
            forms.append(piece)
    return forms


def _stem(word):
    """Dictionary form minus 다, which is what conjugated forms share: 개운하다 -> 개운하."""
    word = word.strip()
    if len(word) > 1 and word.endswith("다"):
        return word[:-1]
    return word


_TRAILING_PUNCT_RE = re.compile(r"[.,!?~…'\"”’)\]]+$")
_LEADING_PUNCT_RE = re.compile(r"^[\"“‘(\[]+")


# Syllables a particle or verb ending can start with. A one-syllable stem (가다, 집) is only
# matched when what follows it looks like one of these, so 가다 does not find 가방.
_ENDING_STARTS = frozenset("아어여요서고는은을를이가에의도만로와과랑면세시지게기니자라던야겠죠네군")
# Final consonants an ending can fuse onto a bare stem syllable: 가 -> 갔/간/갈/갑니다.
_FUSED_FINALS = frozenset(["ㅆ", "ㄴ", "ㄹ", "ㅂ", "ㅁ"])


def _oneSyllableMatch(stem, token):
    """Whether `token` is plausibly the one-syllable `stem` plus an ending or particle."""
    token = token.replace(" ", "")
    if not token:
        return False
    first = token[0]
    if first == stem:
        return len(token) == 1 or token[1] in _ENDING_STARTS
    stemParts, firstParts = syllableParts(stem), syllableParts(first)
    if not stemParts or not firstParts:
        return False
    return (firstParts[:2] == stemParts[:2] and stemParts[2] == ""
            and firstParts[2] in _FUSED_FINALS)


def findGap(word, sentence):
    """Locate `word`, in whatever form it takes, inside `sentence`.

    Returns (start, end) character offsets in the plain sentence covering the whole words
    (eojeol) that hold it, trailing punctuation excluded, or None when it is not there.
    A conjugated or irregular form is matched on its letters: 들뜨다 finds 들떠요 because
    the two share ㄷㅡㄹㄸ before they diverge.
    """
    word = _plain(word)
    if not hasHangul(word) or not hasHangul(sentence):
        return None
    pieces = word.split()
    stemText = _stem(word).replace(" ", "")
    target = toJamo(stemText)
    if len(target) < 2:
        return None

    tokens = [(m.start(), m.end()) for m in re.finditer(r"\S+", sentence)]
    count = len(pieces)
    best = None
    for index in range(len(tokens) - count + 1):
        window = tokens[index:index + count]
        start, end = window[0][0], window[-1][1]
        text = sentence[start:end]
        lead = _LEADING_PUNCT_RE.match(text)
        if lead:
            start += lead.end()
        trail = _TRAILING_PUNCT_RE.search(sentence[start:end])
        if trail:
            end = start + trail.start()
        if len(stemText) == 1 and not _oneSyllableMatch(stemText, sentence[start:end]):
            continue
        candidate = toJamo(sentence[start:end].replace(" ", ""))
        shared = 0
        for a, b in zip(target, candidate):
            if a != b:
                break
            shared += 1
        # Most of the stem must line up from the start; 3 letters minimum stops one-syllable
        # coincidences (가 in 가방) from counting.
        needed = max(min(3, len(target)), int(len(target) * 0.7 + 0.999))
        if shared >= needed and (best is None or shared > best[0]):
            best = (shared, start, end)
    if best is None:
        return None
    return best[1], best[2]


def buildTypingSpec(fieldValues, dictionaryForm=False, answerForm=None):
    """Decide what to ask for a note, or return None when it has nothing typeable.

    `fieldValues` maps field name to raw field HTML. `answerForm` is one of ANSWER_FORMS
    (`dictionaryForm=True` is shorthand for ANSWER_DICTIONARY; the default is the sentence
    gap). Answers are forgiving both ways: in dictionary mode the sentence's conjugated
    form also counts, and in sentence mode the dictionary form reads as "almost".
    A note without a usable example sentence always asks for the word itself.
    """
    if answerForm is None:
        answerForm = ANSWER_DICTIONARY if dictionaryForm else ANSWER_SENTENCE
    answerField = _field(fieldValues, _ANSWER_FIELDS)
    promptField = _field(fieldValues, _PROMPT_FIELDS)
    # Front/Back decks are sometimes the other way round; the Korean side is the answer.
    if not hasHangul(answerField) and hasHangul(promptField):
        answerField, promptField = promptField, answerField
    if not hasHangul(answerField):
        answerField = next((v for v in fieldValues.values() if hasHangul(_plain(v))), "")
    if not hasHangul(answerField):
        return None

    sentence = _field(fieldValues, _SENTENCE_FIELDS)
    translation = _plain(_field(fieldValues, _TRANSLATION_FIELDS))
    notes = _field(fieldValues, _NOTES_FIELDS)
    full = _field(fieldValues, _FULL_FIELDS)
    sounds = _soundsIn(list(fieldValues.values()))
    answer = _plain(answerField)
    prompt = promptField
    plainSentence = _plain(sentence)

    if answerForm == ANSWER_ENGLISH:
        return _englishSpec(answer, promptField, plainSentence, translation, notes, sounds)

    # Find the sentence gap, if there is one: (gappedHtml, gapAnswer, fullSentence).
    gap = None
    if sentence and _MARKED_GAP_RE.search(sentence):
        # 1. The sentence already carries a marked-up gap (Korean Type notes).
        gapped = _MARKED_GAP_RE.sub(_GAP_HTML, AV_TAG_RE.sub("", sentence), count=1)
        fullSentence = _plain(full) or _plain(_MARKED_GAP_RE.sub(answer, sentence, count=1))
        gap = (gapped, answer, fullSentence)
    elif plainSentence and hasHangul(plainSentence):
        # 2. A bold part marks the chunk explicitly (listening sentence cards).
        bold = _BOLD_RE.search(sentence)
        span = None
        if bold and hasHangul(stripHtml(bold.group(1))):
            boldText = _plain(bold.group(1))
            position = plainSentence.find(boldText)
            if position >= 0:
                span = (position, position + len(boldText))
        # 3. Otherwise find the word's conjugated form in the sentence.
        if span is None:
            span = findGap(answer, plainSentence)
        if span is not None:
            start, end = span
            gapped = (html.escape(plainSentence[:start]) + _GAP_HTML
                      + html.escape(plainSentence[end:]))
            gap = (gapped, plainSentence[start:end], plainSentence)

    wordForms = [normalise(answer)] + [normalise(f) for f in conjugationsFromNotes(notes)]

    if gap is not None and answerForm == ANSWER_SENTENCE:
        gapped, gapAnswer, fullSentence = gap
        nearMiss = [f for f in dict.fromkeys(wordForms) if f and f != normalise(gapAnswer)]
        return TypingSpec(MODE_GAP, prompt, gapped, translation, gapAnswer,
                          [normalise(gapAnswer)], fullSentence, notes, sounds,
                          gapAnswer, nearMiss)

    # 4. Type the word. Listed conjugations, and the sentence's own form, also count.
    gapAnswer = gap[1] if gap is not None else ""
    fullSentence = gap[2] if gap is not None else plainSentence
    accepted = wordForms + [normalise(gapAnswer)]
    return TypingSpec(MODE_WORD, prompt, "", translation, answer,
                      list(dict.fromkeys(a for a in accepted if a)),
                      fullSentence, notes, sounds, gapAnswer, ())


# ---------------------------------------------------------------- English direction

_ENGLISH_FILLER_RE = re.compile(r"^(to|a|an|the|be)\s+", re.I)
_PARENS_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")


def normaliseEnglish(text):
    """Lower-case letters and digits only, leading "to"/"a"/"the" dropped."""
    text = unicodedata.normalize("NFKC", stripHtml(text or "")).lower().strip()
    text = _PARENS_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    previous = None
    while previous != text:
        previous, text = text, _ENGLISH_FILLER_RE.sub("", text)
    return text.replace(" ", "")


def englishMeanings(text):
    """Split a meaning field like "get motion sick, carsick; (to) feel ill" into options."""
    plain = _plain(text)
    pieces = re.split(r"[,;/]|\bor\b", _PARENS_RE.sub(lambda m: m.group(0).replace(",", " "),
                                                         plain))
    whole = normaliseEnglish(plain)
    found = [normaliseEnglish(p) for p in pieces] + [whole]
    return list(dict.fromkeys(f for f in found if f))


def _englishSpec(korean, meaning, sentence, translation, notes, sounds):
    """Korean → English: show the word, type its meaning."""
    meaning = _plain(meaning)
    accepted = englishMeanings(meaning)
    if not accepted:
        return None
    return TypingSpec(MODE_ENGLISH, korean, "", translation, meaning, accepted,
                      sentence, notes, sounds, "", ())


# ---------------------------------------------------------------- checking

RESULT_CORRECT = "correct"
RESULT_ALMOST = "almost"
RESULT_WRONG = "wrong"

CheckResult = namedtuple("CheckResult", "verdict typed answer distance formOnly")
CheckResult.__new__.__defaults__ = (False,)
# formOnly: an "almost" because the right word was typed in the wrong form.


def _normaliseFor(spec, text):
    """The comparison form of `text` for this kind of card."""
    return normaliseEnglish(text) if spec.mode == MODE_ENGLISH else normalise(text)


def checkAnswer(typed, spec):
    """Grade `typed` against `spec`: correct, almost (one letter off) or wrong.

    Distances are counted in jamo, so 개운헤요 for 개운해요 is one letter (ㅔ for ㅐ) away.
    Longer answers are allowed one more slip before "almost" becomes "wrong".
    """
    given = _normaliseFor(spec, typed)
    if not given:
        return CheckResult(RESULT_WRONG, typed, spec.answer, None)
    if given in spec.accepted:
        return CheckResult(RESULT_CORRECT, typed, spec.answer, 0)
    if given in spec.nearMiss:
        return CheckResult(RESULT_ALMOST, typed, spec.answer, None, True)
    if spec.mode == MODE_ENGLISH:
        # Letters, not jamo; a little more slack, since spelling is not the point here.
        distance = min(editDistance(given, a) for a in spec.accepted)
        allowance = 1 if len(given) < 6 else 2
        verdict = RESULT_ALMOST if distance <= allowance else RESULT_WRONG
        return CheckResult(verdict, typed, spec.answer, distance)
    givenJamo = toJamo(given)
    distance = min(editDistance(givenJamo, toJamo(a)) for a in spec.accepted)
    allowance = 1 if len(toJamo(spec.accepted[0])) < 12 else 2
    verdict = RESULT_ALMOST if distance <= allowance else RESULT_WRONG
    return CheckResult(verdict, typed, spec.answer, distance)


def matchesForRetype(typed, spec):
    """True when a retyped answer is exactly right (spacing and punctuation aside)."""
    return _normaliseFor(spec, typed) in spec.accepted


def letterNotes(typed, answer):
    """Explain syllable swaps at letter level, e.g. ['헤 → 해 (ㅔ → ㅐ)'].

    Only same-length replaced runs are explained; insertions and deletions are already
    obvious from the highlighted comparison.
    """
    left, right = normalise(typed), normalise(answer)
    notes = []
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op != "replace" or (i2 - i1) != (j2 - j1):
            continue
        for a, b in zip(left[i1:i2], right[j1:j2]):
            partsA, partsB = syllableParts(a), syllableParts(b)
            if not partsA or not partsB:
                notes.append("%s → %s" % (a, b))
                continue
            labels = ("first consonant", "vowel", "final consonant")
            diffs = []
            for label, x, y in zip(labels, partsA, partsB):
                if x != y:
                    diffs.append("%s %s → %s" % (label, x or "none", y or "none"))
            notes.append("%s → %s (%s)" % (a, b, ", ".join(diffs)))
    return notes


def diffHtml(typed, answer):
    """Return (typedHtml, answerHtml) with the differing syllables marked up.

    Spacing and punctuation are compared away, so the markup is over the normalised text.
    """
    left, right = normalise(typed), normalise(answer)
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    typedOut, answerOut = [], []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        a, b = html.escape(left[i1:i2]), html.escape(right[j1:j2])
        if op == "equal":
            typedOut.append(a)
            answerOut.append(b)
            continue
        if a:
            typedOut.append('<span class="tp-bad">%s</span>' % a)
        if b:
            answerOut.append('<span class="tp-fix">%s</span>' % b)
    return "".join(typedOut), "".join(answerOut)


# ---------------------------------------------------------------- hints

MAX_HINT_LEVEL = 3


def hintText(answer, level):
    """The hint at `level` (1-3) for `answer`; '' at level 0.

    1: one ○ per syllable, spacing kept.  2: initial consonants (초성), e.g. ㄱㅇㅎㅇ.
    3: first syllable, the rest as ○.
    """
    if level <= 0:
        return ""
    answer = _plain(answer)
    if not hasHangul(answer):
        return _englishHint(answer, level)
    out = []
    for position, char in enumerate(answer):
        parts = syllableParts(char)
        if char.isspace():
            out.append(" ")
        elif level >= 3 and position == 0:
            out.append(char)
        elif level == 2 and parts:
            out.append(parts[0])
        elif parts or char.isalnum():
            out.append("○")
        else:
            out.append(char)
    return "".join(out)


def _englishHint(answer, level):
    """1: one ○ per letter.  2: first letter of each word.  3: the first word."""
    first = re.split(r"[,;/]", answer)[0].strip()
    words = first.split()
    out = []
    for index, word in enumerate(words):
        if level >= 3 and index == 0:
            out.append(word)
        elif level >= 2:
            out.append(word[0] + "○" * (len(word) - 1))
        else:
            out.append("○" * len(word))
    return " ".join(out)


# ---------------------------------------------------------------- card faces

def promptHtml(spec, hintLevel=0):
    """HTML for the question face of a typing card. Carries no audio, which would leak."""
    if spec.mode == MODE_ENGLISH:
        parts = ['<div class="tp-card">', '<div class="tp-tag">type the English</div>',
                 '<div class="tp-prompt" lang="ko">%s</div>' % html.escape(spec.prompt)]
        hint = hintText(spec.answer, hintLevel)
        if hint:
            parts.append('<div class="tp-hint">hint: %s</div>' % html.escape(hint))
        parts.append("</div>")
        return "".join(parts)
    parts = ['<div class="tp-card">', '<div class="tp-tag">type the Korean</div>']
    if spec.prompt:
        parts.append('<div class="tp-prompt">%s</div>' % AV_TAG_RE.sub("", spec.prompt))
    if spec.mode == MODE_GAP:
        parts.append('<div class="tp-sentence">%s</div>' % spec.sentenceHtml)
        if spec.translation:
            parts.append('<div class="tp-translation">%s</div>'
                         % html.escape(spec.translation))
    hint = hintText(spec.answer, hintLevel)
    if hint:
        parts.append('<div class="tp-hint">hint: %s</div>' % html.escape(hint))
    parts.append("</div>")
    return "".join(parts)


_VERDICT_LABELS = {
    RESULT_CORRECT: ("tp-ok", "✓ Correct"),
    RESULT_ALMOST: ("tp-almost", "≈ Almost: one letter off"),
    RESULT_WRONG: ("tp-wrong", "✕ Not quite"),
}


def resultHtml(spec, result, hintLevel=0):
    """HTML for the answer face: verdict, comparison, the full sentence and its audio."""
    css, label = _VERDICT_LABELS[result.verdict]
    if getattr(result, "formOnly", False):
        label = "≈ Right word: now put it in the sentence's form"
    elif result.verdict == RESULT_ALMOST and spec.mode == MODE_ENGLISH:
        label = "≈ Almost: a spelling slip"
    if result.verdict == RESULT_CORRECT and hintLevel:
        label += " (with a hint)"
    parts = ['<div class="tp-card">', '<div class="tp-verdict %s">%s</div>' % (css, label)]
    if result.verdict != RESULT_CORRECT and spec.mode == MODE_ENGLISH:
        parts.append('<div class="tp-compare"><div><span class="tp-key">you</span> %s</div>'
                     '<div><span class="tp-key">answer</span> %s</div></div>'
                     % (html.escape(result.typed) or "<i>(blank)</i>",
                        html.escape(spec.answer)))
    elif result.verdict != RESULT_CORRECT:
        typedHtml, answerHtml = diffHtml(result.typed, spec.answer)
        parts.append('<div class="tp-compare"><div><span class="tp-key">you</span> %s</div>'
                     '<div><span class="tp-key">answer</span> %s</div></div>'
                     % (typedHtml or "<i>(blank)</i>", answerHtml))
        notes = letterNotes(result.typed, spec.answer)
        if notes:
            parts.append('<div class="tp-letters">%s</div>'
                         % "<br>".join(html.escape(n) for n in notes))
    else:
        parts.append('<div class="tp-answer">%s</div>' % html.escape(spec.answer))

    if spec.fullSentence:
        sentence = html.escape(spec.fullSentence)
        # The form the word takes in the sentence, bolded: in dictionary mode this is where
        # the conjugation is shown without being tested.
        bold = spec.gapAnswer or (spec.answer if spec.mode == MODE_GAP else "")
        if bold:
            sentence = sentence.replace(html.escape(bold), "<b>%s</b>" % html.escape(bold), 1)
        parts.append('<div class="tp-sentence">%s</div>' % sentence)
    if spec.translation:
        parts.append('<div class="tp-translation">%s</div>' % html.escape(spec.translation))
    if result.verdict == RESULT_WRONG:
        parts.append('<div class="tp-retype">Copy it to practise, or Enter to move on. '
                     'Esc: try again.</div>')
    if spec.notes:
        parts.append('<div class="tp-notes">%s</div>' % AV_TAG_RE.sub("", spec.notes))
    if spec.sounds:
        parts.append('<div class="tp-sounds">%s</div>' % replaceAvTags("".join(spec.sounds)))
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------- session summary

def typingSummary(log, top=5):
    """Plain-text stats for the typing done this session.

    `log` maps card id to {"word", "tries", "misses", "peeks", "firstTry"}, where
    firstTry is the first attempt's outcome: "correct", "hint", "almost", "wrong" or
    "peek". Returns '' when nothing was typed.
    """
    entries = [e for e in (log or {}).values() if e.get("tries")]
    if not entries:
        return ""
    typed = len(entries)
    firstRight = sum(1 for e in entries if e.get("firstTry") == "correct")
    tries = sum(e["tries"] for e in entries)
    lines = ["Typing: %d of %d right first try (%d%%)  ·  %.1f tries per card"
             % (firstRight, typed, round(100.0 * firstRight / typed), float(tries) / typed)]
    peeks = sum(e.get("peeks", 0) for e in entries)
    if peeks:
        lines.append("Peeked at the answer %d time%s" % (peeks, "" if peeks == 1 else "s"))
    missed = sorted((e for e in entries if e.get("misses", 0) + e.get("peeks", 0)),
                    key=lambda e: (-(e.get("misses", 0) + e.get("peeks", 0)), e["word"]))
    if missed:
        lines.append("Most missed: " + ", ".join(
            "%s ×%d" % (e["word"], e.get("misses", 0) + e.get("peeks", 0))
            for e in missed[:top]))
    return "\n".join(lines)
