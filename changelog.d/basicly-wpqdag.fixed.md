- **The rebaseline count reports loosenings, not files, so accumulation on one file is
  visible.** `dropin.compose` keyed `rebaselined` by entry and kept the last fragment that
  declared it, so N fragments each loosening one file were reported as one. On this tree that
  was **41 declarations reported as 19**: `tests/test_loop.py` carried four - 311, 146, 13 and
  109 - reported as one, and `merge.py` three. The gate's summary line now reads
  `41 rebaselined across 19 entries`, and names the two apart only when they differ.

  Every entry always bound individually - a deliberately short delta fails the gate, which is
  the positive control - so nothing was ever wrongly admitted. What was wrong is the number an
  operator reads to judge how much debt a file has taken: **it was the count of files, and the
  whole point of counting is to watch debt accumulate on a file.** Per-entry counting was
  exactly blind to accumulation, which is the property `basicly.d/README.md` claimed it
  guaranteed. That claim now states what the count really prevents - each loosening is
  visible, one file taking several is not stopped - carries the 41-against-19 measurement, and
  says that a file appearing in several fragments is the signal to split it.

  The composed baseline is unchanged and asserted as the control: accumulating the *names* must
  not move the *arithmetic*. `Baseline.rebaselined` is now entry to every declaring fragment
  rather than to the last one, which is the only interface change.

  **This finding explains three earlier ones in the same pass.** `tests/test_handoff.py` had
  been rebaselined three times and `merge.py` three, and both were discovered by reading
  `basicly.d/` by hand while the gate reported one apiece - so the instrument that would have
  shown the accumulation was the one being fixed (basicly-wpqdag).
