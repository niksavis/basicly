- **The validate decision queue kind has one spelling.** `loop._hold_for_validate_decision`
  passed the kind as a bare `"validate"` literal while `validate_gate.queue_unreadable_verdict`
  — the other site that queues one — named `validate_gate.VALIDATE_DECISION_KIND`. Both now
  name the symbol. No behaviour changes: the literal and the constant were the same string,
  which is exactly why nothing a call could observe would have caught them diverging.
  `decisions.enqueue` raises on a kind `decision_marker.KINDS` does not reserve, so a
  divergence would have failed the advance outright rather than mis-filing the item — the cost
  of a second spelling is that it is the one a later reader copies.
  `test_the_two_queue_sites_give_the_decision_kind_one_spelling` reads both function bodies
  and refuses one.
