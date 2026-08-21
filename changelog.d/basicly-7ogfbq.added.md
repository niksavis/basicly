- **The board ranks by urgency instead of giving every region the same weight.** A green
  state now costs one token and only an exception expands: the whole gate set reads
  `GATES GREEN` in the status bar, or names the failing and unrun checks and only those, so
  the 36-name grid — and the check-name overlap it produced — is gone rather than resized.
  The alarm leads with the wait in the coarsest honest unit (`6 DAYS`, not `148h 52m`) with
  the id and kind beneath it; each loop phase carries a bar proportional to its share, so 213
  against 1 is visible; the running row draws one card per lane and collapses to a single line
  when none is dispatched, handing its width to the ready list, which then draws its top rows
  untruncated; the backlog and the event ticker are one line each; and the status bar carries
  units closed today where the producer records status events, absent rather than zero where
  it does not. Rendered from this repository's own snapshot at 1920x1080, 1680x1050, 1440x900
  and 1200x900 with both `check_render_overflow.py` signals reporting nothing (basicly-7ogfbq).
