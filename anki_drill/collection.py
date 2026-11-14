"""
collection.py
Locates Anki profiles on Linux and reads a collection through a throwaway snapshot copy.

Nothing in this module ever writes to the real collection. The collection is copied to a
temporary directory first, then opened with PRAGMA query_only, so a running Anki cannot be
disturbed and a bug here cannot corrupt the user's cards or desync AnkiWeb.
"""

import json
import os
import shutil
import sqlite3
import tempfile
from collections import namedtuple

# Candidate Anki data roots, in the order we prefer them. Every platform's locations are
# tried on every platform: probing a directory that cannot exist costs nothing, and it keeps
# the app working from a copied-in collection or an unusual install.
PROFILE_ROOTS = (
    # Linux: Flatpak first because that is the common install today, then native, Snap, legacy.
    "~/snap/anki-desktop/common/",
    "~/.var/app/net.ankiweb.Anki/data/Anki2",
    "~/.local/share/Anki2",
    "~/snap/anki/current/.local/share/Anki2",
    "~/.anki2",
    # Windows: %APPDATA%\Anki2.
    "~/AppData/Roaming/Anki2",
    # macOS.
    "~/Library/Application Support/Anki2",
    # Pre-2.1 layout, and the conventional spot for a manually copied collection.
    "~/Anki2",
    "~/Documents/Anki2",
)

COLLECTION_NAME = "collection.anki2"
MEDIA_NAME = "collection.media"
SIDECAR_SUFFIXES = ("-wal", "-shm")

# The add-on that owns the "pinned" concept. Its folder sits beside the profiles.
ADDONS_DIRNAME = "addons21"
PIN_ADDON_PACKAGE = "todays_review_words"

ADDON_ENABLED = "enabled"
ADDON_DISABLED = "disabled"
ADDON_ABSENT = "absent"

# Directories that live beside the profiles but are not profiles themselves.
NON_PROFILE_DIRS = frozenset(["addons21", "logs", "gldriver6", "backups", "crash"])

# Protobuf field numbers inside the schema-18 deck and deck-config blobs.
_CONFIG_NEW_PER_DAY = 9
_DECK_KIND_NORMAL = 1
_NORMAL_CONFIG_ID = 1
_COMMON_LAST_DAY_STUDIED = 3
_COMMON_NEW_STUDIED = 4

Profile = namedtuple("Profile", "name root collectionPath mediaPath")

Notetype = namedtuple("Notetype", "id name css isCloze fields templates")
Template = namedtuple("Template", "ord name qfmt afmt")

# A single card row joined to the note that owns it.
CardRow = namedtuple(
    "CardRow", "cardId noteId deckId deckName notetypeId ord queue type due ivl tags fields"
)


def addonState(profile, package=PIN_ADDON_PACKAGE):
    """Return ADDON_ENABLED, ADDON_DISABLED or ADDON_ABSENT for an add-on in `profile`.

    Anki writes `meta.json` only for add-ons installed from AnkiWeb, so a hand-placed folder
    with no metadata counts as enabled.
    """
    addonDir = os.path.join(profile.root, ADDONS_DIRNAME, package)
    if not os.path.isdir(addonDir):
        return ADDON_ABSENT
    try:
        with open(os.path.join(addonDir, "meta.json"), "r") as handle:
            if json.load(handle).get("disabled"):
                return ADDON_DISABLED
    except (IOError, OSError, ValueError):
        pass
    return ADDON_ENABLED


def pinningAvailable(profile, snapshot):
    """Whether the Pinned selection should be offered for this profile.

    An add-on that is present but switched off hides the option outright. When the add-on is
    not installed at all the collection itself is consulted, so a profile synced from a
    machine that does have it keeps its pins reachable.
    """
    state = addonState(profile)
    if state == ADDON_ENABLED:
        return True
    if state == ADDON_DISABLED:
        return False
    return snapshot.hasPinTags()


def profileForCollection(collectionPath):
    """Build a Profile for an arbitrary collection.anki2 chosen by the user.

    Used by the "Open…" button so a collection in an unexpected place is still usable.
    """
    collectionPath = os.path.abspath(os.path.expanduser(collectionPath))
    profileDir = os.path.dirname(collectionPath)
    return Profile(
        name=os.path.basename(profileDir) or "Collection",
        root=os.path.dirname(profileDir),
        collectionPath=collectionPath,
        mediaPath=os.path.join(profileDir, MEDIA_NAME))


def candidateRoots():
    """Return the profile roots that actually exist on this machine."""
    roots = []
    # On Windows %APPDATA% may be redirected somewhere other than the default.
    appData = os.environ.get("APPDATA")
    if appData:
        roots.append(os.path.join(appData, "Anki2"))
    roots += [os.path.expanduser(raw) for raw in PROFILE_ROOTS]

    seen = set()
    existing = []
    for root in roots:
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen and os.path.isdir(root):
            seen.add(key)
            existing.append(root)
    return existing


def findProfiles():
    """Return every Anki profile found on this machine as a list of Profile tuples."""
    profiles = []
    for root in candidateRoots():
        for entry in sorted(os.listdir(root)):
            if entry in NON_PROFILE_DIRS or entry.startswith("."):
                continue
            profileDir = os.path.join(root, entry)
            collectionPath = os.path.join(profileDir, COLLECTION_NAME)
            if os.path.isfile(collectionPath):
                profiles.append(Profile(
                    name=entry,
                    root=root,
                    collectionPath=collectionPath,
                    mediaPath=os.path.join(profileDir, MEDIA_NAME),
                ))
    return profiles


def _unicaseCollation(left, right):
    """Case-insensitive comparison standing in for Anki's own "unicase" collation."""
    left, right = (left or "").casefold(), (right or "").casefold()
    return (left > right) - (left < right)


def _decodeConfigValue(raw):
    """Decode a schema-18 `config` table value, which stores JSON in a BLOB."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


class CollectionSnapshot(object):
    """A read-only SQLite handle onto a temporary copy of an Anki collection."""

    def __init__(self, profile):
        self.profile = profile
        self._tempDir = None
        self._conn = None
        self._schema = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, excType, excValue, traceback):
        self.close()
        return False

    def open(self):
        """Copy the collection (and any WAL sidecars) to temp storage and connect read-only."""
        self._tempDir = tempfile.mkdtemp(prefix="anki-drill-")
        target = os.path.join(self._tempDir, COLLECTION_NAME)
        shutil.copyfile(self.profile.collectionPath, target)
        # The WAL may hold committed pages that are not in the main file yet; copying it keeps
        # the snapshot consistent with what Anki itself would read.
        for suffix in SIDECAR_SUFFIXES:
            sidecar = self.profile.collectionPath + suffix
            if os.path.isfile(sidecar):
                shutil.copyfile(sidecar, target + suffix)
        self._conn = sqlite3.connect(target)
        self._conn.text_factory = lambda b: b.decode("utf-8", "replace")
        # Anki declares its `tags` table with a custom "unicase" collation that plain
        # sqlite3 does not know, and any query touching it fails outright without this.
        self._conn.create_collation("unicase", _unicaseCollation)
        self._conn.execute("PRAGMA query_only = ON")
        self._schema = self._detectSchema()

    def close(self):
        """Close the handle and delete the temporary copy."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._tempDir is not None:
            shutil.rmtree(self._tempDir, ignore_errors=True)
            self._tempDir = None

    def _detectSchema(self):
        """Return 18 for the split-table schema, or 11 for the old JSON-in-`col` schema."""
        names = {row[0] for row in
                 self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return 18 if "notetypes" in names and "templates" in names else 11

    @property
    def schema(self):
        """Schema generation of the open collection (18 = modern, 11 = legacy)."""
        return self._schema

    def creationStamp(self):
        """Return the collection's `crt` epoch — the anchor for Anki's day numbering."""
        return int(self._conn.execute("SELECT crt FROM col").fetchone()[0])

    def rolloverHour(self):
        """Return the configured next-day-starts-at hour, defaulting to Anki's 4am."""
        if self._schema >= 18:
            row = self._conn.execute(
                "SELECT val FROM config WHERE key = 'rollover'").fetchone()
            if row is not None:
                value = _decodeConfigValue(row[0])
                if isinstance(value, int):
                    return value
        row = self._conn.execute("SELECT conf FROM col").fetchone()
        if row and row[0]:
            try:
                return int(json.loads(row[0]).get("rollover", 4))
            except (ValueError, TypeError, AttributeError):
                pass
        return 4

    def deckNewAllowances(self, today):
        """Return {deckId: how many new cards that deck may still introduce today}.

        This mirrors Anki's blue "New" count: the deck's configured new-cards-per-day, less
        however many it has already introduced today.
        """
        if self._schema >= 18:
            return self._newAllowancesModern(today)
        return self._newAllowancesLegacy(today)

    def _newAllowancesModern(self, today):
        """Per-deck new allowances from the schema-18 decks/deck_config protobuf blobs."""
        from .rendering import parseProtoBlob
        from .scheduling import DEFAULT_NEW_PER_DAY, newAllowance

        # DeckConfigInner.new_per_day is field 9.
        limits = {}
        for configId, blob in self._conn.execute("SELECT id, config FROM deck_config"):
            values = parseProtoBlob(blob).get(_CONFIG_NEW_PER_DAY)
            limits[int(configId)] = int(values[0]) if values else DEFAULT_NEW_PER_DAY

        allowances = {}
        for deckId, common, kind in self._conn.execute("SELECT id, common, kind FROM decks"):
            # Deck.kind holds a Normal or Filtered submessage; Normal.config_id is field 1.
            configId = 1
            normal = parseProtoBlob(kind).get(_DECK_KIND_NORMAL)
            if normal and isinstance(normal[0], bytes):
                inner = parseProtoBlob(normal[0]).get(_NORMAL_CONFIG_ID)
                if inner:
                    configId = int(inner[0])

            # DeckCommon tracks the day the tallies belong to, then the tallies themselves.
            commonFields = parseProtoBlob(common)
            lastDay = (commonFields.get(_COMMON_LAST_DAY_STUDIED) or [0])[0]
            newStudied = (commonFields.get(_COMMON_NEW_STUDIED) or [0])[0]
            allowances[int(deckId)] = newAllowance(
                limits.get(configId, DEFAULT_NEW_PER_DAY), lastDay, newStudied, today)
        return allowances

    def _newAllowancesLegacy(self, today):
        """Per-deck new allowances from the pre-schema-18 JSON in col.decks / col.dconf."""
        from .scheduling import DEFAULT_NEW_PER_DAY, newAllowance

        limits = {}
        raw = self._conn.execute("SELECT dconf FROM col").fetchone()[0]
        for key, config in json.loads(raw or "{}").items():
            limits[int(key)] = int(config.get("new", {}).get("perDay", DEFAULT_NEW_PER_DAY))

        allowances = {}
        raw = self._conn.execute("SELECT decks FROM col").fetchone()[0]
        for key, deck in json.loads(raw or "{}").items():
            # newToday is stored as [dayNumber, countStudiedThatDay].
            newToday = deck.get("newToday") or [0, 0]
            allowances[int(key)] = newAllowance(
                limits.get(int(deck.get("conf", 1)), DEFAULT_NEW_PER_DAY),
                newToday[0], newToday[1], today)
        return allowances

    def hasPinTags(self):
        """Whether any note in the collection carries the add-on's pin marker tag."""
        from .scheduling import PIN_TAG, tagToken

        row = self._conn.execute(
            "SELECT EXISTS (SELECT 1 FROM notes WHERE instr(tags, ?) > 0)",
            (tagToken(PIN_TAG),)).fetchone()
        return bool(row[0])

    def decks(self):
        """Return [(deckId, displayName)] sorted by name, with nested decks shown as A::B."""
        if self._schema >= 18:
            rows = self._conn.execute("SELECT id, name FROM decks").fetchall()
            # Schema 18 separates nesting levels with 0x1f rather than the displayed '::'.
            decks = [(int(i), n.replace("\x1f", "::")) for i, n in rows]
        else:
            raw = self._conn.execute("SELECT decks FROM col").fetchone()[0]
            decks = [(int(k), v.get("name", "?")) for k, v in json.loads(raw).items()]
        return sorted(decks, key=lambda d: d[1].lower())

    def notetypes(self):
        """Return {notetypeId: Notetype} with CSS, field names and template formats resolved."""
        if self._schema >= 18:
            return self._notetypesModern()
        return self._notetypesLegacy()

    def _notetypesModern(self):
        """Build notetypes from the schema-18 notetypes/fields/templates tables."""
        from .rendering import notetypeConfig, templateConfig

        fieldsByType = {}
        for ntid, ordinal, name in self._conn.execute(
                "SELECT ntid, ord, name FROM fields ORDER BY ntid, ord"):
            fieldsByType.setdefault(int(ntid), []).append(name)

        templatesByType = {}
        for ntid, ordinal, name, config in self._conn.execute(
                "SELECT ntid, ord, name, config FROM templates ORDER BY ntid, ord"):
            qfmt, afmt = templateConfig(config)
            templatesByType.setdefault(int(ntid), []).append(
                Template(ord=int(ordinal), name=name, qfmt=qfmt, afmt=afmt))

        notetypes = {}
        for ntid, name, config in self._conn.execute("SELECT id, name, config FROM notetypes"):
            ntid = int(ntid)
            css, isCloze = notetypeConfig(config)
            notetypes[ntid] = Notetype(
                id=ntid, name=name, css=css, isCloze=isCloze,
                fields=fieldsByType.get(ntid, []),
                templates=templatesByType.get(ntid, []))
        return notetypes

    def _notetypesLegacy(self):
        """Build notetypes from the pre-schema-18 JSON blob in col.models."""
        raw = self._conn.execute("SELECT models FROM col").fetchone()[0]
        notetypes = {}
        for key, model in json.loads(raw).items():
            templates = [Template(ord=i, name=t.get("name", ""),
                                  qfmt=t.get("qfmt", ""), afmt=t.get("afmt", ""))
                         for i, t in enumerate(model.get("tmpls", []))]
            notetypes[int(key)] = Notetype(
                id=int(key), name=model.get("name", "?"), css=model.get("css", ""),
                isCloze=model.get("type", 0) == 1,
                fields=[f.get("name", "") for f in model.get("flds", [])],
                templates=templates)
        return notetypes

    def _fetchRows(self, clause, params, deckIds, orderBy=None):
        """Return CardRows matching the SQL `clause`, limited to `deckIds` (None = all)."""
        sql = [
            "SELECT c.id, c.nid, c.did, c.odid, n.mid, c.ord, c.queue, c.type, c.due, c.ivl,",
            "       n.tags, n.flds",
            "FROM cards c JOIN notes n ON n.id = c.nid",
            "WHERE (" + clause + ")",
        ]
        params = list(params)
        if deckIds:
            # odid is the original deck for cards currently sitting in a filtered deck.
            placeholders = ",".join("?" for _ in deckIds)
            sql.append("AND (CASE WHEN c.odid != 0 THEN c.odid ELSE c.did END) IN (%s)"
                       % placeholders)
            params += [int(d) for d in deckIds]
        # Reviews first, then new cards in their introduction order. A new card's `due` is a
        # queue position rather than a day number, so the two cannot share one sort key.
        sql.append(orderBy or "ORDER BY CASE WHEN c.queue = 0 THEN 1 ELSE 0 END, c.due, c.id")

        deckNames = dict(self.decks())
        rows = []
        for r in self._conn.execute("\n".join(sql), params):
            homeDeck = int(r[3]) or int(r[2])
            rows.append(CardRow(
                cardId=int(r[0]), noteId=int(r[1]), deckId=homeDeck,
                deckName=deckNames.get(homeDeck, "?"), notetypeId=int(r[4]), ord=int(r[5]),
                queue=int(r[6]), type=int(r[7]), due=int(r[8]), ivl=int(r[9]),
                tags=r[10] or "", fields=(r[11] or "").split("\x1f")))
        return rows

    def fetchCards(self, deckIds, queues, today, newAllowances=None, dayStartMs=0):
        """Return CardRows for `queues`, limited to `deckIds` (None = every deck).

        `queues` is a set of the queue-name strings understood by scheduling.queueClause.
        When `newAllowances` is given, new cards are trimmed to each deck's daily limit.
        `dayStartMs` is needed only when the "reviewed" queue is selected.
        """
        from .scheduling import capNewCards, queueClause

        clause, params = queueClause(queues, today, dayStartMs)
        if clause is None:
            return []
        return capNewCards(self._fetchRows(clause, params, deckIds), newAllowances)

    def cardsWithTag(self, deckIds, tag):
        """Unsuspended, unburied cards whose note carries `tag` exactly."""
        from .scheduling import tagToken

        return self._fetchRows(
            "c.queue >= 0 AND c.nid IN (SELECT id FROM notes WHERE instr(tags, ?) > 0)",
            [tagToken(tag)], deckIds)

    def cardsByIds(self, deckIds, cardIds):
        """Unsuspended, unburied cards among `cardIds`."""
        ids = sorted({int(i) for i in cardIds})
        if not ids:
            return []
        return self._fetchRows(
            "c.queue >= 0 AND c.id IN (%s)" % ",".join(str(i) for i in ids), [], deckIds)

    def cardsByNotes(self, deckIds, noteIds):
        """Unsuspended, unburied cards belonging to `noteIds`."""
        ids = sorted({int(i) for i in noteIds})
        if not ids:
            return []
        return self._fetchRows(
            "c.queue >= 0 AND c.nid IN (%s)" % ",".join(str(i) for i in ids), [], deckIds)

    def againCounts(self, sinceMs, reviewsOnly=False):
        """Return {cardId: how many times Again was pressed} since `sinceMs`.

        With `reviewsOnly`, presses during a new card's first learning steps are ignored
        (revlog.type 0); everyone presses Again a few times on a word they met today, and
        that alone should not make it a trouble word. Reviews (1) and relearning (2) count.
        """
        sql = "SELECT cid, count(*) FROM revlog WHERE id >= ? AND ease = 1"
        if reviewsOnly:
            sql += " AND type IN (1, 2)"
        return {int(cid): int(n) for cid, n in self._conn.execute(
            sql + " GROUP BY cid", (int(sinceMs),))}

    def leechCards(self, deckIds, minLapses):
        """Unsuspended review cards that have lapsed at least `minLapses` times."""
        return self._fetchRows("c.queue >= 0 AND c.type = 2 AND c.lapses >= ?",
                               [int(minLapses)], deckIds)

    def queueCounts(self, deckIds, today, newAllowances=None, dayStartMs=0):
        """Return {queueName: count} for the deck selection, for the header counters."""
        from .scheduling import QUEUE_NAMES

        counts = {}
        for name in QUEUE_NAMES:
            counts[name] = len(self.fetchCards(
                deckIds, {name}, today, newAllowances, dayStartMs))
        return counts
