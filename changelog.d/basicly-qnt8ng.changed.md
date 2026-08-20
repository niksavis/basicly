- **The set of handoff artifact kinds is written down once.** `handoff.PRODUCERS` is now the
  only enumeration of it: the schema suite derives the kinds it exercises from that declaration
  instead of keeping a second hand-maintained tuple beside it, and the one declared kind with
  no schema authored for it is named against what the schema directory actually lacks. A ninth
  kind added to the declaration is exercised by construction, or named — it can no longer enter
  one list, miss the other, and read as a live contract because it appears in a list
  (`basicly-qnt8ng`).
