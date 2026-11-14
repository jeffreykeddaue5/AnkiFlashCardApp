"""
recommend.py
Builds the one "Today's recommended" list straight from the collection, with no outside
help: open the app after syncing Anki and the list is already there.

Every card can qualify for several reasons; it is placed by the most urgent one and shows
all of them as badges. The list is capped, and the cap trims from the least urgent end.
"""

from collections import namedtuple

from .scheduling import SECONDS_PER_DAY

# In priority order. The first reason a card has decides where it sits in the list.
REASON_PINNED = "pinned"
REASON_TROUBLE = "trouble"
REASON_MISSED = "missed"
REASON_TYPED = "typed"
REASON_DUE = "due"
REASON_LEECH = "leech"
REASON_NEW = "new"
REASON_REVIEWED = "reviewed"

REASON_ORDER = (REASON_PINNED, REASON_TROUBLE, REASON_MISSED, REASON_TYPED, REASON_DUE,
                REASON_LEECH, REASON_NEW, REASON_REVIEWED)

REASON_BADGES = {
    REASON_PINNED: "📌 Pinned",
    REASON_TROUBLE: "⚠ Trouble word",
    REASON_MISSED: "✕ Missed today",
    REASON_TYPED: "⌨ Typed wrong",
    REASON_DUE: "↻ Due",
    REASON_LEECH: "🩹 Keeps lapsing",
    REASON_NEW: "★ New",
    REASON_REVIEWED: "✓ Done today",
}

# The tag Claude's /pinned-words sets. The app also detects trouble words on its own (see
# below), so the list works the same on days that tag never changes.
TROUBLE_TAG = "use_trouble"

DEFAULT_LIMIT = 40
TROUBLE_WINDOW_DAYS = 14   # look-back for "keeps getting Again"
TROUBLE_AGAIN_COUNT = 2    # Again presses on reviews in that window = a trouble word
TYPED_WINDOW_DAYS = 7      # look-back for "typed it wrong in this app"
LEECH_LAPSES = 3

Recommendation = namedtuple("Recommendation", "cards reasons counts total")
# cards: ordered CardRows (already capped); reasons: {cardId: [reason, ...]} in priority
# order; counts: {reason: cards in the capped list carrying it}; total: size before the cap.


def buildRecommended(snapshot, deckIds, today, dayStartMs, newAllowances, typingStats=None,
                     includePinned=True, limit=DEFAULT_LIMIT):
    """Return a Recommendation for the deck selection. `limit` None means no cap."""
    from .persistence import notesMissedSince, notesWithMissDays

    typingStats = typingStats or {}
    byId = {}
    reasons = {}
    order = []

    def add(rows, reason):
        for row in rows:
            if row.cardId not in byId:
                byId[row.cardId] = row
                order.append(row.cardId)
            bucket = reasons.setdefault(row.cardId, [])
            if reason not in bucket:
                bucket.append(reason)

    if includePinned:
        add(snapshot.fetchCards(deckIds, {"pinned"}, today), REASON_PINNED)

    # Trouble: the tag, OR repeated Again presses, OR typed wrong on two different days.
    windowStartMs = dayStartMs - (TROUBLE_WINDOW_DAYS - 1) * SECONDS_PER_DAY * 1000
    againRecent = snapshot.againCounts(windowStartMs, reviewsOnly=True)
    troubleIds = [cid for cid, n in againRecent.items() if n >= TROUBLE_AGAIN_COUNT]
    add(snapshot.cardsWithTag(deckIds, TROUBLE_TAG), REASON_TROUBLE)
    add(snapshot.cardsByIds(deckIds, troubleIds), REASON_TROUBLE)
    typedTrouble = notesWithMissDays(typingStats, today - TROUBLE_WINDOW_DAYS + 1, 2)
    add(snapshot.cardsByNotes(deckIds, typedTrouble), REASON_TROUBLE)

    add(snapshot.cardsByIds(deckIds, snapshot.againCounts(dayStartMs).keys()), REASON_MISSED)
    add(snapshot.cardsByNotes(
        deckIds, notesMissedSince(typingStats, today - TYPED_WINDOW_DAYS + 1)), REASON_TYPED)
    add(snapshot.fetchCards(deckIds, {"due"}, today), REASON_DUE)
    add(snapshot.leechCards(deckIds, LEECH_LAPSES), REASON_LEECH)
    add(snapshot.fetchCards(deckIds, {"new"}, today, newAllowances), REASON_NEW)
    add(snapshot.fetchCards(deckIds, {"reviewed"}, today, dayStartMs=dayStartMs),
        REASON_REVIEWED)

    rank = {reason: index for index, reason in enumerate(REASON_ORDER)}
    for cardId in reasons:
        reasons[cardId].sort(key=rank.get)
    position = {cardId: index for index, cardId in enumerate(order)}
    ordered = sorted(order, key=lambda cid: (rank[reasons[cid][0]], position[cid]))
    total = len(ordered)
    if limit is not None:
        ordered = ordered[:limit]

    cards = [byId[cid] for cid in ordered]
    kept = {cid: reasons[cid] for cid in ordered}
    counts = {reason: 0 for reason in REASON_ORDER}
    for cardReasons in kept.values():
        for reason in cardReasons:
            counts[reason] += 1
    return Recommendation(cards, kept, counts, total)


def primaryReason(recommendation, cardId):
    """The reason that placed `cardId` in the list, or None."""
    cardReasons = recommendation.reasons.get(cardId) if recommendation else None
    return cardReasons[0] if cardReasons else None


def badgeText(reasonList):
    """Short badge line for a card, e.g. '📌 Pinned · ↻ Due'."""
    return " · ".join(REASON_BADGES[r] for r in reasonList or [])


def summaryText(recommendation):
    """One-line breakdown of the list for the header."""
    if recommendation is None:
        return ""
    bits = []
    for reason in REASON_ORDER:
        count = recommendation.counts.get(reason, 0)
        if count:
            bits.append("%s %d" % (REASON_BADGES[reason].split(" ", 1)[0], count))
    capped = ""
    if recommendation.total > len(recommendation.cards):
        capped = "  (top %d of %d)" % (len(recommendation.cards), recommendation.total)
    return "  ".join(bits) + capped
