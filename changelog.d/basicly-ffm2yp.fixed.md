- **The board's reclaimed ready list now fits the actual wall.** `basicly board --out` drew a
  fixed 14 rows even on a screen 26 fit, because the cap ignored the viewport. `--height` and
  `--width` let an operator state the wall's own size; the row count is measured from it and
  stays at the old safe default when neither is given.
