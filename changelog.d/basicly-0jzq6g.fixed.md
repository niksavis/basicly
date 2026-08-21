- **The wall board no longer cuts a region off the screen when a producer emits one more of
  anything.** The layout stated six pixel row heights, each measured against the tallest content
  that row could hold *on the day it was written*. The fixture behind it carried 12 gate checks;
  this repository's own snapshot carries 36, so the gate strip grew four rows, pushed `HEALTH`
  past the bottom of the footer, and cut the caption under the loop strip to a partial line.
  Measured against the pre-change template, the repository's own snapshot clipped **five** of the
  eight regions - `head`, `loop`, `foot`, `tick` and `inv` - and four of them were already one
  line short on the 12-check fixture the layout shipped green against.

  Every row is now the height of what it holds, and what it holds is bounded by a capacity the
  model states. `GATES` draws a reserved grid of `GATE_COLUMNS` x `GATE_ROWS` cells whether or not
  the checks fill it, so the strips below it cannot move when the count above them changes, and it
  says `+N more checks` for the rest. `HEALTH` and the priority histogram gained the same
  treatment - one is a line per agent and the other is keyed by the producer's own label
  vocabulary, which the schema deliberately does not close, so neither had a length the page could
  assume. The gate strip's mode and stamp moved from two cells to the strip's caption, because a
  two-line cell among one-line ones takes a row the grid had allotted to a check. A lane card now
  puts its id and its phase on one line and its six figures on three, because the in-flight row is
  the one region that absorbs the wall's slack.

  **A pixel tuned against today's count is the same defect one number along**, so the row test
  asserts that no wall row states a length at all, and a new `dense-v1` fixture puts every capped
  population over its cap at once - 40 gate checks against the tree's 36, six agents, ten priority
  labels, seven lanes, all seven phases counted - and asserts each one names what it dropped. Both
  assertions fail on the pre-change template and model. What a test cannot show is that the result
  fits 1080px: that was measured by rendering headless at 1920x1080 and 1200x900 and reporting
  every element whose scroll size exceeded its client size, across five fixtures and the
  repository's own snapshot. Zero, against five clipped regions before.
