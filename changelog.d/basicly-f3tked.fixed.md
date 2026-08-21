- **The board snapshot now carries the fields a wall board is read for: a loop phase and a
  readiness flag per unit, `ready`/`blocked` on `backlog`, the branch, head and dirty state of
  the checkout, the age of every pending ask and the question behind it, and the run's grant
  level, token budget and spend.** Measured on this repository before the fix, `phase` was null
  on 233 of 233 units, `ready` was 0 on 233 of 233, `lanes` and `session` were absent, an ask
  carried neither a question nor a waiting time, and `repo` was a name. Every panel a person
  reads was therefore empty or a count.

  **The fix is the caller, not the section.** `board_sections.units` was right to omit `phase`
  and `ready`: the first is `loop_state.derive_phase` reading a required-gate set the file-only
  producer does not open, and the second is the tracker's own walk over a status vocabulary and
  the whole edge population. A second spelling of either inside a display producer is how two
  derivations come to disagree, and the schema has no field marking a value as derived, so a
  guess would render identically to a read. So they join the lane facts and the lock facts on
  `board_snapshot.Facts`, and whatever a caller withholds stays **absent** - `basicly board`
  supplies them, and `tests/test_board_snapshot.py` pins both directions.

  `repo.branch`/`head`/`dirty` travel the same way for a different reason: `dirty` is
  `git status` and the producer spawns no subprocess, which a spy in
  `tests/test_board_snapshot.py` pins. `asks[].waiting_s` is the one value derived in the
  producer, and it is arithmetic on the injected `now` the document is already dated with
  rather than a clock reading - the shape `board_render`'s freshness age was already exempted
  for. `asks[].question` cannot be derived at all: `policy.record_wait_request` writes an id, a
  kind and the word `requested`, so the wording exists only on the decision queue and is paired
  back to its wait on the checkpoint name appearing in the question, which is
  `decisions.settle_checkpoint`'s own rule.

  **Two facts are bounded by cost, and the bound is published rather than hidden.**
  `loop_state.read_node_state` is the only route to `derive_phase` and it reads the whole event
  log seven times per record - 591 ms over 20 records on this repo's log - so a phase for all
  234 active records is 138 s against a 171 ms build. `basicly board` derived phases for the
  ranked ready front only, and every unit outside it kept `phase` absent; `basicly-s1vqq2`
  removed that cap. `session.spent_tokens` sits behind `policy.session_issue_ids` at 13.1 s and
  behind a run-record file this checkout may not have, so it is emitted only where both hold:
  the figure
  is spend *under the active grant*, never the lifetime one, because publishing the lifetime
  figure beside a ceiling is how a display comes to draw 177970761/4000000 with nothing spent
  under that grant (`basicly-f3tked`).
