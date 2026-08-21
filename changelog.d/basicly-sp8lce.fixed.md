- **`basicly board serve` serves the same document `basicly board --out` writes.** The server
  folded with the supervisor lock facts alone, so a served board carried a phase on **0 of 232**
  units against the emitted board's 232, no `ready` flag at all, and a `repo` section holding
  only a name — every region on a live wall read `not emitted by this producer`. `board_facts`
  sits above the server's tier and cannot be imported there, so the caller now passes a builder
  rather than the server reaching for one, and a test binds the two so a third producer cannot
  diverge in silence (`basicly-sp8lce`).
