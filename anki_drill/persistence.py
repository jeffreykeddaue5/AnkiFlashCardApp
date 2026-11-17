"""
persistence.py
Stores the drill app's local grading state on disk, without writing back into Anki.

The persisted state is intentionally separate from Anki's scheduler: grading a card in this
app should survive closing the window, but it should never touch cards.due/queue/revlog. The
state is keyed by the exact study set (collection path + deck selection + queue selection), so
switching decks or checkboxes gives you a separate remembered session.
"""

import hashlib
import json
import os

from .session import FILTER_ALL

APP_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "anki-drill")
STATE_PATH = os.path.join(APP_DIR, "session-state.json")
STATE_VERSION = 1

# Keep the on-disk format explicit rather than relying on the in-memory session symbols.
STATUS_UNSEEN = "unseen"
STATUS_LEARNING = "learning"
STATUS_KNOWN = "known"
VALID_STATUSES = frozenset([STATUS_UNSEEN, STATUS_LEARNING, STATUS_KNOWN])


def ensureAppDir():
    """Create the state directory when it does not exist yet."""
    if not os.path.isdir(APP_DIR):
        os.makedirs(APP_DIR)


def studySetKey(collectionPath, deckIds, queues):
    """Stable key for one remembered study set.

    The card grades are only meaningful relative to the exact set of cards the user chose to
    study, so the key includes collection path, chosen deck scope, and selected queues.
    """
    payload = {
        "collectionPath": os.path.abspath(os.path.expanduser(collectionPath)),
        "deckIds": sorted(deckIds) if deckIds else None,
        "queues": sorted(queues),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def loadStateFile():
    """Return the full persisted state document, or an empty one on first run/corruption."""
    if not os.path.isfile(STATE_PATH):
        return {"version": STATE_VERSION, "sessions": {}}
    try:
        with open(STATE_PATH, "r") as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError):
        return {"version": STATE_VERSION, "sessions": {}}
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        return {"version": STATE_VERSION, "sessions": {}}
    return data


def saveStateFile(data):
    """Atomically write the full persisted state document."""
    ensureAppDir()
    tempPath = STATE_PATH + ".tmp"
    with open(tempPath, "w") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
    os.replace(tempPath, STATE_PATH)


def loadSessionState(collectionPath, deckIds, queues, studyDay):
    """Return the saved state dict for one study set, or None when there is none.

    Saved grades are day-scoped. A different Anki study day means the remembered statuses are
    stale by definition and should not be revived.
    """
    key = studySetKey(collectionPath, deckIds, queues)
    data = loadStateFile()
    session = data["sessions"].get(key)
    if not isinstance(session, dict):
        return None
    if session.get("studyDay") != int(studyDay):
        return None
    state = session.get("state")
    return state if isinstance(state, dict) else None


def saveSessionState(collectionPath, deckIds, queues, state, studyDay):
    """Persist one study set's grading state, replacing any previous copy.

    `studyDay` is Anki's own day number; a saved state from a different day is ignored so the
    local grades never pile up across days.
    """
    key = studySetKey(collectionPath, deckIds, queues)
    data = loadStateFile()
    data["version"] = STATE_VERSION
    data["sessions"][key] = {"studyDay": int(studyDay), "state": state}
    saveStateFile(data)


def clearSessionState(collectionPath, deckIds, queues):
    """Delete any remembered state for one study set."""
    key = studySetKey(collectionPath, deckIds, queues)
    data = loadStateFile()
    if key in data["sessions"]:
        del data["sessions"][key]
        saveStateFile(data)


def serialiseSession(session):
    """Turn a StudySession into a JSON-safe dict.

    Only the card statuses and the user's chrome state are persisted. The card list itself is
    rebuilt fresh from the collection every time so edits in Anki are naturally picked up.
    """
    current = session.current()
    return {
        "statuses": dict(session._status),  # noqa: SLF001 - persistence is intentionally close
        "filter": session.statusFilter,
        "currentCardId": current.cardId if current is not None else None,
        "anchors": dict(session._anchors),  # noqa: SLF001
    }


def applySessionState(session, state):
    """Restore a saved state dict onto `session`, ignoring anything stale or malformed."""
    if not isinstance(state, dict):
        return

    validIds = {card.cardId for card in session._all}  # noqa: SLF001
    for filterName, cardId in (state.get("anchors") or {}).items():
        try:
            cardId = int(cardId)
        except (TypeError, ValueError):
            continue
        if filterName in (FILTER_ALL, STATUS_LEARNING, STATUS_KNOWN) and cardId in validIds:
            session._anchors[filterName] = cardId  # noqa: SLF001

    for cardId, status in (state.get("statuses") or {}).items():
        try:
            cardId = int(cardId)
        except (TypeError, ValueError):
            continue
        if cardId in validIds and status in VALID_STATUSES:
            session._status[cardId] = status  # noqa: SLF001

    session.setStatusFilter(state.get("filter", FILTER_ALL))

    currentCardId = state.get("currentCardId")
    if currentCardId is None:
        return
    try:
        currentCardId = int(currentCardId)
    except (TypeError, ValueError):
        return
    for index, card in enumerate(session._view):  # noqa: SLF001
        if card.cardId == currentCardId:
            session._index = index  # noqa: SLF001
            session._anchors[session.statusFilter] = currentCardId  # noqa: SLF001
            break


# ---------------------------------------------------------------- typing results
#
# What the user typed in typing mode, per note, kept across days. This is the app's own
# memory of production mistakes: it feeds the recommended list (typed wrong recently, or on
# two different days = a trouble word) and never goes anywhere near Anki.

TYPING_PATH = os.path.join(APP_DIR, "typing-stats.json")
TYPING_KEEP_DAYS = 60


def loadTypingStats():
    """Return {noteId(str): {"missDays": [...], "streak": n, "correct": n, ...}}."""
    path = TYPING_PATH
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError):
        return {}
    notes = data.get("notes") if isinstance(data, dict) else None
    return notes if isinstance(notes, dict) else {}


def saveTypingStats(stats):
    """Atomically write the typing stats document."""
    folder = os.path.dirname(TYPING_PATH)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    tempPath = TYPING_PATH + ".tmp"
    with open(tempPath, "w") as handle:
        json.dump({"version": 1, "notes": stats}, handle, indent=1, sort_keys=True,
                  ensure_ascii=False)
    os.replace(tempPath, TYPING_PATH)


def recordTypingResult(stats, noteId, studyDay, correct, word="", peeked=False):
    """Fold one typed answer into `stats` (in place) and return the note's entry.

    A miss records the study day (once per day) and resets the streak; a clean correct
    answer extends the streak. A peek ("Show answer" without trying) resets the streak too
    but goes in `peekDays`, not `missDays`, so it brings the card back without counting
    toward "trouble word". Days older than TYPING_KEEP_DAYS are dropped as we go.
    """
    entry = stats.setdefault(str(noteId), {"missDays": [], "streak": 0, "correct": 0})
    cutoff = studyDay - TYPING_KEEP_DAYS
    entry["missDays"] = [d for d in entry.get("missDays", [])
                         if isinstance(d, int) and d > cutoff]
    if "peekDays" in entry:
        entry["peekDays"] = [d for d in entry["peekDays"] if isinstance(d, int) and d > cutoff]
    if peeked:
        entry["streak"] = 0
        entry["peeks"] = int(entry.get("peeks", 0)) + 1
        days = entry.setdefault("peekDays", [])
        if studyDay not in days:
            days.append(int(studyDay))
    elif correct:
        entry["streak"] = int(entry.get("streak", 0)) + 1
        entry["correct"] = int(entry.get("correct", 0)) + 1
    else:
        entry["streak"] = 0
        if studyDay not in entry["missDays"]:
            entry["missDays"].append(int(studyDay))
    entry["lastDay"] = int(studyDay)
    if word:
        entry["word"] = word
    return entry


def notesMissedSince(stats, firstDay):
    """Note ids typed wrong, or peeked at, on or after study day `firstDay`."""
    found = []
    for key, entry in (stats or {}).items():
        days = (entry.get("missDays", []) + entry.get("peekDays", [])
                if isinstance(entry, dict) else [])
        if any(isinstance(d, int) and d >= firstDay for d in days):
            try:
                found.append(int(key))
            except ValueError:
                continue
    return found


def notesWithMissDays(stats, firstDay, minimumDays):
    """Note ids typed wrong on at least `minimumDays` different days since `firstDay`."""
    found = []
    for key, entry in (stats or {}).items():
        days = entry.get("missDays", []) if isinstance(entry, dict) else []
        if len({d for d in days if isinstance(d, int) and d >= firstDay}) >= minimumDays:
            try:
                found.append(int(key))
            except ValueError:
                continue
    return found


# ---------------------------------------------------------------- settings

SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")


def loadSettings():
    """Return the app's small preferences dict ({} when missing or unreadable)."""
    try:
        with open(SETTINGS_PATH, "r") as handle:
            data = json.load(handle)
    except (IOError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def saveSettings(settings):
    """Atomically write the preferences dict."""
    folder = os.path.dirname(SETTINGS_PATH)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    tempPath = SETTINGS_PATH + ".tmp"
    with open(tempPath, "w") as handle:
        json.dump(settings, handle, indent=1, sort_keys=True, ensure_ascii=False)
    os.replace(tempPath, SETTINGS_PATH)
