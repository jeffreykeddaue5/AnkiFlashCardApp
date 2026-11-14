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
    "mode prompt sentenceHtml translation answer accepted fullSentence notes sounds")
# mode: "gap" (type the blanked part of a sentence) or "word" (type the word itself).
# accepted: every normalised string that counts as correct.

MODE_GAP = "gap"
MODE_WORD = "word"

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


def buildTypingSpec(fieldValues):
    """Decide what to ask for a note, or return None when it has nothing typeable.

    `fieldValues` maps field name to raw field HTML. A sentence gap is preferred; a note
    without a usable example sentence falls back to typing the word itself.
    """
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

    # 1. The sentence already carries a marked-up gap (Korean Type notes).
    if sentence and _MARKED_GAP_RE.search(sentence):
        gapped = _MARKED_GAP_RE.sub(_GAP_HTML, AV_TAG_RE.sub("", sentence), count=1)
        fullSentence = _plain(full) or _plain(_MARKED_GAP_RE.sub(answer, sentence, count=1))
        return TypingSpec(MODE_GAP, prompt, gapped, translation, answer,
                          [normalise(answer)], fullSentence, notes, sounds)

    plainSentence = _plain(sentence)
    if plainSentence and hasHangul(plainSentence):
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
            gapAnswer = plainSentence[start:end]
            gapped = (html.escape(plainSentence[:start]) + _GAP_HTML
                      + html.escape(plainSentence[end:]))
            return TypingSpec(MODE_GAP, prompt, gapped, translation, gapAnswer,
                              [normalise(gapAnswer)], plainSentence, notes, sounds)

    # 4. No sentence to work with: type the word. Listed conjugations also count.
    accepted = [normalise(answer)] + [normalise(f) for f in conjugationsFromNotes(notes)]
    return TypingSpec(MODE_WORD, prompt, "", translation, answer,
                      list(dict.fromkeys(a for a in accepted if a)),
                      plainSentence, notes, sounds)


# ---------------------------------------------------------------- checking

RESULT_CORRECT = "correct"
RESULT_ALMOST = "almost"
RESULT_WRONG = "wrong"

CheckResult = namedtuple("CheckResult", "verdict typed answer distance")


def checkAnswer(typed, spec):
    """Grade `typed` against `spec`: correct, almost (one letter off) or wrong.

    Distances are counted in jamo, so 개운헤요 for 개운해요 is one letter (ㅔ for ㅐ) away.
    Longer answers are allowed one more slip before "almost" becomes "wrong".
    """
    given = normalise(typed)
    if not given:
        return CheckResult(RESULT_WRONG, typed, spec.answer, None)
    if given in spec.accepted:
        return CheckResult(RESULT_CORRECT, typed, spec.answer, 0)
    givenJamo = toJamo(given)
    distance = min(editDistance(givenJamo, toJamo(a)) for a in spec.accepted)
    allowance = 1 if len(toJamo(spec.accepted[0])) < 12 else 2
    verdict = RESULT_ALMOST if distance <= allowance else RESULT_WRONG
    return CheckResult(verdict, typed, spec.answer, distance)


def matchesForRetype(typed, spec):
    """True when a retyped answer is exactly right (spacing and punctuation aside)."""
    return normalise(typed) in spec.accepted


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


# ---------------------------------------------------------------- card faces

def promptHtml(spec, hintLevel=0):
    """HTML for the question face of a typing card. Carries no audio, which would leak."""
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
    if result.verdict == RESULT_CORRECT and hintLevel:
        label += " (with a hint)"
    parts = ['<div class="tp-card">', '<div class="tp-verdict %s">%s</div>' % (css, label)]
    if result.verdict != RESULT_CORRECT:
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
        if spec.mode == MODE_GAP:
            sentence = sentence.replace(html.escape(spec.answer),
                                        "<b>%s</b>" % html.escape(spec.answer), 1)
        parts.append('<div class="tp-sentence">%s</div>' % sentence)
    if spec.translation:
        parts.append('<div class="tp-translation">%s</div>' % html.escape(spec.translation))
    if result.verdict == RESULT_WRONG:
        parts.append('<div class="tp-retype">Type the answer once to move on.</div>')
    if spec.notes:
        parts.append('<div class="tp-notes">%s</div>' % AV_TAG_RE.sub("", spec.notes))
    if spec.sounds:
        parts.append('<div class="tp-sounds">%s</div>' % replaceAvTags("".join(spec.sounds)))
    parts.append("</div>")
    return "".join(parts)
