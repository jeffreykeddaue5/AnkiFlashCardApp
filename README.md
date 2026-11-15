# Anki Drill

A small PyQt5 flashcard app that reads your Anki collection on Linux and drills the cards
that are **due today** or **learning today**, Quizlet-style: one big card, click to flip,
arrow keys to move, repeat until you know them all.

It is **read-only**. Your collection is copied to a temp file and opened with
`PRAGMA query_only`, so nothing here can corrupt your cards, disturb a running Anki, or
desync AnkiWeb. The flip side of that: grading a card in this app does **not** feed back
into Anki's scheduler. It is a cram/drill tool, not a replacement for reviewing in Anki.

## Running

```bash
cd ~/projects/anki-drill
python3 run.py
```

No install step and no dependencies beyond PyQt5 (plus PyQtWebEngine, optional — see below).

## Today's recommended list

This is the default. Sync Anki, open the app, and the list is already there. It is built
from your synced collection plus the app's own typing record, and doesn't need Claude or
any other outside step. Cards are placed by their most urgent reason, and each card shows
all of its reasons as badges:

| Priority | Badge | Gets in when |
|---|---|---|
| 1 | 📌 Pinned | pinned today or yesterday (see [Pinned cards](#pinned-cards)) |
| 2 | ⚠ Trouble word | tagged `use_trouble`, **or** Again pressed 2+ times on reviews in the last 14 days, **or** typed wrong here on 2+ different days in the last 14 |
| 3 | ✕ Missed today | Again pressed in Anki today |
| 4 | ⌨ Typed wrong | typed wrong in this app in the last 7 days |
| 5 | ↻ Due | due for review today |
| 6 | 🩹 Keeps lapsing | a review card that has lapsed 3+ times |
| 7 | ★ New | today's new-card allowance |
| 8 | ✓ Done today | already answered today |

Again presses during a new card's first learning steps don't count toward "trouble". Everyone
misses a word a few times on the day they meet it.

**Top 20/30/40/60/All** caps the list (default 40), trimming from the least urgent end. The
header shows the breakdown, e.g. `📌 16  ⚠ 26  ↻ 5  (top 40 of 94)`. Shuffle only mixes
cards within the same priority level. **Custom queues** brings back the original Due / New /
Reviewed / Pinned checkboxes described below.

## Typing mode

The **Mode** control (`T` cycles it) decides how each card is studied:

- **Auto** (default): flip a card the first time and type it after that, from round 2 on.
  Pinned, trouble, missed-today, typed-wrong and lapsing cards are typed straight away.
- **Flip only** / **Type all**: one way for every card.

A typing card asks for the Korean. Its example sentence is shown with the word blanked
out, so you type the word *with* its ending or particle:

    get motion sick, carsick
    버스를 오래 타면 ______.
    I get carsick if I ride the bus for a long time.          → 멀미해요

The gap is found automatically. A conjugated or irregular form is matched letter by letter
(들뜨다 finds 들떠요), and a **bold** part of the sentence, or a `Korean Type` note's marked
gap and Chunk, is used as is. A note without a usable sentence asks for the word itself,
and any form on its `Conj:` line also counts as correct.

| Key | In the answer box |
|---|---|
| `Enter` | Check · then next card |
| `Ctrl+H` / **Hint** | Next hint: `○○○○` → initial consonants `ㅁㅁㅎㅇ` → `멀○○○` |
| `Ctrl+Enter` / **That was a typo** | Count an "almost" answer as correct |
| **Show answer** | "I don't know": reveals the answer like a wrong one |
| `←` / `→` | Previous / next card, when the box is empty or the answer is checked (otherwise they move the cursor) |
| `Alt+←` / `Alt+→` | Previous / next card, always, even mid-word |

Checking ignores spacing and punctuation and compares letter by letter (jamo):

- **✓ Correct**: retired for the session (if no hint was used), and the card's audio plays.
- **≈ Almost**: one letter off (two for long answers). Enter keeps the card for another
  round. Ctrl+Enter counts it as a typo.
- **✕ Not quite**: the wrong syllables are marked, with a note such as
  `헤 → 해 (vowel ㅔ → ㅐ)`. You must **type the correct answer once** to move on, and the card
  comes back next round.

While the answer box is up, the single-letter shortcuts are switched off so they type
instead. A Korean syllable still being composed by the input method when you press Enter is
included in the answer.

Results are saved to `~/.local/share/anki-drill/typing-stats.json` (misses by day,
correct streak per note). That file feeds the ⌨ and ⚠ reasons above. Nothing is written to
Anki.

## Custom queues (what it studied before)

The three checkboxes across the top map onto Anki's own scheduler, and show live counts for
the selected deck. All three are on by default:

| Checkbox | Selects | Meaning |
|---|---|---|
| Due today | `queue = 2`, `due <= today` | The green "Due" count, including anything overdue |
| New today | `queue = 0`, capped | The blue "New" count — your deck's daily new-card limit, less however many it has already introduced today |
| Reviewed today | answered since the rollover | Everything you have already studied today, wherever Anki has since scheduled it |
| Pinned | add-on pin tags | Cards pinned today or yesterday — **only shown when the pinning add-on is in use** |

**Reviewed today** is what stops the day's work vanishing from under you. Once you answer a
card in Anki it is rescheduled days into the future, so it drops out of "Due today"
immediately — but it is still the vocabulary you are learning right now. Keeping this ticked
means the cards you have finished stay drillable for the rest of the day. It reads from the
review log, so it counts work done in Anki as well as anything answered before you reloaded.
Bookkeeping entries such as a manual "set due date" are not counted as having studied a card.

**Pinned** appears only when the [Today's Review Words](#pinned-cards) add-on is involved;
see below.

Selecting several queues never shows a card twice, even when it qualifies under more than one.

### Pinned cards

The *Today's Review Words* add-on (package `todays_review_words`) marks a pin with two tags
on the note: `review_pinned`, plus a per-study-day tag such as `review_pinned_day_610`. A pin
counts as live only while **both** are present, which is how the add-on expires pins
overnight. It deliberately keeps the previous day's marker for one more day, so this app
treats **pinned today or yesterday** as pinned.

That two-tag rule matters: collections accumulate stale day markers, because the add-on only
purges them when it next touches a note. Matching on `review_pinned` alone would resurrect
pins from a week ago that the add-on considers long gone.

Suspended and buried pinned cards are excluded, as they are for every other queue.

**When the checkbox appears:**

| Add-on folder | Checkbox |
|---|---|
| Present and enabled | Shown |
| Present but disabled in its `meta.json` | Hidden |
| Not installed, but the collection has pin tags | Shown — so a synced profile keeps its pins reachable |
| Not installed and no pin tags | Hidden |

The add-on is looked for at `<Anki2>/addons21/todays_review_words`. A folder with no
`meta.json` counts as enabled, since Anki only writes that file for AnkiWeb installs.

"New today" is genuinely *today's* allowance, not every new card you own. If a deck holds 46
unseen cards but is configured for 10/day and you have already started 1, this shows 9 — the
same number Anki would. The limit is read from the deck's config, and the already-started
tally from the deck's own daily counters, so the two agree.

"Today" is likewise computed the way Anki computes it: a day number counted from the
collection's creation stamp, with the day not rolling over until your configured rollover
hour (default 4am). Suspended and buried cards are never included. Picking a parent deck
includes its subdecks.

Cards part-way through a learning step (Anki's red "Learning" count) are **not** drilled —
only reviews and new cards. See Limitations.

## Studying

The session runs in **rounds**. You go through every card once; anything you mark
*Still learning* (or never grade) comes back in the next round, and anything you mark
*Got it* is retired. Repeat until the round is empty.

| Key | Action |
|---|---|
| `Space` | Flip the card |
| `→` / `Enter` | Next card |
| `←` | Previous card |
| `Alt+←` / `Alt+→` | Previous / next card (also works while typing) |
| `1` | Still learning — keep it for the next round |
| `2` | Got it — retire it for this session |
| `B` | Toggle front-first / back-first |
| `F` | Cycle Show: All → Still learning → Got it |
| `R` | Replay the audio on the side that is showing |
| `S` | Toggle shuffle |
| `Ctrl` `+` / `Ctrl` `-` | Bigger / smaller card text |
| `F5` or `Ctrl+R` | Re-read the collection from disk |

Clicking the card flips it too — as often as you like, in either direction — though
selecting text on it does not flip it.

At the end of a round you get a summary screen. It is not a dead end: `←` or the
**Back to cards** button returns you to the last card, and `→` carries on into the next
round.

### Front-first vs back-first

The **Show first** toggle decides which side leads. Front-first is recognition
(Korean → English); back-first is recall (English → Korean). Switching mid-session
re-renders the current card immediately.

For this to be useful, the back of the card must not repeat the front — so the app splits
Anki's answer template at the standard `<hr id=answer>` divider and shows only the part
after it. A stock `{{FrontSide}}<hr id=answer>{{Back}}` template therefore yields a clean
back with no spoiler.

### Showing only one grade

The **Show** dropdown narrows the round to the cards you graded one way — `All cards`,
`✕ Still learning`, or `✓ Got it` — with a live count beside each. Filtering to *Still
learning* and drilling just those is the fast way to work on what you keep missing, without
waiting for the round to end.

Behaviour worth knowing:

- Narrowing starts a fresh pass at the top of the subset, since the point is to drill it
  again. Clearing the filter keeps the card on screen, so you do not lose your place.
- Grading a card out of the filter drops it from view and slides the next one in — mark a
  *Still learning* card as *Got it* and it disappears from the list as you go.
- The progress line shows the subset and the round together, e.g. `1 / 8 (of 61)`. The
  known / still-learning counts always refer to the whole round.
- Picking a filter on the end-of-round screen takes you straight back to the cards.
- The filter clears itself whenever grades reset — a new round, a replay, or a restart.

### Shuffle

Toggling shuffle mid-round only reorders the cards **ahead** of the cursor, so your position
and the cards you have already graded stay put.

## Card rendering

Cards are rendered from your real Anki note templates, not just dumped fields:

- `{{Field}}`, `{{FrontSide}}`, `{{Tags}}`, `{{Type}}`, `{{Deck}}`, `{{Subdeck}}`, `{{Card}}`
- Conditional sections `{{#Field}}…{{/Field}}` and `{{^Field}}…{{/Field}}`
- Filters `text:`, `cloze:`, `hint:`, `type:` (a placeholder on the flip card; typing mode has its own answer box)
- Cloze deletions `{{c1::answer::hint}}`, blanked on the front and highlighted on the back
- Your deck's own CSS, applied to a `.card` wrapper exactly as Anki does
- Images from `collection.media`

Audio from `collection.media` plays automatically when a side comes up, one clip after
another, as in Anki. `[sound:…]` references render as a 🔊 glyph: click it to play that clip,
or press `R` to replay the whole side. Anki's built-in `{{tts:…}}` voices are not supported. Schema 18 stores templates as protobuf blobs, which the app parses directly — it does
not import the `anki` package.

The app is dark-only — there is no theme toggle. The card pins its own background and base
text colour, overriding the deck's, because decks hard-code colours that suit Anki's window
and not this one. Everything else from the deck CSS (fonts, sizes, alignment, per-field
colours) still applies. Use **A−** / **A+** to scale the whole card, whatever size the deck
asked for.

## Where it looks for your collection

In order: `~/.var/app/net.ankiweb.Anki/data/Anki2` (Flatpak), `~/.local/share/Anki2`,
`~/Anki2`, `~/snap/anki/current/.local/share/Anki2`, `~/.anki2`. Every profile found is
listed in the **Profile** dropdown. Both the modern schema (18) and the older
JSON-in-`col` schema (11) are supported.

## Requirements

- Python 3.6+
- PyQt5
- PyQtWebEngine — optional but recommended. With it you get the real HTML/CSS card and the
  3D flip animation. Without it the app falls back to a `QTextBrowser` that swaps sides with
  no animation and ignores deck CSS.

If QtWebEngine fails to start in an unusual environment (containers, no GPU), try:

```bash
QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox" python3 run.py
```

## Tests

```bash
python3 -m pytest test/ -q
```

225 tests. Most need neither Qt nor a real Anki install: day arithmetic, queue selection,
daily new-card allowances, the reviewed-today revlog query, the pin-tag rules and add-on
detection, protobuf parsing, template and cloze rendering, the round/session logic, the
status filter, and reading a purpose-built collection — including checks that the snapshot really is read-only and that the temp copy
is cleaned up.

`test/test_gui.py` additionally drives a real window offscreen to guard the bugs that
actually reached the UI: click-to-flip working every time rather than once, the round summary
being escapable, card text staying readable whatever the deck's CSS says, and the Show
filter keeping its counts and its place, and the Pinned checkbox appearing only when it
should. It skips itself if PyQt5 or QtWebEngine is missing, and accounts for most of the
~105s runtime.

## Limitations

- Grading never reaches Anki. Review in Anki to actually advance your scheduling.
- Cards mid-learning-step are not drilled — only reviews and new cards. If you study in Anki
  and leave cards in a learning step, they will not show up here.
- No `{{tts:…}}` speech, no typed-answer checking, no LaTeX rendering.
- If Anki is open and you study something, hit `F5` to pick up the change.
