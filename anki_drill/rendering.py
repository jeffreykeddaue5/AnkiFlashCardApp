"""
rendering.py
Turns Anki notes into the two faces of a flip card, and builds the HTML shell that shows them.

Schema 18 stores notetype and template settings as protobuf blobs rather than JSON, so this
module carries a tiny protobuf field scanner instead of depending on the `anki` package.
"""

import html
import json
import re

# Protobuf field numbers we care about (anki/proto/notetypes.proto).
_TEMPLATE_QFMT = 1
_TEMPLATE_AFMT = 2
_NOTETYPE_KIND = 1
_NOTETYPE_CSS = 3
_NOTETYPE_KIND_CLOZE = 1

# Anki's conventional divider between the repeated front side and the real answer.
ANSWER_DIVIDER_RE = re.compile(r"<hr[^>]*id\s*=\s*[\"']?answer[\"']?[^>]*>", re.I)

REPLACEMENT_RE = re.compile(r"\{\{([^{}]+)\}\}")
CONDITIONAL_RE = re.compile(r"\{\{([#^])([^}]+)\}\}(.*?)\{\{/\2\}\}", re.S)
CLOZE_RE = re.compile(r"\{\{c(\d+)::(.*?)(?:::(.*?))?\}\}", re.S)
AV_TAG_RE = re.compile(r"\[sound:([^\]]*)\]|\[anki:play:[^\]]*\]", re.I)
HTML_TAG_RE = re.compile(r"<[^>]+>")

# Filters we recognise but deliberately pass straight through.
_PASSTHROUGH_FILTERS = frozenset(["furigana", "kana", "kanji", "kanjireading", "cq", "ca"])


def _readVarint(buf, index):
    """Read a base-128 varint from `buf` at `index`, returning (value, nextIndex)."""
    shift = 0
    value = 0
    while index < len(buf):
        byte = buf[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7
    raise ValueError("truncated varint")


def parseProtoBlob(blob):
    """Scan a flat protobuf message into {fieldNumber: [rawValue, ...]}.

    Length-delimited fields come back as bytes and varints as ints; that is all the notetype
    and template messages need. Scanning stops at the first byte we cannot interpret, which
    keeps an unknown future field from losing the fields already read.
    """
    if not blob:
        return {}
    if isinstance(blob, str):
        blob = blob.encode("utf-8", "replace")

    fields = {}
    index = 0
    try:
        while index < len(blob):
            key, index = _readVarint(blob, index)
            fieldNumber, wireType = key >> 3, key & 0x07
            if wireType == 0:
                value, index = _readVarint(blob, index)
            elif wireType == 1:
                value, index = blob[index:index + 8], index + 8
            elif wireType == 2:
                length, index = _readVarint(blob, index)
                value, index = blob[index:index + length], index + length
            elif wireType == 5:
                value, index = blob[index:index + 4], index + 4
            else:
                break
            fields.setdefault(fieldNumber, []).append(value)
    except (ValueError, IndexError):
        pass
    return fields


def _protoString(fields, number):
    """Return field `number` decoded as UTF-8, or '' when absent."""
    values = fields.get(number)
    if not values or not isinstance(values[0], bytes):
        return ""
    return values[0].decode("utf-8", "replace")


def templateConfig(blob):
    """Return (questionFormat, answerFormat) from a schema-18 template config blob."""
    fields = parseProtoBlob(blob)
    return _protoString(fields, _TEMPLATE_QFMT), _protoString(fields, _TEMPLATE_AFMT)


def notetypeConfig(blob):
    """Return (css, isCloze) from a schema-18 notetype config blob."""
    fields = parseProtoBlob(blob)
    kinds = fields.get(_NOTETYPE_KIND) or [0]
    isCloze = kinds[0] == _NOTETYPE_KIND_CLOZE
    return _protoString(fields, _NOTETYPE_CSS), isCloze


def stripHtml(text):
    """Return `text` with tags and AV references removed, for text-only filters."""
    return html.unescape(HTML_TAG_RE.sub("", AV_TAG_RE.sub("", text))).strip()


def replaceAvTags(text):
    """Swap AV references for a speaker glyph; [sound:...] ones carry the file to play.

    The shell autoplays every data-sound glyph on the face that comes up, and plays a single
    one when it is clicked. [anki:play:...] refers to Anki's own TTS, which has no file here.
    """

    def substitute(match):
        name = match.group(1)
        if not name:
            return '<span class="av-tag" title="audio on this card">&#128266;</span>'
        return ('<span class="av-tag" data-sound="%s" title="play audio">&#128266;</span>'
                % html.escape(name.strip(), quote=True))

    return AV_TAG_RE.sub(substitute, text)


def renderCloze(text, clozeOrd, revealed):
    """Resolve {{cN::...}} deletions in `text` for card ordinal `clozeOrd`.

    On the question side the target deletion collapses to its hint (or '[...]'); on the
    answer side it is shown highlighted. Deletions belonging to other cards always show.
    """
    target = clozeOrd + 1

    def substitute(match):
        ordinal = int(match.group(1))
        answer = match.group(2)
        hint = match.group(3)
        if ordinal != target:
            return answer
        if revealed:
            return '<span class="cloze">%s</span>' % answer
        return '<span class="cloze-blank">[%s]</span>' % (hint if hint else "&hellip;")

    return CLOZE_RE.sub(substitute, text)


def _resolveConditionals(template, fieldValues):
    """Expand {{#Field}}/{{^Field}} sections, innermost first, until none remain."""

    def substitute(match):
        kind, name, body = match.group(1), match.group(2).strip(), match.group(3)
        filled = bool(stripHtml(fieldValues.get(name, "")))
        keep = filled if kind == "#" else not filled
        return body if keep else ""

    # Each pass strips one nesting level; the cap stops a malformed template looping forever.
    for _ in range(10):
        expanded = CONDITIONAL_RE.sub(substitute, template)
        if expanded == template:
            break
        template = expanded
    return template


def renderSide(template, context):
    """Render one side of a card template against a RenderContext-style dict.

    `context` supplies fieldValues, tags, deckName, notetypeName, cardName, clozeOrd,
    isCloze, revealed and frontSide.
    """
    fieldValues = context["fieldValues"]
    output = _resolveConditionals(template, fieldValues)

    def substitute(match):
        token = match.group(1).strip()
        if not token or token.startswith("!"):
            return ""

        parts = [p.strip() for p in token.split(":")]
        name = parts[-1]
        filters = parts[:-1]

        if name == "FrontSide":
            return context.get("frontSide") or ""
        if name == "Tags":
            return html.escape(context.get("tags", ""))
        if name == "Type":
            return html.escape(context.get("notetypeName", ""))
        if name == "Card":
            return html.escape(context.get("cardName", ""))
        if name in ("Deck", "Subdeck"):
            deck = context.get("deckName", "")
            if name == "Subdeck":
                deck = deck.split("::")[-1]
            return html.escape(deck)

        if name not in fieldValues:
            return '<span class="tmpl-error">{{%s}}</span>' % html.escape(token)

        value = fieldValues[name]
        # A cloze notetype runs every field through cloze resolution; the explicit
        # {{cloze:Field}} filter does the same on an otherwise-normal notetype.
        if "cloze" in filters or context.get("isCloze"):
            value = renderCloze(value, context.get("clozeOrd", 0), context.get("revealed", False))
        if "text" in filters:
            return html.escape(stripHtml(value))
        if "type" in filters:
            return '<span class="type-prompt">[type the answer]</span>'
        if "hint" in filters:
            if not stripHtml(value):
                return ""
            return ('<details class="hint"><summary>%s</summary>%s</details>'
                    % (html.escape(name), value))
        return value

    return replaceAvTags(REPLACEMENT_RE.sub(substitute, output))


def buildContext(row, notetype, template, revealed):
    """Assemble the render context dict for one card row."""
    names = notetype.fields or []
    values = list(row.fields)
    # Notes are stored as a flat 0x1f-separated string; pad or trim to the notetype's shape.
    values += [""] * max(0, len(names) - len(values))
    fieldValues = dict(zip(names, values))

    return {
        "fieldValues": fieldValues,
        "tags": row.tags.strip(),
        "deckName": row.deckName,
        "notetypeName": notetype.name,
        "cardName": template.name if template else "",
        "clozeOrd": row.ord,
        "isCloze": notetype.isCloze,
        "revealed": revealed,
        "frontSide": None,
    }


def renderCard(row, notetype):
    """Return (termHtml, definitionHtml) — the two faces of the flip card.

    The definition side is the answer template with the repeated front side stripped off, so
    that a back-first session does not give the front away.
    """
    template = None
    if notetype.templates:
        # Cloze notetypes reuse a single template for every deletion ordinal.
        index = 0 if notetype.isCloze else min(row.ord, len(notetype.templates) - 1)
        template = notetype.templates[index]

    if template is None or not template.qfmt:
        return _fallbackFaces(row, notetype)

    term = renderSide(template.qfmt, buildContext(row, notetype, template, revealed=False))

    answerContext = buildContext(row, notetype, template, revealed=True)
    answerContext["frontSide"] = term
    answer = renderSide(template.afmt, answerContext)

    # Anki's stock answer templates are "{{FrontSide}}<hr id=answer>…"; keep only the tail.
    split = ANSWER_DIVIDER_RE.split(answer, 1)
    definition = split[1] if len(split) > 1 else answer

    if not stripHtml(term):
        term = '<span class="tmpl-error">(empty front)</span>'
    if not stripHtml(definition):
        definition = '<span class="tmpl-error">(empty back)</span>'
    return term, definition


def _fallbackFaces(row, notetype):
    """Face content for a note whose templates could not be read: first field vs the rest."""
    names = notetype.fields or ["Field %d" % i for i in range(len(row.fields))]
    pairs = [(n, v) for n, v in zip(names, row.fields) if stripHtml(v)]
    if not pairs:
        return "(empty note)", "(empty note)"
    term = pairs[0][1]
    rest = "".join('<div class="fallback-row"><b>%s</b><br>%s</div>'
                   % (html.escape(n), v) for n, v in pairs[1:])
    return replaceAvTags(term), replaceAvTags(rest or "(no other fields)")


SHELL_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; }
  /* The app is dark-only. Declaring it stops Chromium applying its own automatic dark-mode
     inversion on top, which would fight these colours rather than agree with them. */
  :root { color-scheme: dark; }
  html, body {
    height: 100%; margin: 0;
    background: #0f1225;
    /* The CJK family is named explicitly so Korean, Japanese and Chinese decks do not
       depend on fontconfig picking a covering fallback for the Latin families. */
    font-family: "Segoe UI", Ubuntu, Cantarell, "Noto Sans", "Noto Sans CJK KR",
                 "Noto Sans CJK JP", sans-serif;
    color: #e9ecf8;
  }

  #scene { height: 100%; padding: 22px; perspective: 2200px; }

  #flipper {
    position: relative; width: 100%; height: 100%;
    transition: transform 420ms cubic-bezier(.2,.7,.3,1);
    transform-style: preserve-3d; cursor: pointer;
  }
  #flipper.flipped { transform: rotateX(180deg); }
  #flipper.instant { transition: none; }

  .face {
    position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    backface-visibility: hidden; -webkit-backface-visibility: hidden;
    background: #1a1f3d; border: 1px solid #2b3160; border-radius: 16px;
    box-shadow: 0 10px 30px rgba(0,0,0,.45);
    display: flex; flex-direction: column;
    padding: 26px 30px 22px; overflow: auto;
    /* Backface hiding alone already conceals the far side. The opacity swap is a
       belt-and-braces second mechanism, timed to the midpoint of the 420ms rotation so
       it happens while the card is edge-on and cannot be seen. */
    transition: opacity 0s linear 210ms;
  }
  #front { opacity: 1; }
  #back  { opacity: 0; transform: rotateX(180deg); }
  #flipper.flipped #front { opacity: 0; }
  #flipper.flipped #back  { opacity: 1; }
  #flipper.instant .face { transition: none; }

  .face-label {
    flex: 0 0 auto;
    font-size: 11px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase;
    color: #7b83ad; user-select: none;
  }

  .anki-content {
    flex: 1 1 auto;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    text-align: center; font-size: 30px; line-height: 1.45;
    padding: 12px 0; word-break: break-word;
  }
  .anki-content img { max-width: 100%; height: auto; border-radius: 8px; }
  .anki-content hr { width: 60%; border: none; border-top: 1px solid #2b3160; margin: 18px 0; }

  .flip-hint {
    flex: 0 0 auto; text-align: center;
    font-size: 12px; color: #6d76a0; user-select: none;
  }

  .cloze { color: #6ba4ff; font-weight: 700; }
  .cloze-blank { color: #6ba4ff; font-weight: 700; }
  .av-tag { font-size: .8em; opacity: .55; }
  .av-tag[data-sound] { cursor: pointer; }
  .av-tag[data-sound]:hover { opacity: 1; }
  .type-prompt, .tmpl-error { font-size: 15px; color: #6d76a0; font-style: italic; }

  .hint { font-size: 17px; }
  .hint summary { cursor: pointer; color: #6ba4ff; font-size: 15px; }
  .fallback-row { font-size: 22px; margin: 8px 0; }

  /* Typing mode. Colours are pinned like the rest of the theme. */
  .tp-card { display: flex; flex-direction: column; align-items: center; gap: 14px;
             max-width: 820px; }
  .tp-tag { font-size: 11px; letter-spacing: .14em; text-transform: uppercase;
            color: #7b83ad; }
  .tp-prompt { font-size: 26px; color: #e9ecf8; }
  .tp-sentence { font-size: 28px; color: #f6c1d9; }
  .tp-sentence b { color: #6ba4ff; }
  .tp-gap { display: inline-block; min-width: 4.2em; border-bottom: 3px solid #6ba4ff;
            margin: 0 .15em; }
  .tp-translation { font-size: 18px; color: #8fd8d2; }
  .tp-hint { font-size: 24px; color: #ffd27a; letter-spacing: .12em; }
  .tp-verdict { font-size: 26px; font-weight: 700; }
  .tp-ok { color: #5cd39b; }
  .tp-almost { color: #ffd27a; }
  .tp-wrong { color: #ff8a6b; }
  .tp-answer { font-size: 32px; color: #e9ecf8; }
  .tp-compare { font-size: 28px; text-align: left; }
  .tp-key { display: inline-block; width: 6.5em; font-size: 13px; color: #7b83ad;
            text-transform: uppercase; letter-spacing: .1em; white-space: nowrap; }
  body.typing .flip-hint { visibility: hidden; }
  .tp-bad { color: #ff8a6b; text-decoration: underline wavy #ff8a6b; }
  .tp-fix { color: #5cd39b; font-weight: 700; }
  .tp-letters { font-size: 16px; color: #ffd27a; }
  .tp-retype { font-size: 15px; color: #ff8a6b; font-style: italic; }
  .tp-notes { font-size: 15px; color: #a9b0d4; max-width: 720px; }

  #empty {
    height: 100%; display: none;
    align-items: center; justify-content: center;
    text-align: center; font-size: 19px; color: #8b93bd; padding: 40px;
    white-space: pre-wrap;
  }
  body.is-empty #scene { display: none; }
  body.is-empty #empty { display: flex; }
</style></head>
<body>
  <div id="scene">
    <div id="flipper">
      <div class="face" id="front">
        <div class="face-label" id="front-label">Term</div>
        <!-- The `card` class is what Anki itself wraps note content in, so a deck's own
             CSS (`.card { ... }`) applies here exactly as it does inside Anki. -->
        <div class="anki-content card" id="front-content"></div>
        <div class="flip-hint">click the card or press Space to flip</div>
      </div>
      <div class="face" id="back">
        <div class="face-label" id="back-label">Definition</div>
        <div class="anki-content card" id="back-content"></div>
        <div class="flip-hint">click the card or press Space to flip</div>
      </div>
    </div>
  </div>
  <div id="empty"></div>

<style id="notetype-css"></style>
<style id="post-css"></style>
<script>/*__QWEBCHANNEL_JS__*/</script>
<script>
  var flipper = document.getElementById('flipper');

  // Deck CSS hard-codes its own background and text colour to suit Anki's own window, which
  // here means white text on a white block. The theme therefore pins the card's background
  // and base colour in a stylesheet that comes after the deck's, so it wins. Targeting
  // `.card` itself and not its descendants means a field carrying its own inline colour
  // still shows in that colour.
  var THEME_CSS =
    '.anki-content.card { background: transparent !important; color: #e9ecf8 !important; }';

  function setZoom(factor) {
    document.getElementById('front-content').style.zoom = factor;
    document.getElementById('back-content').style.zoom = factor;
  }

  // Content is pushed in from Python rather than reloading the page, so the flip animation
  // and scroll position survive between cards.
  function setCard(frontHtml, backHtml, css, flipped, frontLabel, backLabel) {
    flipper.classList.add('instant');
    flipper.classList.toggle('flipped', !!flipped);
    document.body.classList.remove('is-empty');
    // Typing cards are answered in the box below, not flipped, so drop the flip hint.
    document.body.classList.toggle('typing', frontHtml.indexOf('tp-card') >= 0);
    document.getElementById('notetype-css').textContent = css || '';
    document.getElementById('front-content').innerHTML = frontHtml;
    document.getElementById('back-content').innerHTML = backHtml;
    document.getElementById('front-label').textContent = frontLabel;
    document.getElementById('back-label').textContent = backLabel;
    document.getElementById('front').scrollTop = 0;
    document.getElementById('back').scrollTop = 0;
    releaseInstant();
    playVisibleFace();
  }

  // Re-enable transitions once the instant state has been painted. A timer backs up the
  // animation frames because Chromium stops servicing rAF when the window is not visible,
  // which would otherwise leave 'instant' stuck on and kill the flip animation for good.
  var instantTimer = null;
  function releaseInstant() {
    function release() { flipper.classList.remove('instant'); }
    requestAnimationFrame(function () { requestAnimationFrame(release); });
    clearTimeout(instantTimer);
    instantTimer = setTimeout(release, 80);
  }

  function setFlipped(flipped) {
    flipper.classList.toggle('flipped', !!flipped);
    playVisibleFace();
  }

  // Sounds play one after another, as in Anki. Relative names resolve against the page's
  // base URL, which is the profile's collection.media directory.
  var player = new Audio();
  var soundQueue = [];
  function playNextSound() {
    if (!soundQueue.length) { return; }
    player.src = encodeURIComponent(soundQueue.shift());
    player.play().catch(function () {});
  }
  player.addEventListener('ended', playNextSound);
  player.addEventListener('error', playNextSound);

  function stopAudio() {
    soundQueue = [];
    player.pause();
  }

  function playSounds(names) {
    stopAudio();
    soundQueue = names.slice();
    playNextSound();
  }

  function playVisibleFace() {
    var face = flipper.classList.contains('flipped') ? 'back' : 'front';
    var tags = document.querySelectorAll('#' + face + '-content .av-tag[data-sound]');
    playSounds(Array.prototype.map.call(tags, function (tag) {
      return tag.getAttribute('data-sound');
    }));
  }

  function showEmpty(message) {
    stopAudio();
    document.body.classList.add('is-empty');
    document.getElementById('empty').textContent = message;
  }

  // Clicks reach Qt over a QWebChannel. Navigating to a custom scheme was tried first and
  // does not work: Chromium silently drops the request for an unregistered scheme, so the
  // card never flipped on click at all.
  if (window.QWebChannel && window.qt && qt.webChannelTransport) {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      window.drillBridge = channel.objects.drill;
    });
  }

  flipper.addEventListener('click', function (event) {
    // A speaker glyph replays its own sound instead of flipping the card.
    var sound = event.target.closest('.av-tag[data-sound]');
    if (sound) {
      playSounds([sound.getAttribute('data-sound')]);
      return;
    }
    // Let the user select text on the card without it flipping underneath them.
    if (String(window.getSelection())) { return; }
    if (window.drillBridge) { window.drillBridge.flip(); }
  });

  document.getElementById('post-css').textContent = THEME_CSS;
</script>
</body></html>
"""


CHANNEL_JS_MARKER = "/*__QWEBCHANNEL_JS__*/"


def shellHtml(channelScript=""):
    """Return the shell page with Qt's qwebchannel.js inlined into its placeholder."""
    return SHELL_HTML.replace(CHANNEL_JS_MARKER, channelScript or "")


def setCardScript(termHtml, definitionHtml, css, flipped, frontLabel, backLabel):
    """Return the JS call that loads one card into the shell page."""
    return "setCard(%s, %s, %s, %s, %s, %s);" % (
        json.dumps(termHtml), json.dumps(definitionHtml), json.dumps(css),
        "true" if flipped else "false", json.dumps(frontLabel), json.dumps(backLabel))
