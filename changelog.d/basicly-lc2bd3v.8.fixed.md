- **Drawing titles no longer costs the wall its bottom rows.** The parked strip's reserved
  height was measured for a one-line strip of bare ids; with titles it reaches two lines at
  1440x900, so the reserve is now the measured worst case and the title is bounded at 14ch. The
  events line takes the row's remaining width instead of its content's (basicly-lc2bd3v.8).
