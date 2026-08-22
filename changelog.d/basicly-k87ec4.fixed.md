- **Planning guidance now says how to find the files a fix touches, not only how to declare
  them honestly.** A scope is a claim about where a wrong value is *produced*, and the
  tempting answer is where it is *displayed*. Those are usually different modules, and the
  gap tends to be discovered only after an agent has spent a budget reaching it.

  Four scopes were written wrong in a single session, all the same way: one named the loop
  and the gate runner when the producers were the three modules that actually run `git`; two
  named a renderer when the rows are built one call below it; one named two projected skill
  surfaces when skills project to three. Three of the four were caught downstream - one at
  the landing gate, which routed a correct and verified change to rework for touching eleven
  files outside its declared four, and one by the projection check refusing the commit. Only
  the two caught by probing before dispatch were cheap.

  `decompose-plan` gains **Locate the producer, never the surface**, carrying those four
  instances, the probe as a runnable command rather than an instruction to think harder, and
  the follow-up question that catches the projection case: does this value reach more than
  one surface? A projected artifact usually has several, and a renderer almost always sits
  one call above the builder that owns the fact.

  The existing honest-sizing section is untouched. The two answer different questions: that
  one is about not shrinking a scope you already know, this one is about naming a confidently
  wrong one.
