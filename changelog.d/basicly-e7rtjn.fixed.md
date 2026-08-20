- **The prose-share instrument refuses source it cannot parse, instead of reporting it as
  0% prose.** `check_comment_density.prose_tokens` returned 0 on any fragment that did not
  `ast.parse`, and documented the 0 in its own docstring. That is the most dangerous answer
  the function had available, because the two size ratchets pull opposite ways: an extraction
  is safe only when the extracted unit is prose-**heavier** than the module it leaves, so a
  lane measuring a docstring section or a method lifted out of its class was told the exact
  opposite of the truth, every time. Measured 2026-08-20, a lane derived 66% by a second path
  sharing no step with the first, against this 0, and only then knew its first measurement was
  an instrument fault rather than a result. It now raises `RatchetError` naming the reason and
  the remedy - parse an extracted unit as the module it will become, not as a raw slice.

  The whole-tree path keeps the tolerance it had, and keeps it for the reason the old test
  gave: a tracked module with a syntax error is ruff's finding to report, and comment-density
  adding a second failure for one cause helps nobody. `verify --mode full` runs every check
  rather than stopping at the first, and ruff runs before this gate, so the tolerance is about
  the report and not about the gate's ability to run. The refusal is for the other caller, a
  lane measuring a fragment, which has no ruff run standing behind it. Both halves are pinned
  by a test that was shown to fail when its half is reverted.

  One test module had to move for this to land: `tests/test_check_comment_density.py` had
  reached the 4000-token cap, and the waiver the gate offers as a remedy would have failed the
  module's own `test_neither_the_gate_nor_this_test_carries_a_waiver`. The `basicly.d` fragment
  delta route is now `tests/test_check_comment_density_fragments.py` - five helpers and five
  tests, all of the subprocess half - and the boundary is the fragment route against
  measurement and ratchet decisions. The split carries the sibling's no-waiver assertion into
  the new file, which the original could no longer make on its behalf (basicly-e7rtjn).
