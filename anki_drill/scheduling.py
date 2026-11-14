"""
scheduling.py
Reimplements the small slice of Anki's day arithmetic and daily limits the drill app needs.

Anki stores review due dates as a day number counted from the collection's creation day, so
working out what "today" means requires the collection's creation stamp and the configured
rollover hour. New cards are additionally capped by a per-deck daily allowance.
"""

from datetime import date, datetime, timedelta

SECONDS_PER_DAY = 86400

# Anki queue values. Negative queues are suspended or buried and are never drilled.
QUEUE_NEW = 0
QUEUE_LEARN = 1
QUEUE_REVIEW = 2
QUEUE_DAY_LEARN = 3

# Queue selections the UI exposes, in display order. "pinned" is only offered when the
# Today's Review Words add-on is in play; see collection.pinningAvailable.
QUEUE_NAMES = ("due", "new", "reviewed", "pinned")

QUEUE_LABELS = {
    "due": "Due today",
    "new": "New today",
    "reviewed": "Reviewed today",
    "pinned": "Pinned",
}

# How the Today's Review Words add-on records a pin: the marker tag plus a per-study-day
# tag, both on the note. A pin only counts as live while both are present, which is how the
# add-on expires pins overnight.
PIN_TAG = "review_pinned"
PIN_DAY_PREFIX = "review_pinned_day_"

# revlog.ease is 0 for bookkeeping entries such as a manual "set due date"; only rows with a
# real button press count as having studied the card.
REVLOG_ANSWERED = "ease > 0"

# Anki's own fallback when a deck config carries no explicit new-card limit.
DEFAULT_NEW_PER_DAY = 20


def schedTimingToday(creationStamp, nowEpoch, rolloverHour=4):
    """Return (daysElapsed, nextDayAtEpoch) the way Anki's own scheduler computes them.

    `daysElapsed` is the day number that review cards are compared against; a review card is
    due when its `due` value is <= this number. The current day does not begin until the
    rollover hour has passed, which is why an early-morning session still counts as yesterday.
    """
    rolloverHour = max(0, min(23, int(rolloverHour)))
    createdDate = date.fromtimestamp(creationStamp)
    nowDateTime = datetime.fromtimestamp(nowEpoch)

    rolloverToday = nowDateTime.replace(
        hour=rolloverHour, minute=0, second=0, microsecond=0)
    rolloverPassed = nowDateTime >= rolloverToday
    nextDayAt = rolloverToday + timedelta(days=1) if rolloverPassed else rolloverToday

    wholeDays = (nowDateTime.date() - createdDate).days
    daysElapsed = wholeDays if rolloverPassed else wholeDays - 1
    return max(0, daysElapsed), int(nextDayAt.timestamp())


def pinDayTag(dayNumber):
    """Return the add-on's per-study-day pin tag, e.g. review_pinned_day_610."""
    return "%s%d" % (PIN_DAY_PREFIX, dayNumber)


def tagToken(tag):
    """Wrap a tag in the spaces Anki pads notes.tags with, so matching is exact.

    Substring matching is done with instr() rather than LIKE because these tags contain
    underscores, and LIKE would treat each one as a single-character wildcard.
    """
    return " %s " % tag


def queueClause(queues, today, dayStartMs=0):
    """Build the SQL WHERE fragment selecting `queues`, as (clause, params).

    `dayStartMs` is the epoch-milliseconds start of the current Anki day, needed only for the
    "reviewed" selection. Returns (None, []) when no queue is selected, so callers can skip
    the query entirely.
    """
    selected = [q for q in QUEUE_NAMES if q in queues]
    if not selected:
        return None, []

    parts = []
    params = []
    for name in selected:
        if name == "due":
            parts.append("(c.queue = ? AND c.due <= ?)")
            params += [QUEUE_REVIEW, today]
        elif name == "new":
            # The daily allowance is applied afterwards, per deck, by capNewCards.
            parts.append("(c.queue = ?)")
            params.append(QUEUE_NEW)
        elif name == "pinned":
            # Live pins carry both tags; yesterday's day marker is kept too, because the
            # add-on preserves it for one more day and those pins are still worth drilling.
            # Suspended and buried cards stay excluded, as they are for every other queue.
            parts.append(
                "(c.queue >= 0 AND c.nid IN (SELECT id FROM notes WHERE"
                " (instr(tags, ?) > 0 AND instr(tags, ?) > 0) OR instr(tags, ?) > 0))")
            params += [tagToken(PIN_TAG),
                       tagToken(pinDayTag(today)),
                       tagToken(pinDayTag(today - 1))]
        elif name == "reviewed":
            # Anything answered since the rollover, whatever queue it has landed in since.
            # This is what keeps today's work drillable after Anki has scheduled it away.
            parts.append(
                "(c.id IN (SELECT cid FROM revlog WHERE id >= ? AND %s))" % REVLOG_ANSWERED)
            params.append(dayStartMs)
    return " OR ".join(parts), params


def newAllowance(newPerDay, lastDayStudied, newStudied, today):
    """Return how many new cards a deck may still introduce today.

    Anki keeps a per-deck tally of new cards studied and the day it belongs to; once that day
    is behind us the tally no longer counts against today's limit.
    """
    alreadyToday = newStudied if lastDayStudied == today else 0
    return max(0, int(newPerDay) - int(alreadyToday))


def capNewCards(rows, allowances):
    """Trim new-card rows down to each deck's remaining daily allowance.

    Review rows pass through untouched. New rows keep the collection's own ordering, which is
    the order Anki would introduce them in.
    """
    if allowances is None:
        return list(rows)

    remaining = dict(allowances)
    kept = []
    for row in rows:
        if row.queue != QUEUE_NEW:
            kept.append(row)
            continue
        left = remaining.get(row.deckId, 0)
        if left > 0:
            remaining[row.deckId] = left - 1
            kept.append(row)
    return kept


def dayStartMilliseconds(nextDayAt):
    """Return the epoch-milliseconds start of the current Anki day."""
    return (int(nextDayAt) - SECONDS_PER_DAY) * 1000


def describeCard(row, today):
    """Return a short human label for a card's scheduling state, e.g. 'Due · 3 days late'."""
    if row.queue == QUEUE_NEW:
        return "New"
    if row.queue in (QUEUE_LEARN, QUEUE_DAY_LEARN):
        return "Learning"
    overdue = today - row.due
    if overdue > 0:
        return "Due · %d day%s late" % (overdue, "" if overdue == 1 else "s")
    if overdue < 0:
        # Already answered and rescheduled into the future — the "Reviewed today" case.
        return "Done · next due in %dd" % -overdue
    return "Due today · %dd interval" % row.ivl
