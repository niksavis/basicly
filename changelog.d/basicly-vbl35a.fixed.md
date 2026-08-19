- **The per-event size cap now bounds by event kind, not by the spelling of a payload key.**
  The cap dispatched on a closed four-member list of key *names*, which was wrong in both
  directions. `value` was on the list and the fold reads `value`, so a tracker field the fold
  derives state from was being cut at 4096 bytes — one record's description is permanently
  truncated in the store of record. And any key the list did not name was not capped at all, so
  the bound a new event kind received was decided by the word its author happened to pick rather
  than by a decision. Two spellings of the same description therefore carried two different
  bounds: whole under `description` on a `created` event, cut under `value` on a `field` event.
  Now `FOLD_READ_KEYS` names every key the fold and its delegates read and the cap may never cut
  one; `KIND_TEXT_BYTES` declares the bound per kind; every other string is cut by default, so a
  new key is bounded without anyone remembering to name it; and **a kind that declares no bound
  is refused rather than stored unbounded**. This is the precondition for carrying a handoff
  artifact as a typed event (basicly-vbl35a).
