"""
session.py
The in-memory drill queue: ordering, navigation and the repeat-until-known round loop.

None of this touches Anki's scheduler. Grading a card only decides whether it comes back in
the next round of this session, which is what makes the app safe to run beside a live Anki.

Two lists are in play. `_queue` is the round — every card being played this time through, and
what the round mechanics (counts, next round, replay) operate on. `_view` is the subset the
user is currently navigating, narrowed by the status filter. With no filter the two match.
"""

import random

STATUS_UNSEEN = "unseen"
STATUS_LEARNING = "learning"
STATUS_KNOWN = "known"

# Status filter values. FILTER_ALL means "do not narrow the round at all".
FILTER_ALL = "all"
FILTER_VALUES = (FILTER_ALL, STATUS_LEARNING, STATUS_KNOWN)


class StudySession(object):
    """An ordered pass over a set of cards, repeated until every card is marked known."""

    def __init__(self, cards, shuffle=False, seed=None, groupOf=None):
        self._all = list(cards)
        # Optional card -> group key. With it, shuffling only mixes cards inside a group
        # and the groups keep their order (the recommended list's priority levels).
        self._groupOf = groupOf
        self._shuffle = bool(shuffle)
        self._rng = random.Random(seed)
        self._status = {}
        self._queue = []
        self._view = []
        self._filter = FILTER_ALL
        self._index = 0
        # One remembered cursor per filter, so switching Show modes can resume where the
        # user last was in that subset instead of always restarting from the top.
        self._anchors = {}
        self.roundNumber = 1
        self.knownTotal = 0
        self.reset()

    def _ordered(self, cards):
        """Return `cards` in play order, shuffled only when the user asked for it."""
        ordered = list(cards)
        if self._shuffle:
            self._shuffleInGroups(ordered)
        return ordered

    def _shuffleInGroups(self, cards):
        """Shuffle `cards` in place, within groups when a grouping is set."""
        if self._groupOf is None:
            self._rng.shuffle(cards)
            return
        groups = []
        index = {}
        for card in cards:
            key = self._groupOf(card)
            if key not in index:
                index[key] = len(groups)
                groups.append([])
            groups[index[key]].append(card)
        cards[:] = []
        for group in groups:
            self._rng.shuffle(group)
            cards.extend(group)

    def _rememberAnchor(self):
        """Remember the current card as this filter's last place, if one is visible."""
        current = self.current()
        if current is not None:
            self._anchors[self._filter] = current.cardId

    def _rebuildView(self, keepCard=None, anchorCardId=None):
        """Recompute the visible subset, parking the cursor by priority.

        Priority is: an explicit `keepCard`, then an explicit `anchorCardId`, then the
        current numeric index clamped into range. The anchor path is what makes switching
        between All / Still learning / Got it feel smooth instead of always restarting.
        """
        if self._filter == FILTER_ALL:
            self._view = list(self._queue)
        else:
            self._view = [c for c in self._queue
                          if self._status.get(c.cardId, STATUS_UNSEEN) == self._filter]

        if keepCard is not None:
            for position, card in enumerate(self._view):
                if card.cardId == keepCard.cardId:
                    self._index = position
                    self._rememberAnchor()
                    return
        if anchorCardId is not None:
            for position, card in enumerate(self._view):
                if card.cardId == anchorCardId:
                    self._index = position
                    self._rememberAnchor()
                    return
        self._index = max(0, min(self._index, len(self._view) - 1))
        self._rememberAnchor()

    def reset(self):
        """Restart the whole session from round one with every card unseen."""
        self._status = {c.cardId: STATUS_UNSEEN for c in self._all}
        self._queue = self._ordered(self._all)
        self._filter = FILTER_ALL
        self._index = 0
        self._anchors = {FILTER_ALL: self._all[0].cardId} if self._all else {}
        self.roundNumber = 1
        self.knownTotal = 0
        self._rebuildView()

    @property
    def isEmpty(self):
        """True when there were no cards to study at all."""
        return not self._queue

    @property
    def statusFilter(self):
        """The active status filter: FILTER_ALL, STATUS_LEARNING or STATUS_KNOWN."""
        return self._filter

    @property
    def isFiltered(self):
        """True while the round is narrowed to one grade."""
        return self._filter != FILTER_ALL

    @property
    def filterIsEmpty(self):
        """True when a filter is active and nothing in the round matches it."""
        return self.isFiltered and not self._view and bool(self._queue)

    @property
    def roundSize(self):
        """Number of cards in the round, ignoring any status filter."""
        return len(self._queue)

    @property
    def visibleCount(self):
        """Number of cards the filter currently lets through."""
        return len(self._view)

    @property
    def position(self):
        """1-based position of the current card within the visible subset (0 when empty)."""
        return self._index + 1 if self._view else 0

    @property
    def atLastCard(self):
        """True when the cursor sits on the final visible card."""
        return bool(self._view) and self._index >= len(self._view) - 1

    def current(self):
        """Return the CardRow under the cursor, or None when nothing is visible."""
        if not self._view:
            return None
        return self._view[self._index]

    def statusOf(self, card):
        """Return the round status of `card`: unseen, learning or known."""
        return self._status.get(card.cardId, STATUS_UNSEEN)

    def counts(self):
        """Return (known, learning, unseen) across the whole round, not the filtered view."""
        known = learning = unseen = 0
        for card in self._queue:
            status = self._status.get(card.cardId, STATUS_UNSEEN)
            if status == STATUS_KNOWN:
                known += 1
            elif status == STATUS_LEARNING:
                learning += 1
            else:
                unseen += 1
        return known, learning, unseen

    def setStatusFilter(self, status):
        """Switch the visible subset, resuming at that subset's last remembered place.

        The first time a filter is chosen there is no remembered place, so it starts at the
        top. After that, each filter remembers its own last visible card until grades reset.
        """
        if status not in FILTER_VALUES:
            status = FILTER_ALL
        if status == self._filter:
            return
        self._rememberAnchor()
        self._filter = status
        self._index = 0
        self._rebuildView(anchorCardId=self._anchors.get(status))

    def setShuffle(self, enabled):
        """Turn shuffling on or off, reordering only the cards still ahead of the cursor.

        Cards already visited keep their places so that toggling shuffle mid-round does not
        rewind the position counter or replay work the user has already done.
        """
        self._shuffle = bool(enabled)
        if not self._queue:
            return

        current = self.current()
        # Reordering happens on the round itself; the view is derived from it afterwards.
        pivot = 0
        if current is not None:
            pivot = next(i for i, c in enumerate(self._queue)
                         if c.cardId == current.cardId) + 1
        head = self._queue[:pivot]
        tail = self._queue[pivot:]
        if enabled:
            self._shuffleInGroups(tail)
        else:
            # Restore the collection's own due order for whatever is still to come.
            upcoming = {c.cardId for c in tail}
            tail = [c for c in self._all if c.cardId in upcoming]
        self._queue = head + tail
        self._rebuildView(keepCard=current)

    def mark(self, status):
        """Record `status` for the current card.

        Returns True if that card is still visible afterwards. It will not be when a filter
        is active and the new grade no longer matches it, in which case the next card has
        already taken its place under the cursor.
        """
        card = self.current()
        if card is None:
            return True
        self._status[card.cardId] = status
        if not self.isFiltered:
            self._rememberAnchor()
            return True
        self._rebuildView(anchorCardId=self._anchors.get(self._filter))
        return any(c.cardId == card.cardId for c in self._view)

    def goNext(self):
        """Move to the next visible card; return False when already on the last one."""
        if self._index < len(self._view) - 1:
            self._index += 1
            return True
        return False

    def goPrev(self):
        """Move to the previous visible card; return False when already on the first one."""
        if self._index > 0:
            self._index -= 1
            return True
        return False

    def unknownCards(self):
        """Cards in the finished round that were not marked known."""
        return [c for c in self._queue if self._status.get(c.cardId) != STATUS_KNOWN]

    def startNextRound(self):
        """Begin another round over the not-yet-known cards; False when nothing is left."""
        remaining = self.unknownCards()
        self.knownTotal += len(self._queue) - len(remaining)
        if not remaining:
            self._queue = []
            self._filter = FILTER_ALL
            self._index = 0
            self._rebuildView()
            return False
        self.roundNumber += 1
        for card in remaining:
            self._status[card.cardId] = STATUS_UNSEEN
        self._queue = self._ordered(remaining)
        # Statuses have just been cleared, so any grade filter would match nothing.
        self._filter = FILTER_ALL
        self._index = 0
        self._anchors = {FILTER_ALL: self._queue[0].cardId} if self._queue else {}
        self._rebuildView()
        return True

    def replayRound(self):
        """Replay the current round from the top, keeping every card in it."""
        if not self._all:
            return
        inRound = self._queue or self._all
        for card in inRound:
            self._status[card.cardId] = STATUS_UNSEEN
        self._queue = self._ordered(inRound)
        self._filter = FILTER_ALL
        self._index = 0
        self._anchors = {FILTER_ALL: self._queue[0].cardId} if self._queue else {}
        self._rebuildView()
