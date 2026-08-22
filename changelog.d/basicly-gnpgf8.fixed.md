- **The board's lane card no longer clips the model id, and the page no longer leaves most of
  its height black.** Both were one CSS decision. `board_page.html.j2` declared a grid whose
  right column was a fixed `470px` while the running row was `minmax(0, 1fr)`, so at 1920x2400
  roughly 70% of the page was black and `claude · claude-opu…` clipped at *every* width - a
  lane card was about 390px inside that column, and `_lane_cells` puts the agent and the model
  in one cell, so the model id had no room to have.

  The fixed column is gone and the page flows. A full-width identity line at 1280 is about
  590px of text in a 1248px box, so the model id reads with roughly two times headroom, and at
  1600 two cards per row still leave about 760px each.

  The card also gains the field with no substitute - the line saying what a lane is doing,
  which is the only thing that tells a working lane from a wedged one - and drops the rows it
  had nothing to put in. The fixed-height slot arithmetic retires with the fixed layout:
  `READY_SLOTS`, `READY_SLOTS_WIDE` and `BAND_ASKS` existed to promise a rendered height
  against a fixed viewport, which a page that flows does not need, and that is what lets the
  alarm band show more than one waiting ask.
